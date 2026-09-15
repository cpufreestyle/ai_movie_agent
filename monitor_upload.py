#!/usr/bin/env python
"""渲染完成后的自动收尾：人脸复检 -> 上传 B站。

后台运行：等 run_anime_series.py 在 outputs/_anime_driver.log 写入 "ALL DONE"
（或驱动进程退出），然后：
  1. 校验三集 epN_vo_anime_mmh3.mp4 均存在且非空；
  2. 跑 diag_face_rate.py 复检真实人脸检出率（目标 0%）；
  3. 任一集 >0% 则停止并告警（不上传）；
  4. 全 0% 则运行 _bili_upload_anime.py（上传动漫版+清理旧原始版）。
全程日志写入 outputs/_anime_monitor.log。
"""
import os
import re
import sys
import time
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
PY = sys.executable
LOG_PATH = os.path.join(ROOT, "outputs", "_anime_monitor.log")
EPS = (1, 2, 3)


def log(m):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        print(m, file=f, flush=True)


def read_pid():
    try:
        return int(open("outputs/_anime_driver.pid").read().strip())
    except Exception:
        return None


def pid_alive(pid):
    if not pid:
        return False
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True)
    return str(pid) in r.stdout


def driver_done():
    try:
        t = open("outputs/_anime_driver.log", encoding="utf-8", errors="ignore").read()
        return "ALL DONE" in t
    except Exception:
        return False


def parse_face_rate(stdout):
    """返回 {ep: (pct, avg_conf)}。输出形如：
    outputs/ep1_vo_anime_mmh3.mp4
      检出真实人脸帧 13/40  (32%)  平均置信 0.697
    """
    res = {}
    cur = None
    for line in stdout.splitlines():
        m = re.search(r"ep(\d)_vo_anime_mmh3\.mp4", line)
        if m:
            cur = int(m.group(1))
            res[cur] = (0, 0.0)
            continue
        if cur is not None:
            m2 = re.search(r"真实人脸帧\s+\d+/\d+\s*\((\d+)%\)\s*平均检测置信\s*([\d.]+)", line)
            if m2:
                res[cur] = (int(m2.group(1)), float(m2.group(2)))
                cur = None
    return res


def _wait_driver_done() -> bool:
    """等驱动打出 ALL DONE；超时或驱动异常退出返回 False。"""
    log("[monitor] 启动，等待驱动 ALL DONE ...")
    waited = 0
    while waited < 6 * 3600:
        if driver_done():
            log("[monitor] 检测到驱动 ALL DONE")
            return True
        if not pid_alive(read_pid()):
            log("[monitor] 驱动进程已退出")
            if driver_done():
                return True
            log("[monitor] 警告：驱动退出但未 ALL DONE（可能异常），停止")
            return False
        time.sleep(60)
        waited += 60
    log("[monitor] 超时未收到 ALL DONE，停止")
    return False


def _produced_videos():
    """校验三集动漫视频均就绪；缺失/过小返回 None。"""
    videos = {e: f"outputs/videos/ep{e}_vo_anime_mmh3.mp4" for e in EPS}
    missing = [e for e in EPS
               if not os.path.exists(videos[e]) or os.path.getsize(videos[e]) < 1_000_000]
    if missing:
        log(f"[monitor] 缺失/过小的动漫视频: ep{missing}，停止上传")
        return None
    log("[monitor] 三集动漫视频均就绪")
    return videos


def _face_rate_gate(videos: dict) -> bool:
    """人脸复检分档门禁：全 0% 返回 True（可上传），否则 False。"""
    log("[monitor] 运行 diag_face_rate 复检（目标 0%）")
    try:
        r = subprocess.run([PY, "diag_face_rate.py", *[videos[e] for e in EPS]],
                           capture_output=True, text=True, timeout=900)
    except Exception as e:
        log(f"[monitor] face-rate 异常: {e}")
        return False
    log(r.stdout)
    rates = parse_face_rate(r.stdout)
    log(f"[monitor] 检出率/置信: {rates}")
    # 分档门禁（ buffalo_l 默认阈值~0.5；真人脸置信通常 0.9+ ）
    gray_pct, gray_conf = 10, 0.85
    real = [e for e in EPS if rates.get(e, (0, 0))[0] > gray_pct
            or rates.get(e, (0, 0))[1] > gray_conf]
    gray = [e for e in EPS if 0 < rates.get(e, (0, 0))[0] <= gray_pct
            and rates.get(e, (0, 0))[1] <= gray_conf]
    if real:
        log(f"[monitor] 疑似真脸(ep{real})，暂停上传并告警")
        return False
    if gray:
        log(f"[monitor] 灰区：ep{gray} 仅低置信少量检出(动漫误报噪声，非真脸)，"
            f"暂停自动上传，等待人工确认")
        return False
    log("[monitor] 全 0%，开始上传")
    return True


def _run_upload() -> None:
    """调用上传脚本并转发 stdout / stderr。"""
    try:
        r2 = subprocess.run([PY, "_bili_upload_anime.py"],
                            capture_output=True, text=True, timeout=3600)
    except Exception as e:
        log(f"[monitor] 上传脚本异常: {e}")
        return
    log("[monitor] === upload stdout ===")
    log(r2.stdout)
    if r2.stderr:
        log("[monitor] === upload stderr (tail) ===")
        log(r2.stderr[-3000:])
    log("[monitor] 上传流程结束")


def main():
    if not _wait_driver_done():
        return
    videos = _produced_videos()
    if not videos:
        return
    if not _face_rate_gate(videos):
        return
    _run_upload()


if __name__ == "__main__":
    main()
