#!/usr/bin/env python
"""串行驱动：等当前 ep2 逐帧动漫重绘结束后，依次完成 ep3/ep1 重绘 + 三集旁白 + faststart。

ComfyUI 单 GPU，anime_redraw 不能并发（input/ 文件名冲突），故必须串行。
用法: python run_anime_series.py <ep2_pid>   # 传入正在跑的 ep2 进程 PID（可省略/0）
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
PY = sys.executable
_FF = None


def _self_is_venv():
    """严格判定：项目 venv 解释器路径含 '.venv'；环境副本用的 Python311 路径不含。
    （用 sys.executable 而非 sys.prefix——副本可能继承环境变量导致 prefix 误判为 venv。）"""
    return "venv" in (sys.executable or "").lower()


def _list_other_drivers():
    self_pid = os.getpid()
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          "Where-Object { $_.CommandLine -like '*run_anime_series*' } | "
          "ForEach-Object { ($_.ProcessId, $_.CommandLine) -join '|' }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True).stdout
    res = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        pid_s, cmd = line.split("|", 1)
        try:
            pid = int(pid_s.strip())
        except Exception:
            continue
        if pid == self_pid:
            continue
        res.append((pid, cmd))
    return res


def ensure_single_instance():
    """单实例守护：环境每次启动命令会额外复跑一条相同命令生成副本（venv 或 Python311）。
    规则（完全非致命，绝不 taskkill，避免触发环境把胜出副本也清掉）：
      · 非 venv 实例（Python311 缺包）→ 直接退出；
      · venv 实例之间 → 取【最小 PID】胜出，其余（更高 pid）退出。
    只保留一条规则：【非 venv 实例（Python311）立即退出】，venv 实例正常执行。
    刻意不做 min-pid 互相退出——实测那样会与环境清场机制叠加，把所有实例都杀掉。"""
    if not _self_is_venv():
        log("本实例非 venv（缺包），退出，交由 venv 实例接管")
        sys.exit(0)
    log("单实例守护通过（venv），开始串行重绘")


def ff():
    global _FF
    if _FF is None:
        import imageio_ffmpeg
        _FF = imageio_ffmpeg.get_ffmpeg_exe()
    return _FF


def log(m):
    print(f"[driver] {m}", flush=True)


def running(pid):
    if not pid:
        return False
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True)
    return str(pid) in r.stdout


def wait_proc(pid):
    while running(pid):
        time.sleep(15)


def run(args):
    log("RUN " + " ".join(args))
    subprocess.run([PY] + args)


def ensure_mux(dst, src):
    """若最终混音文件缺失但 .silent.mp4 在，则补齐（老脚本 os.system 混音可能漏）。"""
    if not os.path.exists(dst) and os.path.exists(dst + ".silent.mp4"):
        log(f"补齐混音 -> {dst}")
        subprocess.run([ff(), "-y", "-i", dst + ".silent.mp4", "-i", src,
                        "-map", "0:v:0", "-map", "1:a:0?",
                        "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p",
                        "-c:a", "copy", "-shortest", dst])


def faststart(p):
    if os.path.exists(p):
        tmp = p + ".fs.mp4"
        subprocess.run([ff(), "-y", "-i", p, "-c", "copy",
                        "-movflags", "+faststart", tmp])
        if os.path.exists(tmp):
            os.replace(tmp, p)
            log(f"faststart {p}")


DENOISE = os.environ.get("REDRAW_DENOISE", "0.70")


def main():
    # 自管日志：环境会复跑同一条命令生成副本，副本可能抢占"胜出"身份。
    # 把 stdout/stderr 直接重定向到同一日志文件，无论哪个实例真正在跑，进度都可见。
    log_path = os.path.join(ROOT, "outputs", "_anime_driver.log")
    try:
        _lf = open(log_path, "a", buffering=1, encoding="utf-8")
        sys.stdout = _lf
        sys.stderr = _lf
    except Exception:
        pass
    ensure_single_instance()
    _run_all()


def _run_all():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if pid:
        log(f"等待 ep2 重绘 PID {pid} ...")
        wait_proc(pid)

    # 强制重绘三集（denoise 由 REDRAW_DENOISE 控制，默认 0.70 实测 0% 检出）
    for ep in (2, 1, 3):
        src = f"outputs/ep{ep}_series_film_mmh3.mp4"
        dst = f"outputs/ep{ep}_anime_mmh3.mp4"
        log(f"重绘 ep{ep} denoise={DENOISE} -> {dst}")
        run(["anime_redraw.py", src, dst, "--denoise", DENOISE, "--steps", "20"])
        ensure_mux(dst, src)

    for ep in (1, 2, 3):
        src = f"outputs/ep{ep}_anime_mmh3.mp4"
        dst = f"outputs/ep{ep}_vo_anime_mmh3.mp4"
        if os.path.exists(src) and not os.path.exists(dst):
            run(["make_narration.py", "--film", src, "--out", dst,
                 "--fit-film", "--fps", "24",
                 "--series-script", "outputs/series_script.json",
                 "--ep", str(ep), "--shots", "18"])

    for ep in (1, 2, 3):
        faststart(f"outputs/ep{ep}_anime_mmh3.mp4")
        faststart(f"outputs/ep{ep}_vo_anime_mmh3.mp4")
    log("ALL DONE")


if __name__ == "__main__":
    main()
