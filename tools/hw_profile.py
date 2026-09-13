#!/usr/bin/env python3
"""硬件自适应配置工具。

根据机器显存(VRAM) / 内存(RAM) 自动选择配置档位（high / mid / low / cpu），
并把对应的参数覆盖应用到 config.yaml，或打印供启动时自动套用的环境变量。

依赖：仅标准库（无 psutil/pynvml/torch 等）。GPU 检测走 nvidia-smi /
WMI(/proc/lspci)，内存走 ctypes / /proc/meminfo / sysctl。

用法：
  python tools/hw_profile.py                 # 检测硬件 + 推荐档位 + 将应用的覆盖（dry-run，不改配置）
  python tools/hw_profile.py --tier high    # 强制指定档位
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

from config_env import detect_hardware, pick_tier, HW_TIER_PROFILES  # noqa: E402


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


def main():
    ap = argparse.ArgumentParser(description="硬件自适应配置：按显存/内存自动选档")
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--tier", choices=list(HW_TIER_PROFILES.keys()),
                    help="强制指定档位（跳过硬件检测）")
    ap.add_argument("--write", action="store_true",
                    help="把推荐档位写入 config.yaml（追加 auto_hardware/hw_tier 字段）")
    ap.add_argument("--set-env", action="store_true",
                    help="打印启动环境变量指令（AUTO_HW=1），供 cli.py/webui.py 自动套用")
    args = ap.parse_args()

    hw = detect_hardware()
    tier = args.tier or pick_tier(hw)
    overrides = compute_overrides(tier, hw.get("vendor"))

    print("== 硬件检测 ==")
    print(f"  厂商      : {hw.get('vendor') or '无独显 / 未识别'}")
    print(f"  GPU       : {hw.get('gpu_name') or '-'}")
    print(f"  显存 VRAM : {hw.get('vram_gb') or 0:.1f} GB")
    print(f"  内存 RAM  : {hw.get('ram_gb') or 0:.1f} GB")
    print(f"  推荐档位  : {tier}")
    print("\n== 将应用的配置覆盖 ==")
    if not overrides:
        print("  (无)")
    for k, v in overrides.items():
        print(f"  {k} = {v!r}")

    if hw.get("vendor") == "AMD":
        print("\n[提示] AMD 显卡：LTX 精度已降为 bf16；MiniMax H3 的 int4_convrot 权重为 NVIDIA 专属，")
        print("        需改用手动 bf16/INT8 权重（见 download_mmh3_models.py --gpu amd）。")

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
