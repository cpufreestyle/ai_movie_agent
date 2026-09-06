"""一次性启动 ComfyUI(8188) 并跑 LTX-2.5 高分辨率稳定性测试。

把"启动服务 + 等待就绪 + 跑测试"放进同一进程，避免后台进程被沙箱回收。
"""
import subprocess, time, sys, os, requests

PROJ = os.path.dirname(os.path.abspath(__file__))
# 本项目 .venv 是 Git-Bash 的 msys/posix Python，只认 POSIX 路径（/mnt/d/...）
COMFY = "/mnt/d/ComfyUI"
COMFY_PY = "/mnt/d/ComfyUI/venv/Scripts/python.exe"
API = "http://127.0.0.1:8188/"

log = open(os.path.join(PROJ, "outputs", "comfy_start.log"), "w")
p = subprocess.Popen([COMFY_PY, "main.py", "--port", "8188", "--listen", "127.0.0.1"],
                     cwd=COMFY, stdout=log, stderr=subprocess.STDOUT)
print("ComfyUI launched pid", p.pid, flush=True)

s = requests.Session(); s.trust_env = False
ready = False
for i in range(90):
    if p.poll() is not None:
        print("ComfyUI process exited early (code %s)" % p.returncode, flush=True)
        break
    try:
        if s.get(API, timeout=5).status_code == 200:
            ready = True
            print("ComfyUI ready after ~%ds" % ((i + 1) * 5), flush=True)
            break
    except Exception:
        pass
    time.sleep(5)

if not ready:
    print("ComfyUI NOT ready; tail of start log:", flush=True)
    print(open(os.path.join(PROJ, "outputs", "comfy_start.log")).read()[-2500:], flush=True)
    sys.exit(1)

print("=== run LTX-2.5 stability test (768x448, 49 frames) ===", flush=True)
r = subprocess.run([
    os.path.join(PROJ, ".venv", "bin", "python"),
    os.path.join(PROJ, "run_ltx25_multishot.py"),
    "--shot", "1", "--width", "768", "--height", "448", "--frames", "49"],
    cwd=PROJ)
print("TEST_RETURN", r.returncode, flush=True)
sys.exit(r.returncode)
