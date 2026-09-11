"""时间轴：镜头顺序 / 启停 / 裁剪的读取与拼接导出（WebUI 时间轴 + cli timeline 共用）。

镜头来自 series_manifest.json（man[key]=视频路径，见 agent/ab.load_manifest），保存于
outputs/timeline.json：{ shots:[{key,label,enabled,in_point,out_point,order}], updated }。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
DEFAULT_WORKDIR = os.path.join(PROJECT_ROOT, "outputs")


def resolve_workdir(workdir=None) -> str:
    return workdir or DEFAULT_WORKDIR


def build_timeline(workdir=None) -> dict:
    """从 series_manifest.json 构造初始时间轴（全部启用、未裁剪）。

    没有 manifest 时回退到 storyboard.json 的镜头数；都没有则空时间轴。
    """
    wd = resolve_workdir(workdir)
    man_path = os.path.join(wd, "series_manifest.json")
    keys = []
    if os.path.exists(man_path):
        try:
            keys = list(json.load(open(man_path, encoding="utf-8")).keys())
        except Exception:
            keys = []
    if not keys:
        sb_path = os.path.join(wd, "storyboard.json")
        try:
            sb = json.load(open(sb_path, encoding="utf-8")) if os.path.exists(sb_path) else {}
            keys = [f"shot{i + 1}" for i in range(len(sb.get("shots", [])))]
        except Exception:
            keys = []
    shots = [{"key": k, "label": k, "enabled": True,
              "in_point": None, "out_point": None, "order": i}
             for i, k in enumerate(keys)]
    return {"shots": shots, "updated": None}


def timeline_path(workdir=None) -> str:
    return os.path.join(resolve_workdir(workdir), "timeline.json")


def load_timeline(workdir=None) -> dict:
    """读 timeline.json；不存在/损坏则构建。"""
    p = timeline_path(workdir)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return build_timeline(workdir)
    return build_timeline(workdir)


def save_timeline(workdir, data: dict) -> str:
    p = timeline_path(workdir)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


def ordered_shots(timeline: dict) -> list:
    return sorted(timeline.get("shots", []), key=lambda s: s.get("order", 0))


def resolve_clip(key: str, workdir=None) -> str | None:
    """按 shot key 解析视频路径：优先 series_manifest 记录，其次常见位置。"""
    from . import ab
    wd = resolve_workdir(workdir)
    man = ab.load_manifest(wd)
    if key in man and isinstance(man[key], str) and os.path.exists(man[key]):
        return man[key]
    for c in (os.path.join(wd, "clips", f"{key}.mp4"),
              os.path.join(wd, "clips", f"{key}.mov"),
              os.path.join(wd, f"{key}.mp4"),
              os.path.join(wd, f"{key}.mov")):
        if os.path.exists(c):
            return c
    return None


def enabled_clips(timeline: dict, workdir=None) -> list:
    """返回 [(视频路径, in_point, out_point)]，仅启用镜头、按 order。"""
    wd = resolve_workdir(workdir)
    out = []
    for s in ordered_shots(timeline):
        if not s.get("enabled", True):
            continue
        path = resolve_clip(s.get("key", ""), wd)
        if not path:
            continue
        out.append((path, s.get("in_point"), s.get("out_point")))
    return out


def export_concat(timeline: dict, workdir=None,
                  out_path: str = "movie_timeline.mp4") -> str:
    """写出 ffmpeg concat 列表(.txt) 与拼接命令，返回命令字符串。

    用 concat demuxer + inpoint/outpoint 做裁剪，无需先转码（-c copy）。
    """
    wd = resolve_workdir(workdir)
    clips = enabled_clips(timeline, wd)
    if not clips:
        raise ValueError("时间轴中没有可拼接的启用镜头（series_manifest/clips 无匹配视频）")
    base = os.path.dirname(os.path.abspath(out_path))
    lines = []
    for path, ipt, opt in clips:
        rel = os.path.relpath(path, base)
        line = f"file '{rel}'"
        if ipt is not None:
            line += f"\ninpoint {float(ipt)}"
        if opt is not None:
            line += f"\noutpoint {float(opt)}"
        lines.append(line)
    txt = out_path + ".concat.txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return f'ffmpeg -y -f concat -safe 0 -i "{txt}" -c copy "{out_path}"'
