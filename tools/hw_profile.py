#!/usr/bin/env python3
"""硬件自适应配置工具。

根据机器显存(VRAM) / 内存(RAM) 自动选择配置档位（high / mid / low / cpu / amd395-128g / dgxspark-128g），
并把对应的参数覆盖应用到 config.yaml，或打印供启动时自动套用的环境变量。

依赖：仅标准库（无 psutil/pynvml/torch 等）。GPU 检测走 nvidia-smi /
WMI(/proc/lspci)，内存走 ctypes / /proc/meminfo / sysctl。

用法：
  python tools/hw_profile.py                 # 检测硬件 + 推荐档位 + 将应用的覆盖（dry-run，不改配置）
  python tools/hw_profile.py --tier high    # 强制指定档位
  python tools/hw_profile.py --tier amd395  # AMD Ryzen AI Max+ 395（128G UMA）；别名 395/strix-halo 均可
  python tools/hw_profile.py --write         # 把推荐档位写入 config.yaml（追加 auto_hardware/hw_tier）
  python tools/hw_profile.py --set-env       # 打印启动环境变量指令（AUTO_HW=1），供 cli.py/webui.py 自动套用
  python tools/hw_profile.py --config path   # 指定 config.yaml 路径
"""
from __future__ import annotations

import os
import re
import sys
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config_env import (  # noqa: E402
    AMD395_TIER,
    HW_TIER_PROFILES,
    detect_hardware,
    explain_tier,
    normalize_tier,
)


def compute_overrides(tier: str, vendor) -> dict:
    ov = dict(HW_TIER_PROFILES.get(tier, {}))
    if vendor == "AMD":
        ov["engine.comfyui_ltx.precision"] = "bf16"
    return ov


def write_config(path: str, tier: str):
    """在 config.yaml 末尾（或就地）设置 auto_hardware: true / hw_tier: <tier>，保留其余注释。"""
    text = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    if re.search(r"^\s*auto_hardware\s*:", text, re.M):
        text = re.sub(r"^\s*auto_hardware\s*:.*$", "auto_hardware: true", text, flags=re.M)
    else:
        text += "\nauto_hardware: true\n"
    if re.search(r"^\s*hw_tier\s*:", text, re.M):
        text = re.sub(r"^\s*hw_tier\s*:.*$", f"hw_tier: {tier}", text, flags=re.M)
    else:
        text += f"hw_tier: {tier}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _resolve_tier(arg_tier) -> str:
    """--tier 参数 -> 规范档位；未知则报错退出。空参数返回空串（走自动检测）。"""
    if not arg_tier:
        return ""
    tier = normalize_tier(arg_tier)
    if not tier:
        print(f"[错误] 未知档位：{arg_tier}")
        print("        可选：" + " / ".join(HW_TIER_PROFILES))
        print(f"        别名：amd395 / amd-395 / 395 / strix-halo / ai-max-395-128g "
              f"→ {AMD395_TIER}")
        sys.exit(2)
    return tier


def _print_tier_hint(tier: str, hw: dict) -> None:
    """按档位 / 厂商打印针对性的部署提示。"""
    if tier == AMD395_TIER:
        print("\n[提示] AMD Ryzen AI Max+ 395 档位（Strix Halo / 128GB 统一内存）：")
        print("        - 显存由 UMA 从内存切出，被 WMI/lspci 报成 0~4GB 属正常，故用「RAM+型号」识别")
        print("        - 先在 BIOS 把 UMA Frame Buffer Size 设为 75–96GB，否则 ROCm 只看到 16–32GB")
        print("        - LTX 精度已钉死 bf16；权重用 bf16/INT8 变体：download_ltx_models.py --gpu amd")
        print("        - 详见 LTX25_AMD395_PLAN.md 与 setup_amd.sh")
    elif hw.get("vendor") == "AMD":
        print("\n[提示] AMD 显卡：LTX 精度已降为 bf16；MiniMax H3 的 int4_convrot 权重为 NVIDIA 专属，")
        print("        需改用手动 bf16/INT8 权重（见 download_mmh3_models.py --gpu amd）。")


def main():
    ap = argparse.ArgumentParser(description="硬件自适应配置：按显存/内存自动选档")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--tier", default=None,
                    help="强制指定档位（跳过硬件检测）：" + " / ".join(HW_TIER_PROFILES)
                         + "；亦接受别名 amd395 / 395 / strix-halo")
    ap.add_argument("--write", action="store_true",
                    help="把推荐档位写入 config.yaml（追加 auto_hardware/hw_tier 字段）")
    ap.add_argument("--set-env", action="store_true",
                    help="打印启动环境变量指令（AUTO_HW=1），供 cli.py/webui.py 自动套用")
    args = ap.parse_args()

    forced = _resolve_tier(args.tier)      # 先校验档位，非法即刻退出（不必探测硬件）
    hw = detect_hardware()
    info = explain_tier(hw)                # 档位 + 判定依据（同源，不会漂移）
    tier = forced or info["tier"]
    overrides = compute_overrides(tier, hw.get("vendor"))

    print("== 硬件检测 ==")
    print(f"  厂商      : {hw.get('vendor') or '无独显 / 未识别'}")
    print(f"  GPU       : {hw.get('gpu_name') or '-'}")
    print(f"  显存 VRAM : {hw.get('vram_gb') or 0:.1f} GB")
    print(f"  内存 RAM  : {hw.get('ram_gb') or 0:.1f} GB")
    print(f"  推荐档位  : {tier}" + ("  (手动指定)" if forced else ""))
    # 判定依据：大统一内存机靠型号线索识别，线索没命中会**静默**落到 high —— 摆出来供核对
    if forced:
        print(f"  判定依据  : 手动指定（自动检测会判 {info['tier']} —— {info['reason']}）")
    else:
        print(f"  判定依据  : {info['reason']}")
    print("\n== 将应用的配置覆盖 ==")
    if not overrides:
        print("  (无)")
    for k, v in overrides.items():
        print(f"  {k} = {v!r}")

    _print_tier_hint(tier, hw)

    if args.set_env:
        if os.name == "nt":
            print("\nrem 启动管线前执行：")
            print("set AUTO_HW=1")
        else:
            print("\n# 启动管线前执行：")
            print("export AUTO_HW=1")
        return

    if args.write:
        write_config(args.config, tier)
        print(f"\n[已写入] {args.config}：auto_hardware: true, hw_tier: {tier}")
        print("下次启动 cli.py / webui.py 会自动套用该档位（也可改用 `set AUTO_HW=1` 实时检测）。")
        return

    print("\n(未改动配置。加 --write 写入 config.yaml，或 --set-env 打印启动变量。)")


if __name__ == "__main__":
    main()
