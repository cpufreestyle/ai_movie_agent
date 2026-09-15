#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 电影 Agent · 配置驱动的一键部署（跨平台 Windows / Linux / macOS）。

读 config.yaml + 探测本机环境（OS / Docker / GPU），按「配置 × 环境」自动选部署方案：
  - 容器（docker compose）还是原生（venv + 系统 Python）
  - NVIDIA(CUDA) / AMD(ROCm) / 无显卡（远程 ComfyUI）
  - 视频引擎 MiniMax H3 还是 LTX-2.5（决定权重与精度）
  - Blender 白模是否启用（决定要不要提示装 Blender + MCP 插件）

默认只打印方案（plan）；--apply 执行安全部分：建 venv、装依赖、生成 .env、
docker compose up（原生则只装依赖，启动命令另行打印）。
视频权重大下载需显式 --with-weights（用户授权）才执行；Blender 不自动安装。

用法示例：
  python deploy.py                  # 只打印按当前配置算出的方案
  python deploy.py --apply          # 执行安全部分（建 venv / 装依赖 / 写 .env / compose up）
  python deploy.py --apply --with-weights --models-dir D:/ComfyUI/models   # 授权下载视频权重
  python deploy.py --method native --gpu amd --engine comfyui_ltx          # 手动覆盖探测结果
  python deploy.py --gpu amd --tier amd395-128g    # AMD Ryzen AI Max+ 395（128GB 统一内存）
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from urllib.parse import urlparse

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)


# ---------------------------------------------------------------- 硬件档位
def _config_env():
    """惰性导入 config_env（纯标准库，venv 前也能用）；失败返回 None。"""
    try:
        import config_env
        return config_env
    except Exception:
        return None


def resolve_tier_arg(value: str) -> str:
    """把 --tier 值归一为规范档位（支持 amd395 / 395 / strix-halo 等别名）；未知则报错退出。"""
    if not value:
        return ""
    ce = _config_env()
    tier = ce.normalize_tier(value) if ce else value.strip().lower()
    if not tier:
        tiers = " / ".join(ce.HW_TIER_PROFILES) if ce else "high / mid / low / cpu / amd395-128g"
        print(f"[错误] 未知档位：{value}（可选：{tiers}）")
        sys.exit(2)
    return tier


def detect_hw_tier() -> str:
    """自动检测本机硬件档位（跨平台，纯标准库）；失败返回空串。"""
    ce = _config_env()
    if ce is None:
        return ""
    try:
        return ce.pick_tier(ce.detect_hardware())
    except Exception:
        return ""


# ---------------------------------------------------------------- 环境探测
def detect_os() -> str:
    s = platform.system().lower()
    if s == "windows":
        return "windows"
    if s == "darwin":
        return "macos"
    return "linux"


def _run(cmd, timeout: int = 20):
    """跑一条命令，返回 CompletedProcess；任何异常返回 None（不抛）。"""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def detect_docker() -> bool:
    if shutil.which("docker") is None:
        return False
    r = _run(["docker", "compose", "version"])
    if r is not None and r.returncode == 0:
        return True
    r = _run(["docker-compose", "version"])  # 旧版独立二进制
    return bool(r is not None and r.returncode == 0)


def detect_gpu() -> str:
    """返回 nvidia / amd / none。"""
    r = _run(["nvidia-smi", "-L"]) if shutil.which("nvidia-smi") else None
    if r is not None and r.returncode == 0:
        return "nvidia"
    r = _run(["rocminfo"]) if shutil.which("rocminfo") else None
    if (r is not None and r.returncode == 0) or os.path.exists("/dev/kfd"):
        return "amd"
    return "none"


def probe_comfyui(api: str, timeout: int = 3) -> bool:
    """尽力探测 ComfyUI 是否在线（本地走 trust_env=False 绕过系统代理）。"""
    try:
        import requests
        s = requests.Session()
        s.trust_env = False
        return s.get(api.rstrip("/") + "/", timeout=timeout).status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------- 配置读取
def _coerce(v: str):
    v = v.strip().strip('"').strip("'")
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    try:
        return int(v)
    except ValueError:
        return v


