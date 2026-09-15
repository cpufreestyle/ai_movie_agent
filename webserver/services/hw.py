"""部署 · 硬件档位（`HW_TIER`）的选项 / 当前值 / 覆盖预览。

给 WebUI「设置页」的下拉框供数据。**档位列表与覆盖内容全部从 `config_env` 派生**
（`HW_TIER_PROFILES` / `TIER_ALIASES` 是唯一权威）—— 这里只额外提供中文标签，
所以新增档位时只需改 `config_env.py` + 在 `TIER_LABELS` 补一条说明；
漏补会被 `tests/test_hw_webui.py` 的「每个档位都有标签」断言拦下（界面上漏档位是静默的）。

本模块**不 import** 任何会建目录 / 探外部程序的根脚本（与 `services/anchor.py` 同样的纪律）；
`config_env` 是纯标准库，导入它不会拖慢 Web 服务启动。
"""
from __future__ import annotations

import sys

from ..state import HERE, load_config

if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config_env as ce  # noqa: E402

#: 空值 = 不动配置（下拉框第一项）
NO_TIER_LABEL = "默认 · 不改动（沿用 config.yaml 现有值）"

#: 档位 -> 中文说明。键必须覆盖 config_env.HW_TIER_PROFILES 的全部档位（有测试兜底）。
TIER_LABELS = {
    "amd395-128g": "amd395-128g · AMD Ryzen AI Max+ 395（128GB 统一内存，bf16）",
    "dgxspark-128g": "dgxspark-128g · NVIDIA DGX Spark / Project Digits（128GB 统一内存，bf16）",
    "high": "high · 大显存独显（≥24GB VRAM）",
    "mid": "mid · 主流独显（≥12GB VRAM）",
    "low": "low · 小显存 / 需 offload",
    "cpu": "cpu · 无独显（仅文案链路，不本地出视频）",
}


def tier_options() -> list:
    """下拉项：[默认] + 各档位（顺序跟 config_env.HW_TIER_PROFILES）。"""
    opts = [{"value": "", "label": NO_TIER_LABEL}]
    for name in ce.HW_TIER_PROFILES:
        opts.append({"value": name, "label": TIER_LABELS.get(name, name)})
    return opts


def profiles() -> dict:
    """每个档位将应用的覆盖项（点路径 -> 值），给前端做预览。"""
    return {name: dict(over) for name, over in ce.HW_TIER_PROFILES.items()}


def current() -> dict:
    """config.yaml 里的当前选择。

    `hw_tier` 归一成规范档位；若原值是别名（如 `395`）会归一后返回，
    让下拉框能正确选中同一档。原值无法识别时 `unknown=True` 并原样带回 `raw`，
    前端据此显示一条「无法识别」项而不是静默丢掉。
    """
    cfg = load_config() or {}
    raw = cfg.get("hw_tier")
    raw = raw.strip() if isinstance(raw, str) else ""
    normalized = ce.normalize_tier(raw) if raw else ""
    return {
        "hw_tier": normalized,
        "raw": raw,
        "unknown": bool(raw) and not normalized,
        "auto_hardware": cfg.get("auto_hardware") is True,
    }


def detect() -> dict:
    """探测本机硬件并给出推荐档位。

    注意：Windows 下会调一次 PowerShell（约 1~2 秒），所以**只在 `?detect=1` 时调用**，
    默认不探。失败不抛，回一个 error 字段让前端提示。
    """
    try:
        hw = ce.detect_hardware()
        info = ce.explain_tier(hw)
        return {
            "vendor": info.get("vendor"),
            "gpu_name": info.get("gpu_name"),
            "vram_gb": round(hw.get("vram_gb") or 0.0, 1),
            "ram_gb": round(hw.get("ram_gb") or 0.0, 1),
            "tier": info["tier"],
            # 判定依据：让「为什么判成这档」可被用户核对。大统一内存机靠型号线索认，
            # 线索没命中时会**静默**落到 high —— 没有依据就只能靠猜（见 config_env.explain_tier）。
            "reason": info.get("reason", ""),
            "matched_hints": list(info.get("matched_hints") or []),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def payload(with_detect: bool = False) -> dict:
    data = {
        "ok": True,
        "tiers": tier_options(),
        "aliases": dict(ce.TIER_ALIASES),
        "current": current(),
        "profiles": profiles(),
        "amd395_tier": ce.AMD395_TIER,
    }
    if with_detect:
        data["detected"] = detect()
    return data


__all__ = ["NO_TIER_LABEL", "TIER_LABELS", "current", "detect", "payload",
           "profiles", "tier_options"]
