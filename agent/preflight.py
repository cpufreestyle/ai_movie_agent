"""投稿前体检：把成片交给 biliup 之前，按 B 站投稿规范 + 项目约定做静态校验。

不依赖 biliup / ffmpeg 也能跑：时长/封面尺寸用 ffprobe 探测，缺失时降级为警告。
纯新增模块，不修改任何既有流程。
"""
from __future__ import annotations

import json
import os
import re
import subprocess

# B 站投稿常见约束（超出部分以官方为准）
BILI = {
    "title_max": 80,                     # 标题最长 80 字
    "tag_max": 10,                       # 标签最多 10 个
    "tag_len_max": 20,                   # 单个标签最长 20 字
    "dynamic_max": 233,                  # 动态最长 233 字
    "cover_w": 1146, "cover_h": 717,     # 投稿封面建议尺寸
    "video_size_max": 8 * 1024 ** 3,     # 单文件 8GB 上传上限
    "video_dur_warn": 900,               # 普通账号 15 分钟提醒（需认证才更长）
    "video_dur_error": 14400,            # 4 小时硬上限
}


def _probe(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except Exception:
        return None


def _probe_duration(video):
    """返回秒数(float)或 None（ffprobe 缺失/失败）。"""
    s = _probe(["ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "default=nw=1:nk=1", video])
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _probe_wh(image):
    """返回 (w, h) 或 None。"""
    s = _probe(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of",
                "csv=s=x", image])
    if not s or "x" not in s:
        return None
    try:
        w, h = s.split("x")
        return int(w), int(h)
    except ValueError:
        return None


def check(video=None, *, title=None, tags=None, cover=None,
          desc=None, dynamic=None, workdir=None):
    """对投稿素材做静态体检。

    返回 {ok, errors:[], warnings:[]}。ok=False 表示有阻断性问题，建议先修。
    """
    errors, warnings = [], []

    if video:
        if not os.path.exists(video):
            errors.append(f"视频文件不存在: {video}")
        else:
            size = os.path.getsize(video)
            if size == 0:
                errors.append("视频文件为空(0 字节)")
            elif size > BILI["video_size_max"]:
                errors.append(f"视频超过 8GB 上传上限({size / 1024 ** 3:.1f}GB)")
            dur = _probe_duration(video)
            if dur is None:
                warnings.append("无法探测视频时长（ffprobe 未安装或被防火墙拦截）")
            else:
                if dur > BILI["video_dur_error"]:
                    errors.append(f"视频超过 4 小时硬上限({int(dur)}s)")
                elif dur > BILI["video_dur_warn"]:
                    warnings.append(
                        f"时长 {int(dur)}s 超过 15 分钟，需 B 站账号认证才能投")

    if title is not None:
        if not title.strip():
            errors.append("标题为空")
        else:
            if len(title) > BILI["title_max"]:
                errors.append(f"标题超长({len(title)}>{BILI['title_max']}字)")
            if not re.search(r"第[0-9一二三四五六七八九十百]+集", title):
                warnings.append("标题缺少集数标记(第N集)")

    if tags is not None:
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if len(tags) > BILI["tag_max"]:
            errors.append(f"标签数 {len(tags)} 超过上限 {BILI['tag_max']}")
        for t in tags:
            if len(t) > BILI["tag_len_max"]:
                warnings.append(f"标签「{t}」超过 {BILI['tag_len_max']} 字")

    if dynamic and len(dynamic) > BILI["dynamic_max"]:
        warnings.append(f"动态超过 {BILI['dynamic_max']} 字(投稿时会被截断)")

    if cover:
        if not os.path.exists(cover):
            errors.append(f"封面文件不存在: {cover}")
        else:
            wh = _probe_wh(cover)
            if wh is None:
                warnings.append("无法探测封面尺寸（ffprobe 缺失）")
            elif wh != (BILI["cover_w"], BILI["cover_h"]):
                warnings.append(
                    f"封面非建议尺寸 1146x717，当前 {wh[0]}x{wh[1]}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def check_from_outputs(workdir=None, video=None, title=None, tags=None,
                       cover=None, dynamic=None):
    """从 outputs 目录读取成片 + state.json 自动组装体检输入。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workdir = workdir or os.path.join(root, "outputs")
    if video is None:
        video = os.path.join(workdir, "movie_final.mp4")
        if not os.path.exists(video):
            video = os.path.join(workdir, "scenes", "concept_demo.mp4")
    state = {}
    sp = os.path.join(workdir, "state.json")
    if os.path.exists(sp):
        try:
            state = json.load(open(sp, encoding="utf-8"))
        except Exception:
            state = {}
    if title is None:
        ep = state.get("scene_count", 1) or 1
        film = state.get("title", "未命名")
        title = f"{film} · 第{ep}集"
    if tags is None:
        tags = state.get("publish_tags") or ["AI电影", "AIGC"]
    if cover is None:
        cover = os.path.join(workdir, "scenes", "concept_cover.png")
    return check(video, title=title, tags=tags, cover=cover, dynamic=dynamic)