def _mini_yaml(text: str) -> dict:
    """极简 YAML 解析（仅够取本项目 config.yaml 的标量叶子 + 2 空格缩进嵌套映射）。

    不依赖 PyYAML，保证 deploy 在还没有 venv 的系统 Python 上也能读配置。
    """
    root: dict = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()
        if ":" not in content:
            continue  # 列表项 / 不支持
        key, _, val = content.partition(":")
        key, val = key.strip(), val.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            node: dict = {}
            parent[key] = node
            stack.append((indent, node))
        else:
            parent[key] = _coerce(val)
    return root


def load_config(path: str) -> dict:
    try:
        import yaml  # 系统 Python 不一定有，有则用官方解析更稳
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        with open(path, encoding="utf-8") as f:
            return _mini_yaml(f.read())


def cfg_get(cfg: dict, *keys, default=None):
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


# ---------------------------------------------------------------- 方案求解
def _cfg_tier(cfg: dict) -> str:
    """读 config.hw_tier 并归一；无则空串。"""
    raw = cfg_get(cfg, "hw_tier", default="")
    if not raw:
        return ""
    ce = _config_env()
    return ce.normalize_tier(raw) if ce else str(raw).strip().lower()


def resolve_tier_choice(args, cfg: dict, gpu: str) -> tuple:
    """决定本次部署用的硬件档位，返回 (tier, 来源)。优先级：--tier > config.hw_tier > 自动识别。

    只在自动识别命中 AMD Ryzen AI Max+ 395（amd395-128g）时才自动采用——那台机器上
    iGPU 显存会被低估成 cpu 档，必须显式纠正；其余档位不自动写入，避免改变既有行为。
    """
    if args.tier:
        return resolve_tier_arg(args.tier), "手动 --tier"
    t = _cfg_tier(cfg)
    if t:
        return t, "config.hw_tier"
    if gpu == "amd":
        ce = _config_env()
        auto = detect_hw_tier()
        if auto and ce is not None and auto == ce.AMD395_TIER:
            return auto, "自动识别 AMD 395 128G"
    return "", ""


def resolve_scheme(cfg: dict, args) -> dict:
    env_gpu = detect_gpu()
    gpu = args.gpu if args.gpu != "auto" else env_gpu
    # gpu=none 时仍当作 nvidia 走占位（权重在远程机下）；但方案里标注"远程"
    engine = args.engine or cfg_get(cfg, "engine", "backend", default="comfyui_mmH3")
    blender = bool(cfg_get(cfg, "blender", "enabled", default=False))
    tier, tier_source = resolve_tier_choice(args, cfg, gpu)

    api = cfg_get(cfg, "engine", engine, "api",
                  default=cfg_get(cfg, "engine", "comfyui_mmH3", "api",
                                  default="http://127.0.0.1:8188"))
    host = urlparse(api).hostname or ""
    comfyui_local = host in ("127.0.0.1", "localhost", "")

    docker = detect_docker()
    method = args.method if args.method != "auto" else ("docker" if docker else "native")

    # Ollama 地址：容器里用服务名，原生用本机
    ollama_url = "http://ollama:11434" if method == "docker" else "http://localhost:11434"
    llm_model = cfg_get(cfg, "llm", "model", default="qwen2.5:3b")

    env_vars = {
        "OLLAMA_URL": ollama_url,
        "LLM_MODEL": llm_model,
        "COMFYUI_API": api,
        "ENGINE_BACKEND": engine,
        "GPU_BACKEND": gpu if gpu != "none" else "nvidia",
        "HW_TIER": tier,          # 硬件档位；amd395-128g = AMD Ryzen AI Max+ 395（128G UMA）
        "COMFYUI_IMAGE": "your-registry/comfyui-ltx-mmh3:latest",
        "COMFYUI_IMAGE_ROCM": "your-registry/comfyui-rocm:latest",
        "SOL_H3_API": api if engine == "sol_h3" else "",
    }
    return {
        "os": detect_os(),
        "docker": docker,
        "env_gpu": env_gpu,
        "gpu": gpu,
        "tier": tier,
        "tier_source": tier_source,
        "engine": engine,
        "blender": blender,
        "api": api,
        "comfyui_local": comfyui_local,
        "method": method,
        "llm_model": llm_model,
        "env": env_vars,
    }


