#!/usr/bin/env python3
"""ai_movie_agent · 服务自愈守护进程（watchdog）。

看护两条本地服务，进程死掉自动拉起，日志落绝对路径：
  - ComfyUI   (D:/ComfyUI, :8188)  —— 出图引擎，最常被沙箱回收
  - WebUI     (本项目,     :8000)  —— 浏览器操作入口，约 15h 后被回收
                                            → 下次打开就 `Failed to fetch`

可靠性分层（「终极」链）：
  登录自启(schtasks) → run_guard.bat 外层 respawn → 本 supervisor → 两条服务
  任一环节挂掉都能被上一层重新拉起。

本进程内的兜底：
  * 无 Popen 子进程但端口被**健康**实例占用 → 接管监控（不打断正在用的页面）。
  * 端口被**非健康**占用 → taskkill 回收再拉起。
  * 子进程存活但健康检查失败（假死）→ 过 60s 宽限期后重启。
  * 2 分钟内重启 >8 次 → 暂停 60s 防雪崩。
  * 自看门狗线程：主循环 >300s 无心跳（疑卡死）→ 自行 `os._exit(3)` 交由外层 guard 重启。
  * 日志按 5MB 轮转（保留一份 .1 备份），防长跑撑爆磁盘。
  * 所有探测强制不走代理（ProxyHandler({})）；子进程注入 NO_PROXY。

命令行：
  python supervise.py            # 前台常驻（Ctrl-C 退出，会清理自有子进程）
  python supervise.py --status   # 只探一次两服务状态并打印（不启动任何东西）
  python supervise.py --once     # 跑一轮 ensure（缺啥拉啥）后退出
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUTS = os.path.join(HERE, "outputs")
os.makedirs(OUTPUTS, exist_ok=True)

#: 服务定义。WebUI 用 sys.executable（即运行 supervise 的 python）启动自身。
SERVICES = [
    {
        "name": "ComfyUI",
        "cmd": [
            "D:/ComfyUI/venv/Scripts/python.exe", "D:/ComfyUI/main.py",
            "--listen", "127.0.0.1", "--port", "8188",
            "--fp32-vae", "--use-sage-attention",
        ],
        "cwd": "D:/ComfyUI",
        "port": 8188,
        "health": "http://127.0.0.1:8188/system_stats",
        "log": os.path.join(OUTPUTS, "_comfy.log"),
        "env": {"TORCH_COMPILE_DISABLE": "1", "PYTHONPATH": ""},
    },
    {
        "name": "WebUI",
        "cmd": [sys.executable, "cli.py", "webui", "--host", "127.0.0.1", "--port", "8000"],
        "cwd": HERE,
        "port": 8000,
        "health": "http://127.0.0.1:8000/api/anchor",
        "log": os.path.join(OUTPUTS, "_webui.log"),
        "env": {},
    },
]

HEALTH_TIMEOUT = 5
START_GRACE = 60          # 子进程启动宽限期（秒）：期内不做「存活但假死」判定
RESTART_WINDOW = 120      # 雪崩判定窗口（秒）
RESTART_LIMIT = 8         # 窗口内最多重启次数，超出则停 60s
POLL_INTERVAL = 10        # 主循环间隔（秒）
MAX_LOG_BYTES = 5 * 1024 * 1024   # 单条服务日志上限，超出轮转
WATCHDOG_STALL = 300      # 主循环无心跳超此秒数 → 自判卡死并退出（交外层重启）

_CHILDREN = []            # 我们真正 spawn 的子进程（退出时清理）
_HB = {"t": time.time()}  # 主循环心跳时间戳（看门狗线程读）


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)


def health_ok(url: str, timeout: int = HEALTH_TIMEOUT) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={"User-Agent": "supervise/1.0"})
        with opener.open(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def find_pid_on_port(port: int) -> int | None:
    """Windows: netstat 解析 LISTENING 的 PID；取不到返回 None。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
    except Exception:
        return None
    needle = f":{port}"
    for line in out.splitlines():
        if needle not in line or "LISTENING" not in line:
            continue
        try:
            return int(line.split()[-1])
        except ValueError:
            continue
    return None


def kill_pid(pid: int) -> None:
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        log(f"  已强制结束 pid={pid}")
    except Exception as e:
        log(f"  taskkill pid={pid} 失败：{e}")


