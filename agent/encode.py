"""统一成片编码档位（参照 MoneyPrinterTurbo / Video2X 的成片输出约定）。

背景：项目里 ffmpeg 的编码参数散落在 `run_series.py`（crf 18/medium）、
`make_narration.py`（crf 18/medium）、`anime_redraw.py`（crf 17）三处，
且**都没有** `-movflags +faststart`（上传 B 站/网页播放要反复缓冲）和
GOP/色彩元数据。本模块把这些收敛成一处，供所有出片路径复用。

档位设计依据（x264 官方 + 开源项目惯例）：
  - crf      ：视觉无损区间 16~20；低于 16 体积暴涨、收益递减。
  - preset   ：越慢压缩率越好；成片用 slow 划算（一次性成本）。
  - tune     ：`animation` 针对赛璐璐/线条（保留平涂色块与描边，抑制环形振铃），
               `film` 针对真人颗粒感。动漫片用 animation 是开源社区共识。
  - profile/level：high + 4.2，保证老设备/网页端兼容。
  - GOP      ：`-g` = 2 秒一个关键帧，兼顾拖动Seek与压缩率。
  - pix_fmt  ：yuv420p，8bit 兼容性最好（不然部分播放器黑屏）。
  - faststart：moov 前置，边下边播必备。
"""
from __future__ import annotations

import os
import re
import subprocess

# 档位 -> 编码参数。crf/preset 为主，tune 仅在有明确内容类型时开启。
ENCODE_PROFILES: dict[str, dict] = {
    # 草稿/预览：快，够看清就够
    "draft": {"crf": 23, "preset": "veryfast", "tune": "", "abitrate": "128k"},
    # 常规成片：与项目既有行为一致（crf 18 / medium），补齐 faststart + GOP
    "standard": {"crf": 18, "preset": "medium", "tune": "", "abitrate": "192k"},
    # 高质量真人：慢编码 + film tune
    "high": {"crf": 16, "preset": "slow", "tune": "film", "abitrate": "192k"},
    # 动漫/赛璐璐：animation tune 保线条，是本仓库默认画风
    "anime": {"crf": 16, "preset": "slow", "tune": "animation", "abitrate": "192k"},
}

DEFAULT_PROFILE = "standard"

# EBU R128 响度归一化：I=-16 LUFS 是主流平台（B 站 / YouTube）的常见落点，
# TP=-1.5 dBFS 防真峰值削波，LRA=11 保留动态范围。
LOUDNORM_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"

# 超分模型推荐（参照 Video2X 的「按内容选引擎」表：动漫走动漫专用模型，
# 真人走通用模型）。当前管线走 ComfyUI 的 UpscaleModelLoader，故这里都是
# 可放进 models/upscale_models 的权重文件名。
SR_MODELS: dict[str, list[str]] = {
    "anime": [
        "RealESRGAN_x4plus_anime_6B.pth",   # 动漫首选：线条干净、无色斑
        "4x-UltraSharp.pth",                # 兜底
    ],
    "real": [
        "4x-UltraSharp.pth",                # 真人首选：通用、肤色自然
        "RealESRGAN_x4plus.pth",
    ],
}

DEFAULT_SR_MODEL = "4x-UltraSharp.pth"


def profiles() -> list[str]:
    """可用档位名（顺序稳定，便于 CLI --help / WebUI 下拉）。"""
    return list(ENCODE_PROFILES)


def resolve(name: str) -> dict:
    """取档位参数；未知档位回退 standard（不抛错，避免配置写错就崩管线）。"""
    return ENCODE_PROFILES.get((name or "").strip().lower(), ENCODE_PROFILES[DEFAULT_PROFILE])


def sr_model_for(kind: str, explicit: str = "") -> str:
    """按内容类型推荐超分模型；explicit 优先。未知类型回退通用模型。"""
    if explicit:
        return explicit
    return SR_MODELS.get((kind or "").strip().lower(), SR_MODELS["real"])[0]


def quality_args(profile: str = DEFAULT_PROFILE, fps: float = 0.0,
                 width: int = 0, height: int = 0, loudnorm: bool = False) -> list[str]:
    """生成 libx264 + aac 的高质量编码参数（不含 -i / 输出路径）。

    fps 用于算 GOP（2 秒一个关键帧）；width/height 用于放宽 level；
    loudnorm 开启 EBU R128 响度归一化（发布用：B 站/YouTube 会按 -14~-16 LUFS
    再归一化，各集响度不一致会被压得忽大忽小；开启后多集听感一致）。
    """
    p = resolve(profile)
    args = ["-c:v", "libx264", "-crf", str(p["crf"]), "-preset", p["preset"]]
    if p["tune"]:
        args += ["-tune", p["tune"]]
    args += ["-pix_fmt", "yuv420p", "-profile:v", "high"]
    # level：1080p 及以下 4.2（兼容性/硬解友好）；超过则放宽到 5.1
    lvl = "5.1" if (width > 2048 or height > 1080) else "4.2"
    args += ["-level:v", lvl]
    if fps and fps > 0:
        args += ["-g", str(int(round(fps * 2))), "-keyint_min", str(int(round(fps)))]
    args += ["-c:a", "aac", "-b:a", p["abitrate"], "-ar", "48000", "-ac", "2"]
    if loudnorm:
        args += ["-af", LOUDNORM_FILTER]
    args += ["-movflags", "+faststart"]
    return args


def build_encode_cmd(ff: str, src: str, out: str, profile: str = DEFAULT_PROFILE,
                     fps: float = 0.0, denoise: bool = False,
                     extra_vf: str = "") -> list[str]:
    """单输入重编码命令（保留音轨，仅改视频编码与封装）。"""
    vf = []
    if denoise:
        # hqdn3d：空域+时域降噪，抑制扩散模型常见的细密噪点（_CPU 轻量_）
        vf.append("hqdn3d=1.5:1.0:6:4.5")
    if extra_vf:
        vf.append(extra_vf)
    cmd = [ff, "-y", "-i", src]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += quality_args(profile, fps)
    cmd += [out]
    return cmd


def probe_fps(path: str, fallback: float = 24.0, ff: str = "") -> float:
    """读视频帧率；读不到回退 fallback。imageio_ffmpeg 无 ffprobe，故解析 -i 输出。"""
    exe = ff or ffmpeg_exe()
    if not exe or not os.path.exists(path):
        return fallback
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", path],
                           capture_output=True, text=True, errors="ignore")
        m = re.search(r"(\d+(?:\.\d+)?)\s*fps", r.stderr or "")
        if m:
            v = float(m.group(1))
            if v > 0:
                return v
    except Exception:
        pass
    return fallback


def ffmpeg_exe(explicit: str = "") -> str:
    """定位 ffmpeg；优先复用 comfy_paths（FFMPEG env > PATH > imageio-ffmpeg）。"""
    try:
        from comfy_paths import ffmpeg_exe as _f
        return _f(explicit)
    except Exception:
        import shutil
        return shutil.which("ffmpeg") or "ffmpeg"