def _docker_steps(sch: dict, gpu: str, comfyui_local: bool, engine: str) -> list:
    """docker 部署的步骤（起容器 + 拉 LLM 模型）。"""
    up = ["docker", "compose", "up", "-d"]
    gpu_profile = comfyui_local and gpu != "none" and engine != "sol_h3"
    if gpu_profile:
        up = (["docker", "compose", "-f", "docker-compose.yml",
               "-f", "docker-compose.amd.yml", "--profile", "gpu", "up", "-d"]
              if gpu == "amd"
              else ["docker", "compose", "--profile", "gpu", "up", "-d"])
    return [
        {
            "title": "启动容器（agent + ollama" + (" + comfyui" if gpu_profile else "") + "）",
            "cmds": [("docker compose up", up)],
            "runnable": True,
            "manual": "",
        },
        {
            "title": "拉取 LLM 模型（容器内 Ollama）",
            "cmds": [("ollama pull", ["docker", "compose", "exec", "ollama",
                                      "ollama", "pull", sch["llm_model"]])],
            "runnable": False,  # 属模型下载，按约定不自动跑
            "manual": "",
        },
    ]


def _native_steps(sch: dict, gpu: str, comfyui_local: bool) -> list:
    """原生部署的步骤（建 venv + 装依赖 + Ollama / ComfyUI 提示）。"""
    vpy = venv_python()
    steps = [
        {
            "title": "创建虚拟环境并安装依赖",
            "cmds": [
                ("创建 venv", [sys.executable, "-m", "venv", ".venv"]),
                ("安装依赖", [vpy, "-m", "pip", "install", "-U", "pip"]),
                ("安装依赖", [vpy, "-m", "pip", "install", "-r", "requirements.txt"]),
            ],
            "runnable": True,
            "manual": "",
        },
        {
            "title": "安装并启动 Ollama（需自行装，非 pip 包）",
            "cmds": [],
            "runnable": False,
            "manual": f"装好 Ollama 后执行：  ollama pull {sch['llm_model']}",
        },
    ]
    if comfyui_local and gpu != "none":
        steps.append({
            "title": "启动本机 ComfyUI（需自行装，非 pip 包）",
            "cmds": [],
            "runnable": False,
            "manual": "在显卡机上启动 ComfyUI，并确认引擎节点（LTX-2.5 / MiniMax H3）已装。",
        })
    return steps


def _webui_step(sch: dict, method: str) -> dict:
    """启动 WebUI 步骤（长驻进程，只打印不自动跑）。"""
    start_cmd = ("浏览器打开 http://localhost:8000" if method == "docker"
                 else (".\\start_webui_windows.bat" if sch["os"] == "windows"
                       else "./start_webui.sh"))
    return {"title": "启动 WebUI", "cmds": [], "runnable": False, "manual": start_cmd}


def _weights_step(sch: dict, args, gpu: str, engine: str,
                  comfyui_local: bool) -> dict:
    """视频权重下载步骤（按引擎 / 是否有本地 ComfyUI 分档）。"""
    if engine == "sol_h3":
        return {
            "title": "视频权重（DGX Spark 远程 Sol-H3）",
            "cmds": [],
            "runnable": False,
            "manual": "权重在 DGX Spark 本地，由 deploy/sol_h3_spark/deploy_sol_h3_spark.sh "
                      "在其上执行 download_checkpoints.py。本机只跑 agent，"
                      f"通过 engine.sol_h3.api 指向 DGX 上的 sol_h3_server.py（{sch['api']}）。",
        }
    if comfyui_local and gpu != "none":
        dl_script = ("download_mmh3_models.py" if engine == "comfyui_mmH3"
                     else "download_ltx_models.py")
        models_dir = args.models_dir or default_models_dir(sch["os"])
        dl = ["python", dl_script, "--gpu", gpu, "--models-dir", models_dir]
        return {
            "title": f"下载视频权重（{engine} / {gpu}）",
            "cmds": [("下载权重", dl)],
            "runnable": bool(args.with_weights),  # 默认不跑，需 --with-weights 授权
            "manual": "" if args.with_weights else "需授权：加 --with-weights --models-dir <ComfyUI/models 路径>",
        }
    if not comfyui_local:
        return {
            "title": "视频权重（远程 ComfyUI）",
            "cmds": [],
            "runnable": False,
            "manual": f"ComfyUI 指向远程 {sch['api']}，权重在远程显卡机按上节下载，本机不用下。",
        }
    return {
        "title": "视频权重",
        "cmds": [],
        "runnable": False,
        "manual": "本机无独显，无法本地出视频；请把 COMFYUI_API 指向远程有显卡的 ComfyUI。",
    }


