#!/usr/bin/env python
"""一键画质增强：把成片按「开源最佳顺序」跑一遍再高质量封装。

参照 Video2X（frame extraction → upscale/interpolate → reassemble with audio）
与 Real-ESRGAN 官方建议，增强顺序固定为：

    1) RIFE 插帧（先做）—— 在**低分辨率**上补帧，显存/耗时最省；
       Video2X 文档明确「高动态场景」推荐先插值再放大。
    2) ESRGAN 超分（后做）—— 帧数已翻倍，输出更顺滑。
    3) 统一高质量重编码 —— crf/preset/tune/faststart 走 `agent/encode.py` 档位。

只跑编码（不碰 GPU）也支持：`--no-rife --no-sr`，用于给已有成片补
faststart / GOP / 动漫 tune。

依赖：RIFE 与超分阶段需要 ComfyUI(8188) 及其 Frame-Interpolation 插件；
缺失时会自动跳过并提示，不阻断（除非 --strict）。

用法：
  python enhance_video.py <src.mp4> <dst.mp4>
  python enhance_video.py in.mp4 out.mp4 --kind anime        # 动漫：anime tune + 动漫超分模型
  python enhance_video.py in.mp4 out.mp4 --no-sr             # 只插帧
  python enhance_video.py in.mp4 out.mp4 --no-rife --no-sr   # 只重编码（补 faststart）
  python enhance_video.py in.mp4 out.mp4 --profile high --denoise
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from agent import encode as enc  # noqa: E402

API_DEFAULT = "http://127.0.0.1:8188"


def comfy_ready(api: str = API_DEFAULT) -> bool:
    """ComfyUI 是否就绪（用 /system_stats，失败即视为不可用）。"""
    try:
        import urllib.request

        req = urllib.request.Request(api.rstrip("/") + "/system_stats", method="GET")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def has_audio(path: str, ff: str) -> bool:
    """探测是否有音轨（决定最终混音取哪一路）。"""
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path],
                           capture_output=True, text=True, errors="ignore")
        return "Audio:" in (r.stderr or "")
    except Exception:
        return False


def _run(cmd: list[str], timeout: int = 3600) -> bool:
    print("[run] " + " ".join(cmd), flush=True)
    try:
        r = subprocess.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("[err] 超时，跳过该阶段")
        return False
    if r.returncode != 0:
        print(f"[err] 退出码 {r.returncode}，跳过该阶段")
        return False
    return True


def _build_steps(a, cur: str, tmp_dir: str, sr_model: str,
                 rife_ckpt: str) -> tuple[list[list[str]], str]:
    """编排 GPU 阶段。顺序固定：先 RIFE（在低分辨率上补帧最省显存/时间），
    后 ESRGAN 超分 —— Video2X 文档对「高动态场景」给出的推荐顺序。

    返回 (命令列表, 最后一步产物路径)。
    """
    steps: list[list[str]] = []
    if not a.no_rife:
        step1 = os.path.join(tmp_dir, "01_rife.mp4")
        steps.append([sys.executable, os.path.join(HERE, "rife_interp.py"),
                      cur, step1, "--multiplier", str(a.multiplier),
                      "--ckpt", rife_ckpt, "--api", a.api])
        cur = step1
    if not a.no_sr:
        step2 = os.path.join(tmp_dir, "02_sr.mp4")
        # 中间片用低 crf（12）避免二次编码累积损失；最终档位在最后一步生效
        steps.append([sys.executable, os.path.join(HERE, "upscale_video.py"),
                      cur, step2, "--full", "--chunk", str(a.chunk),
                      "--model", sr_model, "--crf", "12", "--api", a.api])
        cur = step2
    return steps, cur


def _final_cmd(a, ff: str, cur: str, fps: float, profile: str) -> list[str]:
    """最终封装命令：视频取处理片；音轨优先取处理片，处理片无音轨时回退源片。"""
    audio_src = cur if has_audio(cur, ff) else os.path.abspath(a.src)
    cmd = [ff, "-y", "-i", cur]
    if os.path.abspath(audio_src) != os.path.abspath(cur):
        cmd += ["-i", audio_src]
        maps = ["-map", "0:v:0", "-map", "1:a:0?"]
    else:
        maps = ["-map", "0:v:0", "-map", "0:a:0?"]
    if a.denoise:
        cmd += ["-vf", "hqdn3d=1.5:1.0:6:4.5"]
    return cmd + maps + enc.quality_args(profile, fps, loudnorm=a.loudnorm) + ["-shortest", a.dst]


def _resolve_settings(a) -> tuple[str, str]:
    """推断 (内容类型, 编码档位)。显式参数优先，其次猜画风。"""
    kind = (a.kind or "").strip().lower()
    if not kind:
        kind = "anime" if _style_looks_anime() else "real"
    profile = a.profile or ("anime" if kind == "anime" else "standard")
    return kind, profile


def _resolve_models(a, kind: str) -> tuple[str, str]:
    """按 ComfyUI **实际扫到的权重** 定超分模型与 RIFE 权重，并把回退原因打出来。

    不做这步会出现「以为超分了、其实权重缺失被静默跳过」的假成功。
    """
    from agent import comfy_models as cm
    sr, sr_note = cm.pick_sr_model(kind, api=a.api, explicit=a.sr_model)
    rife, rife_note = cm.pick_rife_ckpt(api=a.api, explicit=a.rife_ckpt)
    for note in (sr_note, rife_note):
        if note:
            print("[warn] " + note, flush=True)
    return sr, rife


def _apply_comfy_gate(a) -> None:
    """ComfyUI 不可用时降级为只重编码；--strict 则直接报错。"""
    if a.no_rife and a.no_sr:
        return
    if comfy_ready(a.api):
        return
    msg = (f"ComfyUI 未就绪（{a.api}），将只做重编码。"
           "需插帧/超分请先启动 ComfyUI 并加载 ComfyUI-Frame-Interpolation。")
    if a.strict:
        raise SystemExit("[err] " + msg)
    print("[warn] " + msg, flush=True)
    a.no_rife = a.no_sr = True


def main() -> int:
    ap = argparse.ArgumentParser(description="一键画质增强（RIFE → ESRGAN → 高质量编码）")
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--profile", default="", choices=enc.profiles(),
                    help="编码档位；默认按 --kind 推断（anime→anime，否则 standard）")
    ap.add_argument("--kind", default="", choices=["anime", "real"],
                    help="内容类型：决定超分模型与 tune；默认从 config.project.style 猜")
    ap.add_argument("--sr-model", default="", help="超分模型文件名（覆盖 --kind 推荐）")
    ap.add_argument("--rife-ckpt", default="", help="RIFE 权重文件名（默认取本机最新版本）")
    ap.add_argument("--multiplier", type=int, default=2, help="RIFE 插帧倍数（默认 2）")
    ap.add_argument("--chunk", type=int, default=60, help="超分分段帧数（防 OOM）")
    ap.add_argument("--no-rife", action="store_true", help="跳过插帧")
    ap.add_argument("--no-sr", action="store_true", help="跳过超分")
    ap.add_argument("--denoise", action="store_true", help="轻度降噪（hqdn3d），抑制扩散噪点")
    ap.add_argument("--loudnorm", action="store_true",
                    help="响度归一化到 -16 LUFS（EBU R128）；多集发布时听感一致")
    ap.add_argument("--fps", type=float, default=0.0, help="输出帧率；0=沿用处理片")
    ap.add_argument("--api", default=API_DEFAULT)
    ap.add_argument("--strict", action="store_true", help="ComfyUI 不可用时报错退出")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    a = ap.parse_args()

    if not os.path.exists(a.src):
        raise SystemExit(f"源文件不存在: {a.src}")
    ff = enc.ffmpeg_exe()
    if not ff:
        raise SystemExit("未找到 ffmpeg（设置 FFMPEG 环境变量或安装 imageio-ffmpeg）")

    kind, profile = _resolve_settings(a)
    _apply_comfy_gate(a)
    sr_model, rife_ckpt = _resolve_models(a, kind)

    src_fps = enc.probe_fps(a.src, ff=ff)
    out_fps = a.fps if a.fps > 0 else 0.0
    print(f"[cfg] kind={kind} profile={profile} sr_model={sr_model} "
          f"rife_ckpt={rife_ckpt} src={src_fps:.3f}fps "
          f"rife={'关' if a.no_rife else str(a.multiplier) + 'x'} "
          f"sr={'关' if a.no_sr else '开'}", flush=True)

    out_dir = os.path.dirname(os.path.abspath(a.dst)) or "."
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = os.path.join(out_dir, ".enhance_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    cur = os.path.abspath(a.src)
    steps, cur = _build_steps(a, cur, tmp_dir, sr_model, rife_ckpt)

    for cmd in steps:
        if a.dry_run:
            print("[dry] " + " ".join(cmd))
            continue
        if not _run(cmd):
            print("[warn] 阶段失败，回退到上一阶段产物继续封装")
            cur = os.path.abspath(a.src) if cmd is steps[0] else cur
            break

    # ---- 最终高质量封装 ----
    fps = out_fps or enc.probe_fps(cur, fallback=src_fps, ff=ff)
    cmd = _final_cmd(a, ff, cur, fps, profile)
    if a.dry_run:
        print("[dry] " + " ".join(cmd))
        return 0
    if not _run(cmd):
        return 1
    size = os.path.getsize(a.dst) / 2 ** 20
    print(f"[OK] {a.dst}  {size:.2f}MB  {fps:.3f}fps  profile={profile}", flush=True)
    return 0


def _style_looks_anime() -> bool:
    """从 config.project.style 猜内容类型（动漫关键词命中即按动漫处理）。

    读不到配置就按真人处理（保守，不阻断）。
    """
    keys = ("anime", "动漫", "cel-shaded", "二次元", "赛璐璐")
    for rel in ("config.yaml", "config.example.yaml"):
        path = os.path.join(HERE, rel)
        if not os.path.exists(path):
            continue
        try:
            import yaml  # type: ignore
            with open(path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            style = str(((cfg.get("project") or {}).get("style") or "")).lower()
            if style:
                return any(k in style for k in keys)
        except Exception:
            continue
    return False


if __name__ == "__main__":
    raise SystemExit(main())
