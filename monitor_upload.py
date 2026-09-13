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


def main():
    log("[monitor] 启动，等待驱动 ALL DONE ...")
    waited = 0
    done = False
    while waited < 6 * 3600:
        if driver_done():
            log("[monitor] 检测到驱动 ALL DONE")
            done = True
            break
        if not pid_alive(read_pid()):
            log("[monitor] 驱动进程已退出")
            if driver_done():
                done = True
                break
            log("[monitor] 警告：驱动退出但未 ALL DONE（可能异常），停止")
            return
        time.sleep(60)
        waited += 60
    if not done:
        log("[monitor] 超时未收到 ALL DONE，停止")
        return

    # 校验产出
    videos = {e: f"outputs/videos/ep{e}_vo_anime_mmh3.mp4" for e in EPS}
    missing = [e for e in EPS
               if not os.path.exists(videos[e]) or os.path.getsize(videos[e]) < 1_000_000]
    if missing:
        log(f"[monitor] 缺失/过小的动漫视频: ep{missing}，停止上传")
        return
    log("[monitor] 三集动漫视频均就绪")

    # 人脸复检
    log("[monitor] 运行 diag_face_rate 复检（目标 0%）")
    try:
        r = subprocess.run([PY, "diag_face_rate.py", *[videos[e] for e in EPS]],
                           capture_output=True, text=True, timeout=900)
    except Exception as e:
        log(f"[monitor] face-rate 异常: {e}")
        return
    log(r.stdout)
    rates = parse_face_rate(r.stdout)
    log(f"[monitor] 检出率/置信: {rates}")
    # 分档门禁（ buffalo_l 默认阈值~0.5；真人脸置信通常 0.9+ ）
    GRAY_PCT, GRAY_CONF = 10, 0.85
    real = [e for e in EPS if rates.get(e, (0, 0))[0] > GRAY_PCT
            or rates.get(e, (0, 0))[1] > GRAY_CONF]
    gray = [e for e in EPS if 0 < rates.get(e, (0, 0))[0] <= GRAY_PCT
            and rates.get(e, (0, 0))[1] <= GRAY_CONF]
    if real:
        log(f"[monitor] 疑似真脸(ep{real})，暂停上传并告警")
        return
    if gray:
        log(f"[monitor] 灰区：ep{gray} 仅低置信少量检出(动漫误报噪声，非真脸)，"
            f"暂停自动上传，等待人工确认")
        return
    log("[monitor] 全 0%，开始上传")

    # 上传
    try:
        r2 = subprocess.run([PY, "_bili_upload_anime.py"],
                            capture_output=True, text=True, timeout=3600)
        log("[monitor] === upload stdout ===")
        log(r2.stdout)
        if r2.stderr:
            log("[monitor] === upload stderr (tail) ===")
            log(r2.stderr[-3000:])
    except Exception as e:
        log(f"[monitor] 上传脚本异常: {e}")
        return
    log("[monitor] 上传流程结束")


if __name__ == "__main__":
    main()