def _blender_step() -> dict:
    """Blender 白模提示步骤。"""
    return {
        "title": "Blender 白模（已启用，需手动装）",
        "cmds": [],
        "runnable": False,
        "manual": "Blender 白模已启用：需在 Blender 里装 blender_mcp_addon 并启用 auto_start，"
                  "deploy 不自动装 Blender。详见 docs/wsl2_deploy_plan.md。",
    }


def build_steps(sch: dict, args) -> list:
    """返回步骤列表：每步 {title, cmds:[(label,argv)], runnable, manual}。"""
    method, gpu, engine = sch["method"], sch["gpu"], sch["engine"]
    comfyui_local, blender = sch["comfyui_local"], sch["blender"]

    # 1) .env（apply 时由脚本写；这里给出内容）
    steps = [{
        "title": "生成 .env",
        "cmds": [],
        "runnable": True,
        "manual": "（deploy 自动写入 .env，内容见上方「.env 内容」）",
    }]

    if engine == "sol_h3":
        steps.append({
            "title": "在 DGX Spark 部署 Sol-H3（远程视频引擎）",
            "cmds": [],
            "runnable": False,
            "manual": "把 deploy/sol_h3_spark/ 整个目录拷到 DGX Spark，SSH 上去执行：\n"
                      "  cd deploy/sol_h3_spark && bash deploy_sol_h3_spark.sh\n"
                      "按提示填 HF_TOKEN / 三环境解释器路径。服务起在 0.0.0.0:8000，"
                      "把 config.yaml 的 engine.sol_h3.api 指向 http://<DGX-IP>:8000，"
                      "并把 engine.backend 设为 sol_h3（或 stage_profiles.G.engine=sol_h3）。",
        })

    steps += (_docker_steps(sch, gpu, comfyui_local, engine) if method == "docker"
              else _native_steps(sch, gpu, comfyui_local))
    steps.append(_webui_step(sch, method))
    steps.append(_weights_step(sch, args, gpu, engine, comfyui_local))
    if blender:
        steps.append(_blender_step())
    return steps


def venv_python() -> str:
    if os.name == "nt":
        return os.path.join(REPO, ".venv", "Scripts", "python.exe")
    return os.path.join(REPO, ".venv", "bin", "python")


def default_models_dir(os_name: str) -> str:
    return r"D:\ComfyUI\models" if os_name == "windows" else "/ComfyUI/models"


# ---------------------------------------------------------------- 输出 / 执行
def print_plan(sch: dict, steps: list):
    bar = "=" * 64
    print(bar)
    print("AI 电影 Agent · 部署方案（依据 config.yaml + 本机探测）")
    print(bar)
    print(f"  OS          : {sch['os']}")
    print(f"  Docker      : {'有' if sch['docker'] else '无'}  → 采用方式: {sch['method']}")
    print(f"  GPU 探测    : {sch['env_gpu']}  → 显卡后端: {sch['gpu']}")
    tier_line = sch["tier"] or "(未指定，用 config 默认)"
    if sch.get("tier_source"):
        tier_line += f"   ← {sch['tier_source']}"
    print(f"  硬件档位    : {tier_line}")
    print(f"  视频引擎    : {sch['engine']}")
    svc_label = "Sol-H3 服务" if sch["engine"] == "sol_h3" else "ComfyUI"
    print(f"  {svc_label:<12}: {'本机 ' if sch['comfyui_local'] else '远程 '}{sch['api']}")
    print(f"  Blender 白模: {'启用' if sch['blender'] else '关闭'}")
    print()
    print("## .env 内容（apply 时写入）")
    for k, v in sch["env"].items():
        print(f"  {k}={v}")
    print()
    print("## 执行步骤")
    for i, s in enumerate(steps, 1):
        print(f"  [{i}] {s['title']}")
        for label, argv in s["cmds"]:
            flag = "[执行]" if s["runnable"] else "[手动]"
            print(f"        {flag} {label}: {' '.join(argv)}")
        if s["manual"]:
            print(f"        > {s['manual']}")
    print(bar)