def _rotate_if_big(path: str) -> None:
    """日志超过 MAX_LOG_BYTES 就轮转成 .1（只留一份，防长跑撑爆磁盘）。"""
    try:
        if os.path.isfile(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            bak = path + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(path, bak)
            log(f"  日志超 {MAX_LOG_BYTES // (1024 * 1024)}MB 已轮转 -> {os.path.basename(bak)}")
    except Exception as e:  # noqa: BLE001
        log(f"  日志轮转失败：{e}")


def launch_now(svc: dict) -> None:
    _rotate_if_big(svc["log"])
    env = dict(os.environ)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    env.update(svc.get("env", {}))
    logf = open(svc["log"], "a", buffering=1, encoding="utf-8")
    p = subprocess.Popen(svc["cmd"], cwd=svc["cwd"], env=env,
                         stdout=logf, stderr=subprocess.STDOUT)
    svc["proc"] = p
    svc["pid"] = p.pid
    svc["adopted"] = False
    svc["started_at"] = time.time()
    _CHILDREN.append(p)
    log(f"[{svc['name']}] 已启动 pid={p.pid} -> {svc['log']}")


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def ensure_running(svc: dict) -> None:
    proc = svc.get("proc")

    # 情况 A：我们拥有子进程
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            # 存活。超过宽限期且健康检查失败 → 假死，重启
            if time.time() - svc.get("started_at", 0) > START_GRACE and not health_ok(svc["health"]):
                log(f"[{svc['name']}] 进程存活但健康检查失败，重启")
                _terminate(proc)
                svc["proc"] = None
                svc["adopted"] = False
            else:
                return
        else:
            log(f"[{svc['name']}] 进程退出 rc={rc}")
            svc["proc"] = None
            svc["adopted"] = False

    # 情况 B：当前没有我们 spawn 的进程
    if health_ok(svc["health"]):
        pid = find_pid_on_port(svc["port"])
        svc["pid"] = pid
        svc["adopted"] = True
        svc["proc"] = None
        log(f"[{svc['name']}] 端口 {svc['port']} 已有健康实例"
            + (f"（pid={pid}）已接管监控" if pid else ""))
        return

    # 情况 C：需要（重）启动
    if svc.get("adopted") and svc.get("pid"):
        kill_pid(svc["pid"])
        svc["pid"] = None
        svc["adopted"] = False
    occupant = find_pid_on_port(svc["port"])
    if occupant and occupant != svc.get("pid"):
        log(f"[{svc['name']}] 端口 {svc['port']} 被 pid={occupant} 占用且非健康，强制回收")
        kill_pid(occupant)
        time.sleep(1)

    # 雪崩保护
    now = time.time()
    svc.setdefault("restarts", [])
    svc["restarts"] = [t for t in svc["restarts"] if now - t < RESTART_WINDOW]
    if len(svc["restarts"]) >= RESTART_LIMIT:
        log(f"[{svc['name']}] {RESTART_WINDOW}s 内重启超 {RESTART_LIMIT} 次，暂停 60s 防雪崩")
        time.sleep(60)
        svc["restarts"] = []
    svc["restarts"].append(now)
    launch_now(svc)


def _shutdown(*_args) -> None:
    log("收到退出信号，清理自有子进程 ...")
    for p in _CHILDREN:
        if p.poll() is None:
            _terminate(p)
    sys.exit(0)


def _watchdog() -> None:
    """自看门狗：主循环长时间无心跳即视为卡死，退出交外层 guard 重启。"""
    while True:
        time.sleep(15)
        if time.time() - _HB["t"] > WATCHDOG_STALL:
            log(f"主循环 >{WATCHDOG_STALL}s 无心跳，判定卡死，退出交外层 guard 重启")
            os._exit(3)


def cmd_status() -> int:
    """只探一次两服务状态，打印表格并返回码（0=全 up）。"""
    print("=== ai_movie_agent 服务状态 ===")
    all_up = True
    for svc in SERVICES:
        up = health_ok(svc["health"])
        pid = find_pid_on_port(svc["port"])
        all_up = all_up and up
        print(f"  {svc['name']:<8} :{svc['port']:<5} {'UP' if up else 'DOWN':<4} pid={pid}")
    webui = SERVICES[-1]["port"]
    print(f"  → WebUI: http://127.0.0.1:{webui}/")
    return 0 if all_up else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="ai_movie_agent 服务守护")
    ap.add_argument("--status", action="store_true", help="只探一次状态并退出（不启动任何东西）")
    ap.add_argument("--once", action="store_true", help="跑一轮 ensure（缺啥拉啥）后退出")
    args = ap.parse_args()

    for svc in SERVICES:
        svc.setdefault("proc", None)
        svc.setdefault("pid", None)
        svc.setdefault("adopted", False)
        svc.setdefault("started_at", 0.0)
        svc.setdefault("restarts", [])

    if args.status:
        sys.exit(cmd_status())

    if args.once:
        for svc in SERVICES:
            ensure_running(svc)
        return

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    threading.Thread(target=_watchdog, daemon=True).start()

    log("=== ai_movie_agent 服务守护启动 ===")
    names = ", ".join(f"{s['name']}(:{s['port']})" for s in SERVICES)
    log(f"看护：{names}")
    last_beat = 0.0
    while True:
        _HB["t"] = time.time()
        for svc in SERVICES:
            try:
                ensure_running(svc)
            except Exception as e:  # noqa: BLE001 - 单服务异常不应拖垮守护
                log(f"[{svc['name']}] ensure_running 异常：{e}")
        now = time.time()
        if now - last_beat > 60:
            last_beat = now
            status = " / ".join(
                f"{s['name']}="
                + ("up" if (s.get("proc") and s["proc"].poll() is None) or health_ok(s["health"]) else "DOWN")
                for s in SERVICES
            )
            log(f"心跳：{status}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
