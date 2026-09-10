import argparse
import os
import subprocess
import sys
import time

import requests

# 路径解析统一在 comfy_paths（COMFYUI_ROOT 可覆盖；跨平台探测根目录与解释器）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from comfy_paths import comfyui_root as _find_root, python_exe as _python_exe  # noqa: E402


def _kill_existing() -> None:
    """只结束 ComfyUI 进程，避免误杀其它 python。"""
    if os.name == "nt":
        # 新版 Win11 已移除 wmic，改用 PowerShell CIM 按命令行匹配
        ps = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*ComfyUI*main.py*' } | "
              "Select-Object -ExpandProperty ProcessId")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True)
        pids = {ln.strip() for ln in out.stdout.splitlines() if ln.strip().isdigit()}
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        subprocess.run(["taskkill", "/F", "/IM", "ComfyUI.exe"], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "ComfyUI/main.py"], capture_output=True)


ap = argparse.ArgumentParser(
    description="重启 ComfyUI 后端（跨平台；目录自动探测，可用 --comfyui-root / COMFYUI_ROOT 指定）")
ap.add_argument("--comfyui-root", default="", metavar="DIR",
                help="ComfyUI 安装目录（含 main.py）；缺省自动探测 D:/ComfyUI、~/ComfyUI 等")
ap.add_argument("--port", type=int, default=8188, help="ComfyUI 监听端口，默认 8188")
ap.add_argument("--api", default="", help="就绪探测地址，默认 http://127.0.0.1:<port>")
ap.add_argument("--lowvram", action="store_true",
                help="加 --lowvram 启动：16GB 显存跑 22B(NVFP4)+12B 文本编码器时避免采样 OOM")
ap.add_argument("--novram", action="store_true",
                help="加 --novram 启动：模型常驻 CPU、按层上 GPU，最省显存（明显变慢）")
ap.add_argument("--reserve-vram", type=float, default=0.0, metavar="GB",
                help="给激活/中间结果预留的显存(GB)：22B NVFP4 权重 staged 约 17.8GB，"
                     "16GB 卡上需留 4~6GB 才不会在采样阶段 OOM")
ap.add_argument("--vram-headroom", type=float, default=0.0, metavar="GB",
                help="DynamicVRAM 始终保持完全空闲的显存(GB)")
ap.add_argument("--disable-smart-memory", action="store_true",
                help="加 --disable-smart-memory：强制把模型卸载回内存，不长期驻留显存")
ap.add_argument("--disable-pinned-memory", action="store_true",
                help="加 --disable-pinned-memory：内存紧张（大量 Pin error）时使用")
ap.add_argument("--fp8-text-enc", action="store_true",
                help="加 --fp8_e4m3fn-text-enc：文本编码器权重存 fp8，"
                     "把 12.5GB 的文本编码器压到 ~6GB（32GB 内存跑 novram 时必需）")
ap.add_argument("--cpu", action="store_true",
                help="加 --cpu 启动：纯 CPU 推理（不占显存）。权重与输入同在 CPU，"
                     "可绕开 GGUF patcher 不分块导致的显存撑满/device mismatch；代价是极慢")
ap.add_argument("--sage-attention", action="store_true",
                help="加 --use-sage-attention：用 SageAttention 替换默认注意力后端，"
                     "在支持的显卡上显著加速采样并省显存（需先 pip install sageattention）")
args = ap.parse_args()

# 0) 定位 ComfyUI 安装目录
root = _find_root(args.comfyui_root)
if not root:
    print("找不到 ComfyUI：请用 --comfyui-root 或环境变量 COMFYUI_ROOT "
          "指向含 main.py 的目录")
    raise SystemExit(2)
api = args.api or f"http://127.0.0.1:{args.port}"
print(f"ComfyUI 目录: {root}")

# 1) 仅结束 ComfyUI 相关进程（避免误杀其它 python）
_kill_existing()
time.sleep(3)

# 2) 重新拉起 ComfyUI 后端
# 禁用 torch.compile：22B 模型(LTX-2.5)在 16GB 卡上编译会卡死/极慢，关掉只损失少量速度、功能正常
os.environ["TORCH_COMPILE_DISABLE"] = "1"
cmd = [_python_exe(root), os.path.join(root, "main.py"),
       "--listen", "127.0.0.1", "--port", str(args.port),
       # Wan2.2 VAE 在 bf16 下解码输出纯灰色噪点，必须强制 fp32
       "--fp32-vae"]
if args.lowvram:
    cmd.append("--lowvram")
if args.novram:
    cmd.append("--novram")
if args.reserve_vram:
    cmd += ["--reserve-vram", str(args.reserve_vram)]
if args.vram_headroom:
    cmd += ["--vram-headroom", str(args.vram_headroom)]
if args.disable_smart_memory:
    cmd.append("--disable-smart-memory")
if args.disable_pinned_memory:
    cmd.append("--disable-pinned-memory")
if args.fp8_text_enc:
    cmd.append("--fp8_e4m3fn-text-enc")
if args.cpu:
    # 纯 CPU 推理时让 PyTorch 用满核心：默认线程数偏少会让 22B 每步耗时 2 小时(实测 CPU 仅 18% 负载)
    cores = str(os.cpu_count() or 8)
    os.environ["OMP_NUM_THREADS"] = cores
    os.environ["MKL_NUM_THREADS"] = cores
    cmd.append("--cpu")
if args.sage_attention:
    # SageAttention：支持的显卡上大幅加速采样并省显存；依赖 sageattention 包
    cmd.append("--use-sage-attention")

log_path = os.path.join(root, "comfy_run.log")
# 后台启动：Windows 用 DETACHED_PROCESS，类 Unix 用新会话，避免随父进程一起退出
_detach = {"creationflags": 0x00000008} if os.name == "nt" else {"start_new_session": True}
p = subprocess.Popen(cmd, cwd=root,
                     stdout=open(log_path, "w"), stderr=subprocess.STDOUT, **_detach)
print(f"launched backend pid {p.pid}（日志: {log_path}）")

# 3) 等待就绪
for _ in range(60):
    try:
        if requests.get(api + "/", timeout=5).status_code == 200:
            print("ComfyUI READY")
            break
    except Exception:
        pass
    time.sleep(2)
else:
    print("NOT READY within 120s")
