"""部署期用环境变量覆盖 config.yaml（Docker / 远程 ComfyUI 场景，免改 yaml）。

支持：
  OLLAMA_URL     LLM 服务地址，如 http://ollama:11434 或 http://192.168.1.10:11434
                 -> 自动改写 config.llm.base_url 与所有 llm 档案的 base_url（补 /v1）
  LLM_MODEL      LLM 模型名，如 qwen2.5:3b
  LLM_API_KEY    LLM api_key（Ollama 默认 ollama）
  COMFYUI_API    视频生成服务地址，如 http://comfyui:8188 或 http://192.168.1.20:8188
                 -> 改写 engine.comfyui_ltx.api / engine.comfyui_mmH3.api /
                    image_prompt.comfyui.api 及所有 comfyui 档案
  ENGINE_BACKEND 默认视频引擎：comfyui_mmH3（MiniMax H3）或 comfyui_ltx（LTX-2.5）
  GPU_BACKEND    显卡后端：nvidia（默认）或 amd。amd 时 NVFP4 不支持，
                 自动把 LTX 精度降为 bf16（视频权重需改用 bf16/GGUF/INT8 变体，见下载脚本）
  HW_TIER        硬件档位：high / mid / low / cpu / amd395-128g。
                 amd395-128g 专为 AMD Ryzen AI Max+ 395（Strix Halo, 128GB 统一内存）
                 准备；别名 amd395 / amd-395 / 395 / strix-halo / ai-max-395-128g 会自动归一。
  AUTO_HW        1/true 时自动检测本机硬件选档（AMD 395 128G 机器也能被正确识别为
                 amd395-128g，而不是因 iGPU 显存被报小而落到 cpu）。
"""
import os
import sys
import subprocess


def _set_profile_key(cfg: dict, group: str, key: str, value) -> None:
    """把 value 写入 cfg.profiles.<group>.*.<key>（仅处理 dict 档案）。"""
    profiles = ((cfg.get("profiles", {}) or {}).get(group, {}) or {}).values()
    for p in profiles:
        if isinstance(p, dict):
            p[key] = value


def _override_llm_endpoint(cfg: dict, base_url: str) -> None:
    """把 Ollama 地址写入 config.llm 与所有 llm 档案（补 /v1 后缀）。"""
    v1 = base_url + "/v1"
    cfg.setdefault("llm", {})["base_url"] = v1
    _set_profile_key(cfg, "llm", "base_url", v1)


def _override_comfyui_endpoint(cfg: dict, api: str) -> None:
    """把 ComfyUI 地址写入 engine 两套引擎 + image_prompt + 所有 comfyui 档案。"""
    cfg.setdefault("engine", {}).setdefault("comfyui_ltx", {})["api"] = api
    cfg.setdefault("engine", {}).setdefault("comfyui_mmH3", {})["api"] = api
    cfg.setdefault("image_prompt", {}).setdefault("comfyui", {})["api"] = api
    _set_profile_key(cfg, "comfyui", "api", api)


def apply_env_overrides(cfg: dict) -> dict:
    cfg = cfg or {}

    ollama = (os.environ.get("OLLAMA_URL") or "").rstrip("/")
    if ollama:
        _override_llm_endpoint(cfg, ollama)

    if os.environ.get("LLM_MODEL"):
        cfg.setdefault("llm", {})["model"] = os.environ["LLM_MODEL"]
    if os.environ.get("LLM_API_KEY"):
        cfg.setdefault("llm", {})["api_key"] = os.environ["LLM_API_KEY"]

    comfy = os.environ.get("COMFYUI_API")
    if comfy:
        _override_comfyui_endpoint(cfg, comfy)

    if os.environ.get("ENGINE_BACKEND"):
        cfg.setdefault("engine", {})["backend"] = os.environ["ENGINE_BACKEND"]

    # 显卡后端：amd 时 NVFP4 不支持，把 LTX 精度降为 bf16（bf16 权重跑 ROCm 更稳）
    if os.environ.get("GPU_BACKEND") == "amd":
        cfg.setdefault("engine", {}).setdefault("comfyui_ltx", {})["precision"] = "bf16"
    return apply_hw_overrides(cfg)


