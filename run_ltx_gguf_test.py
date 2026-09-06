"""等 LTX-2.5 GGUF 权重下载完成后，自动跑一次最小参数的生成测试。

用途：16GB 显存跑 LTX-2.5 的唯一路径是 GGUF 量化（官方最小组合 34GB+ 放不下）。
权重约 11.5GB，下载耗时长，本脚本挂在后台：先轮询等下载完成，再自动提交一次
最小片段（25 帧）的生成任务，便于事后从日志判断是否出片/是否 OOM。

用法（后台）：
    Start-Process python -ArgumentList run_ltx_gguf_test.py -RedirectStandardOutput ...
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GGUF = r"D:\ComfyUI\models\diffusion_models\LTX-2.5-Distilled-Q2_K.gguf"
TOTAL = 8834977792  # LTX-2.5-Distilled-Q2_K.gguf 的字节数

PROMPT = "a cat walking on the grass, cinematic lighting"
OUT = os.path.join("outputs", "ltx_gguf_test.mp4")
FRAMES = "9"  # 最小可用帧数（=1+floor(fps*duration/8)*8），先把激活显存压到最低验证出片


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def wait_download():
    last = -1
    while True:
        size = os.path.getsize(GGUF) if os.path.exists(GGUF) else 0
        if size != last:
            log(f"[wait] {size / 1e9:.2f} / {TOTAL / 1e9:.2f} GB")
            last = size
        if size >= TOTAL:
            log("[wait] GGUF 下载完成")
            return True
        time.sleep(30)


if __name__ == "__main__":
    wait_download()
    log(f"[run ] 提交最小生成测试：{FRAMES} 帧 -> {OUT}")
    r = subprocess.run(
        [sys.executable, "cli.py", "ltx", "--prompt", PROMPT,
         "--frames", FRAMES, "--out", OUT],
        cwd=HERE)
    log(f"[run ] 退出码 = {r.returncode}")
