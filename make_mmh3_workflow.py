"""生成可在 ComfyUI 界面直接导入的 MiniMax H3（Turbo）工作流。

背景：引擎 agent/mmh3_engine.py 是运行时拼 **API 格式** 工作流的，没法直接拖进
ComfyUI 画布；手搭又极易把文本编码器配成 LTX-2.5 的 Gemma4-12B（6144 维），
导致 KSampler 报 "mat1 and mat2 shapes cannot be multiplied (75x6144 and 5120x5376)"。

本脚本以官方 comfyui-minimax-h3-audio-T8 的示例工作流为骨架（UI 格式：nodes+links），
按 config.yaml 的 engine.comfyui_mmH3 改写权重名 / 分辨率 / 帧数 / 步数，
产出可直接导入画布的 JSON。

用法：
    python make_mmh3_workflow.py
    python make_mmh3_workflow.py --source <官方示例.json> --out workflows/xxx.json
    python make_mmh3_workflow.py --prompt "..." --length 73 --resolution 1280x720
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 官方示例工作流位置：随 ComfyUI 安装目录解析（COMFYUI_ROOT / COMFYUI_CUSTOM_NODES 可覆盖）
try:
    from comfy_paths import custom_nodes_dir as _custom_nodes_dir
    DEFAULT_SRC = os.path.join(
        _custom_nodes_dir(), "comfyui-minimax-h3-audio-T8", "examples", "workflows",
        "01-basic-generation", "2026-08-06_H3_Turbo_EXP_4V8A.json")
except Exception:      # 单独拷走使用时退回字面兜底
    DEFAULT_SRC = (
        r"D:\ComfyUI\custom_nodes\comfyui-minimax-h3-audio-T8"
        r"\examples\workflows\01-basic-generation\2026-08-06_H3_Turbo_EXP_4V8A.json"
    )
DEFAULT_OUT = os.path.join("workflows", "mmh3_turbo_4v8a_ui.json")
DEFAULT_PROMPT = (
    "Cinematic shot of a young woman with short dark hair in a worn dark trench coat "
    "standing on a windswept city rooftop at golden hour, gazing over a sprawling neon "
    "skyline, slow push-in camera, warm volumetric backlight, shallow depth of field, "
    "subtle film grain; ambient city wind and a low distant traffic hum."
)

# 节点 ID 与 widgets_values 位序（来自官方示例，改动前请对照示例文件）
N_UNET, N_LORA, N_CLIP = "1", "2", "3"
N_VIDEO_VAE, N_AUDIO_VAE = "4", "5"
N_COND, N_SAMPLER, N_NOISE = "6", "7", "9"
N_COMBINE = "12"


def snap_length(n: int, base: int = 5, step: int = 17) -> int:
    """H3 要求长度落在 17n+5 网格（5, 22, 39, 56, 73, 90, 107, 124 ...）。"""
    n = max(int(n), base)
    return base + -(-(n - base) // step) * step


def snap_resolution(res: str, align: int = 32) -> tuple[int, int]:
    """H3 要求宽高被 32 整除。"""
    try:
        w, h = (int(x) for x in str(res).lower().split("x"))
    except Exception:
        w, h = 768, 448
    w = max(align, (max(w, align) // align) * align)
    h = max(align, (max(h, align) // align) * align)
    return w, h


def load_cfg(path: str = "config.yaml") -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("engine", {}).get("comfyui_mmH3", {}) or {}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成可导入的 MiniMax H3 Turbo 工作流")
    ap.add_argument("--source", default=DEFAULT_SRC, help="官方示例工作流（UI 格式）")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--prompt", default="", help="覆盖提示词")
    ap.add_argument("--length", type=int, default=0, help="帧数（自动吸附 17n+5）")
    ap.add_argument("--resolution", default="", help="如 768x448 / 1280x720（自动 32 对齐）")
    ap.add_argument("--no-turbo", action="store_true", help="不挂 Turbo LoRA（改用标准步数）")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        print(f"[err] 找不到官方示例工作流：{args.source}\n"
              f"      请用 --source 指向 comfyui-minimax-h3-audio-T8 的 "
              f"examples/workflows/01-basic-generation/*.json")
        return 1

    cfg = load_cfg(args.config)
    with open(args.source, encoding="utf-8") as f:
        wf = json.load(f)

    nodes = {str(n["id"]): n for n in wf.get("nodes", [])}

    def widgets(nid: str) -> list:
        return nodes[nid].setdefault("widgets_values", [])

    # ---- 权重：全部改用 config.yaml 里实际下载的文件名 ----
    widgets(N_UNET)[0] = cfg.get("unet") or "minimax_h3_fl2va_pruned_int4_convrot.safetensors"
    if not args.no_turbo:
        widgets(N_LORA)[0] = (cfg.get("lora")
                              or "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors")
        widgets(N_LORA)[1] = float(cfg.get("lora_strength", 1.0))
    # CLIPLoader: [clip_name, type, device] —— type 必须是 minimax，不是 lumina2
    clip_w = widgets(N_CLIP)
    clip_w[0] = cfg.get("text_encoder") or "qwen3vl_32b_minimax_h3_int4_convrot.safetensors"
    if len(clip_w) > 1:
        clip_w[1] = "minimax"
    widgets(N_VIDEO_VAE)[0] = cfg.get("video_vae") or "minimax_h3_video_vae_fp16.safetensors"
    widgets(N_AUDIO_VAE)[0] = cfg.get("audio_vae") or "minimax_h3_audio_vae_fp32.safetensors"

    # ---- 采样步数（Turbo 4 视频 / 8 音频）与 shift ----
    swe = widgets(N_SAMPLER)
    if len(swe) >= 4:
        swe[0], swe[1] = int(cfg.get("video_steps", 4)), int(cfg.get("audio_steps", 8))
        swe[2], swe[3] = float(cfg.get("shift_video", 12.0)), float(cfg.get("shift_audio", 3.0))

    # ---- 条件节点：prompt / 宽 / 高 / 长度 ----
    w, h = snap_resolution(args.resolution or cfg.get("resolution", "768x448"))
    length = snap_length(args.length or int(cfg.get("num_frames", 56)))
    cw = widgets(N_COND)
    if len(cw) >= 4:
        cw[0] = args.prompt or DEFAULT_PROMPT
        cw[1], cw[2], cw[3] = w, h, length

    # ---- 输出：帧率 / 前缀 ----
    # VHS_VideoCombine 的 widgets_values 在 UI/API 两种格式下都是 dict，按键写入即可
    comb = nodes.get(N_COMBINE, {}).get("widgets_values")
    if isinstance(comb, dict):
        comb["frame_rate"] = int(cfg.get("fps", 24))
        comb["filename_prefix"] = cfg.get("filename_prefix") or "H3/pipe"
    else:
        print(f"[warn] 未找到 VHS_VideoCombine 参数字典，帧率/前缀未改写"
              f"（{type(comb).__name__}）")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(wf, f, ensure_ascii=False, indent=1)

    print(f"[ok] 已生成 {args.out}")
    print(f"     unet  : {widgets(N_UNET)[0]}")
    print(f"     clip  : {clip_w[0]} (type={clip_w[1] if len(clip_w) > 1 else '?'})")
    print(f"     vae   : {widgets(N_VIDEO_VAE)[0]} / {widgets(N_AUDIO_VAE)[0]}")
    print(f"     lora  : {widgets(N_LORA)[0] if not args.no_turbo else '(已关闭 Turbo)'}")
    print(f"     输出  : {w}x{h}, {length}帧, {swe[0]}视频步/{swe[1]}音频步")
    print("     导入：ComfyUI 菜单 Workflow → Open（或把 json 拖到画布）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