# ---------------------------------------------------------------------------
# 硬件自适应：根据 detected 显存(VRAM) / 内存(RAM) 自动选择配置档位
# 无额外依赖（标准库 + nvidia-smi / rocm-smi / WMI / /proc/meminfo）。
# 触发方式（任一即可，否则保持原配置，向后兼容）：
#   AUTO_HW=1                      自动检测本机硬件选档
#   HW_TIER=high|mid|low|cpu|amd395-128g   强制指定（远程显卡规格已知时最准）
#   config.auto_hardware: true     同上，写进 config.yaml
#   config.hw_tier: <档>           同上，写进 config.yaml
# ---------------------------------------------------------------------------
# AMD Ryzen AI Max+ 395（Strix Halo, 128GB 统一内存）专属档位。
# 为什么要单独一档：其「显存」是从 128GB 统一内存里切出来的（BIOS 设 UMA 75–96GB），
# 而 Windows WMI 的 AdapterRAM 是 32 位字段、iGPU 常被报成 512MB~4GB；Linux lspci 更是 0。
# 结果就是自动检测把顶级机器判成 cpu 档。故用「AMD + 大内存 + 型号线索」显式识别。
AMD395_TIER = "amd395-128g"
AMD395_NAME_HINTS = ("395", "STRIX", "AI MAX", "8060", "RADEON 8050")
# 别名归一：允许 HW_TIER / config.hw_tier / --tier 用口语写法
TIER_ALIASES = {
    "amd395": AMD395_TIER,
    "amd-395": AMD395_TIER,
    "amd_395": AMD395_TIER,
    "amd395-128g": AMD395_TIER,
    "amd395128g": AMD395_TIER,
    "amd-395-128g": AMD395_TIER,
    "395": AMD395_TIER,
    "395-128g": AMD395_TIER,
    "ai-max-395": AMD395_TIER,
    "ai-max-395-128g": AMD395_TIER,
    "ryzen-ai-max-395": AMD395_TIER,
    "strix-halo": AMD395_TIER,
    "strixhalo": AMD395_TIER,
    "halo": AMD395_TIER,
}


def normalize_tier(name) -> str:
    """把档位名归一为 HW_TIER_PROFILES 的规范键；未知/空返回空串。"""
    if not isinstance(name, str):
        return ""
    t = name.strip().lower()
    if not t:
        return ""
    t = TIER_ALIASES.get(t, t)
    return t if t in HW_TIER_PROFILES else ""


def _is_amd395(hw: dict) -> bool:
    """判是否 AMD Ryzen AI Max+ 395 一类的大统一内存机（Strix Halo）。"""
    if hw.get("vendor") != "AMD":
        return False
    if (hw.get("ram_gb") or 0) < 96:
        return False
    name = (hw.get("gpu_name") or "").upper()
    has_hint = any(h in name for h in AMD395_NAME_HINTS)
    # 型号没线索时，靠「AMD 大内存 + 显存报得极小（iGPU 被 WMI/lspci 低估）」兜底。
    # 真独显（如 RX 7900 + 128G RAM）显存 ≥8GB，不会命中这里。
    return has_hint or (hw.get("vram_gb") or 0) < 6


def _detect_ram_gb() -> float:
    """跨平台读取物理内存总量（GiB）；失败返回 0.0。"""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=20)
            if out.returncode == 0 and out.stdout.strip():
                return int(out.stdout.strip()) / (1024 ** 3)
        elif sys.platform.startswith("linux"):
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024 / 1024
        elif sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=10)
            return int(out.stdout.strip()) / (1024 ** 3)
    except Exception:
        pass
    return 0.0


