"""自动配乐 + 旁白 ducking：给成片自动选 BGM，并用 ffmpeg 侧链压缩做旁白避让。

纯新增、可选能力：ffmpeg 缺失或 BGM 目录为空时优雅返回错误，不阻断管线。
publisher.publish_concept 的 --bgm 可调用本模块做自动选曲 + 侧链混音。
"""
from __future__ import annotations

import os
import shutil
import subprocess


def is_ready(ffmpeg="ffmpeg"):
    return shutil.which(ffmpeg) is not None


def list_bgm(bgm_dir):
    if not bgm_dir or not os.path.isdir(bgm_dir):
        return []
    exts = (".mp3", ".wav", ".ogg", ".flac", ".m4a")
    return sorted(
        os.path.join(bgm_dir, f) for f in os.listdir(bgm_dir)
        if f.lower().endswith(exts))


def _duration(path, ffprobe="ffprobe"):
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                              "format=duration", "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        return float(out) if out else None
    except Exception:
        return None


def select_bgm(bgm_dir, video_duration=None, mood=None):
    """自动选曲：优先选时长>=视频时长 60% 且最接近视频时长的；否则选最长的（将循环）。

    确定性：同输入同输出（先按 mood 过滤，再按文件名排序取首个匹配），便于可复现。
    """
    cands = list_bgm(bgm_dir)
    if not cands:
        return None
    if mood:
        tagged = [c for c in cands if mood.lower() in os.path.basename(c).lower()]
        if tagged:
            cands = tagged
    if video_duration:
        fit = [c for c in cands if (_duration(c) or 0) >= video_duration * 0.6]
        if fit:
            return min(fit, key=lambda c: abs((_duration(c) or 0) - video_duration))
    return max(cands, key=lambda c: _duration(c) or 0)


def duck_mix(video, bgm, out, narration=None, ffmpeg="ffmpeg",
             bgm_gain=-20, duck_threshold=-28, ratio=4,
             attack=20, release=200, makeup=0):
    """把 BGM 混入视频；旁白出现处自动压低 BGM（侧链压缩 ducking）。

    返回 {ok, out} 或 {ok:False, error}。
    """
    if not is_ready(ffmpeg):
        return {"ok": False, "error": f"未找到 ffmpeg（{ffmpeg}），请先安装 ffmpeg"}
    for p in (video, bgm):
        if not os.path.exists(p):
            return {"ok": False, "error": f"文件不存在: {p}"}
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    inputs = ["-i", video, "-i", bgm]
    if narration:
        if not os.path.exists(narration):
            return {"ok": False, "error": f"旁白文件不存在: {narration}"}
        inputs += ["-i", narration]

    if narration:
        # [主=bgm][侧链=旁白]：旁白响度超阈值时压低 bgm；makeup 补偿整体增益
        filtergraph = (
            f"[1:a]volume={bgm_gain}dB[bgm];"
            f"[2:a]volume=0dB[narr];"
            f"[bgm][narr]sidechaincompress="
            f"threshold={duck_threshold}dB:ratio={ratio}:"
            f"attack={attack}ms:release={release}ms:makeup={makeup}dB[bgm_d];"
            f"[0:a][bgm_d][narr]amix=inputs=3:normalize=0[outa]"
        )
    else:
        filtergraph = (
            f"[1:a]volume={bgm_gain}dB[bgm];"
            f"[0:a][bgm]amix=inputs=2:normalize=0[outa]"
        )

    cmd = [ffmpeg, "-y", *inputs, "-filter_complex", filtergraph,
           "-map", "0:v", "-map", "[outa]", "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", out]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[-500:]
        return {"ok": False, "error": f"ffmpeg 失败: {msg}"}
    return {"ok": True, "out": out}
