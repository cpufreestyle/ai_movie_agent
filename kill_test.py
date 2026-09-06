# 临时：终止卡住的最小测试进程（run_ltx_gguf_test.py 及其 cli.py 子进程），保留 ComfyUI(main.py)
import os
import re
import subprocess

out = subprocess.run(
    ["powershell", "-c",
     "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' } | "
     "Select-Object ProcessId,CommandLine"],
    capture_output=True, text=True)
killed = []
for line in out.stdout.splitlines():
    m = re.match(r"^\s*(\d+)\s+(.*)$", line)
    if not m:
        continue
    pid, cmd = m.group(1), m.group(2)
    if "main.py" in cmd:          # 保留 ComfyUI
        continue
    if "cli.py" in cmd or "run_ltx_gguf_test" in cmd:
        try:
            os.kill(int(pid), 9)
            killed.append(pid)
        except Exception as e:
            print("ERR", pid, e)
print("killed:", killed)