def _detect_nvidia() -> dict | None:
    """nvidia-smi 探测 NVIDIA 显卡；未安装/失败返回 None。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        lines = [l for l in out.stdout.strip().splitlines() if l.strip()]
        if not lines:
            return None
        parts = [p.strip() for p in lines[0].split(",")]
        info = {"vendor": "NVIDIA", "gpu_name": parts[0], "vram_gb": 0.0}
        try:
            # nvidia-smi --format=nounits 的 memory.total 单位为 MiB
            info["vram_gb"] = float(parts[1]) / 1024.0
        except (ValueError, IndexError):
            pass
        return info
    except Exception:
        return None


def _pick_vram_device(arr: list) -> dict:
    """从 WMI 设备列表里选出显存最大的那块，并识别厂商。"""
    info = {"vendor": None, "gpu_name": None, "vram_gb": 0.0}
    for dev in arr:
        name = (dev.get("Name") or "").upper()
        ram = (dev.get("AdapterRAM") or 0) / (1024 ** 3)
        if "AMD" in name or "RADEON" in name:
            info["vendor"] = "AMD"
        elif info["vendor"] is None and ("NVIDIA" in name or "INTEL" in name):
            info["vendor"] = "OTHER"
        if ram > info["vram_gb"]:
            info["vram_gb"] = ram
            info["gpu_name"] = dev.get("Name")
    return info


def _detect_gpu_win_wmi() -> dict | None:
    """Windows WMI 探测非 NVIDIA 显卡（AMD / Intel）；失败返回 None。"""
    import json as _json
    try:
        ps = ("Get-CimInstance Win32_VideoController | "
              "Where-Object {$_.AdapterRAM} | "
              "Select-Object Name,AdapterRAM | ConvertTo-Json")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        arr = _json.loads(out.stdout)
    except Exception:
        return None
    if isinstance(arr, dict):
        arr = [arr]
    return _pick_vram_device(arr)


def _detect_gpu_linux_lspci() -> dict | None:
    """Linux lspci 探测 AMD / NVIDIA 显卡；失败返回 None。"""
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if "VGA" not in line and "3D" not in line:
                continue
            if "AMD" in line or "ATI" in line:
                vendor = "AMD"
            elif "NVIDIA" in line:
                vendor = "NVIDIA"
            else:
                continue
            return {"vendor": vendor, "gpu_name": line.split(":")[-1].strip(),
                    "vram_gb": 0.0}
    except Exception:
        return None
    return None


def detect_hardware() -> dict:
    """跨平台检测 GPU 厂商 / 显存 / 内存，无需额外依赖。"""
    info = {"vendor": None, "gpu_name": None, "vram_gb": 0.0, "ram_gb": 0.0}
    info["ram_gb"] = _detect_ram_gb()
    nv = _detect_nvidia()
    if nv:
        info.update(nv)
        return info
    other = (_detect_gpu_win_wmi() if sys.platform.startswith("win")
             else _detect_gpu_linux_lspci() if sys.platform.startswith("linux")
             else None)
    if other:
        info.update({k: v for k, v in other.items() if k != "ram_gb"})
    return info


def pick_tier(hw: dict) -> str:
    vram = hw.get("vram_gb") or 0
    ram = hw.get("ram_gb") or 0
    # 先认 AMD 395（Strix Halo 128G）：其显存是 UMA 切分且常被低估，必须先于通用档判断
    if _is_amd395(hw):
        return AMD395_TIER
    if not hw.get("vendor") or vram < 6:
        return "cpu"
    if vram >= 24 and ram >= 48:
        return "high"
    if vram >= 12 and ram >= 24:
        return "mid"
    return "low"


# 各档位的覆盖项（点路径 -> 值）。只在对应档位写入，其余保留 config 默认。
HW_TIER_PROFILES = {
    # AMD Ryzen AI Max+ 395（Strix Halo, 128GB 统一内存）+ ROCm。
    # 与 high 的区别：显式钉死 bf16（NVFP4/int4_convrot 是 NVIDIA 专属）、开两遍采样、
    # 允许多 reroll、Blender 高档采样——128G 统一内存有足够余量，不必省。
    "amd395-128g": {
        "engine.comfyui_ltx.precision": "bf16",
        "engine.comfyui_mmH3.resolution": "1024x576",
        "engine.comfyui_mmH3.num_frames": 90,
        "engine.offload": False,
        "llm.model": "qwen2.5:7b",
        "engine.comfyui_mmH3.block_cache.enable": True,
        "engine.comfyui_mmH3.two_pass.enable": True,
        "qa.max_rerolls": 3,
        "blender.samples": 64,
    },
    "high": {
        "engine.comfyui_mmH3.resolution": "1024x576",
        "engine.comfyui_mmH3.num_frames": 90,
        "engine.offload": False,
        "llm.model": "qwen2.5:7b",
        "engine.comfyui_mmH3.block_cache.enable": True,
        "engine.comfyui_mmH3.two_pass.enable": False,
        "qa.max_rerolls": 2,
        "blender.samples": 48,
    },
    "mid": {
        "engine.comfyui_mmH3.resolution": "768x448",
        "engine.comfyui_mmH3.num_frames": 56,
        "llm.model": "qwen2.5:3b",
        "engine.comfyui_mmH3.block_cache.enable": False,
        "qa.max_rerolls": 1,
        "blender.samples": 24,
    },
    "low": {
        "engine.comfyui_mmH3.resolution": "512x288",
        "engine.comfyui_mmH3.num_frames": 17,
        "engine.offload": True,
        "llm.model": "qwen2.5:1.5b",
        "engine.comfyui_mmH3.block_cache.enable": False,
        "engine.comfyui_mmH3.two_pass.enable": False,
        "qa.max_rerolls": 0,
        "blender.samples": 12,
    },
    "cpu": {
        "llm.model": "qwen2.5:1.5b",
        "llm.disabled": False,
        "qa.max_rerolls": 0,
        "blender.enabled": False,
        "engine.comfyui_mmH3.block_cache.enable": False,
        "engine.comfyui_mmH3.two_pass.enable": False,
    },
}


def _deep_set(cfg: dict, dotted: str, val):
    keys = dotted.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d.setdefault(k, {})
        if not isinstance(d, dict):
            return
    d[keys[-1]] = val


def _apply_tier(cfg: dict, tier: str, vendor=None):
    canonical = normalize_tier(tier)
    if not canonical:
        return
    overrides = dict(HW_TIER_PROFILES[canonical])
    if vendor == "AMD":
        overrides["engine.comfyui_ltx.precision"] = "bf16"
    for path, val in overrides.items():
        _deep_set(cfg, path, val)


def apply_hw_overrides(cfg: dict) -> dict:
    """根据 AUTO_HW / HW_TIER 环境变量或 config.auto_hardware / config.hw_tier，
    自动套用硬件档位覆盖。默认不改动（向后兼容）。"""
    tier = os.environ.get("HW_TIER")
    if tier:
        tier = normalize_tier(tier)
    else:
        tier = normalize_tier(cfg.get("hw_tier"))
    if tier:
        _apply_tier(cfg, tier, os.environ.get("GPU_BACKEND"))
        return cfg
    auto = os.environ.get("AUTO_HW") or ("true" if cfg.get("auto_hardware") is True else "")
    if str(auto).lower() in ("1", "true", "yes", "on"):
        try:
            hw = detect_hardware()
            _apply_tier(cfg, pick_tier(hw), hw.get("vendor"))
        except Exception:
            pass
    return cfg