def write_env(sch: dict, force: bool) -> bool:
    path = os.path.join(REPO, ".env")
    if os.path.exists(path) and not force:
        print(f"[跳过] .env 已存在，未覆盖（用 --force-env 强制重写）：{path}")
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 由 deploy.py 生成（配置驱动）。可手动修改。\n")
        for k, v in sch["env"].items():
            f.write(f"{k}={v}\n")
    print(f"[完成] 已写入 .env：{path}")
    return True


def run_steps(steps: list, apply: bool):
    if not apply:
        return
    for i, s in enumerate(steps, 1):
        if not s["cmds"]:
            continue
        if not s["runnable"]:
            continue  # 手动步骤（模型/Blender）只打印
        for label, argv in s["cmds"]:
            print(f"\n>>> [{i}] {label}: {' '.join(argv)}")
            r = _run(argv)
            if r is None:
                print("    [警告] 命令未执行（异常/超时），请手动运行。")
                continue
            out = (r.stdout or "") + (r.stderr or "")
            if out.strip():
                print("    " + out.strip().replace("\n", "\n    ")[:2000])
            if r.returncode != 0:
                print(f"    [失败] 返回码 {r.returncode}，请检查后手动继续。")


def main():
    p = argparse.ArgumentParser(description="AI 电影 Agent 配置驱动部署")
    p.add_argument("--config", default=os.path.join(REPO, "config.yaml"))
    p.add_argument("--method", choices=["docker", "native", "auto"], default="auto")
    p.add_argument("--gpu", choices=["nvidia", "amd", "none", "auto"], default="auto")
    p.add_argument("--engine", choices=["comfyui_mmH3", "comfyui_ltx", "sol_h3"], default=None)
    p.add_argument("--tier", default=None,
                   help="硬件档位：high / mid / low / cpu / amd395-128g"
                        "（amd395-128g = AMD Ryzen AI Max+ 395, 128GB 统一内存；"
                        "别名 amd395 / 395 / strix-halo）。会写入 .env 的 HW_TIER")
    p.add_argument("--models-dir", default=None, help="视频权重目录（--with-weights 时需要）")
    p.add_argument("--apply", action="store_true", help="执行安全部分（venv/依赖/.env/compose up）")
    p.add_argument("--with-weights", action="store_true", help="授权下载视频权重（需同时 --apply）")
    p.add_argument("--force-env", action="store_true", help="强制覆盖已存在的 .env")
    args = p.parse_args()

    if not os.path.exists(args.config):
        print(f"[错误] 找不到配置文件：{args.config}")
        sys.exit(2)

    cfg = load_config(args.config)
    sch = resolve_scheme(cfg, args)
    steps = build_steps(sch, args)

    print_plan(sch, steps)

    if args.apply:
        print("\n=== 执行安全部分 ===")
        write_env(sch, args.force_env)
        run_steps(steps, apply=True)
        print("\n=== 完成 ===")
        for s in steps:
            if not s["runnable"] and (s["cmds"] or s["manual"]):
                pass  # 已在计划里列出
        print("需手动确认的步骤（模型/Blender/WebUI 启动）见上方方案。")
    else:
        print("\n（仅打印方案，未做任何改动。加 --apply 执行安全部分；"
              "权重下载需 --apply --with-weights 授权。）")


if __name__ == "__main__":
    main()
