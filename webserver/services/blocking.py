"""白模模块业务逻辑：outputs/blocking 下的产物扫描与媒体类型判定。"""
from __future__ import annotations

import glob
import os

from ..state import WORKDIR

_BLOCK_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mp4": "video/mp4"}
_BLOCK_BASE = os.path.join(WORKDIR, "blocking")


def blocking_artifacts(limit: int = 80) -> list:
    """outputs/blocking 下的白模产物（图/视频），按修改时间倒序。"""
    base = _BLOCK_BASE
    items = []
    if os.path.isdir(base):
        for path in glob.glob(os.path.join(base, "**", "*"), recursive=True):
            if not os.path.isfile(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in _BLOCK_MEDIA:
                continue
            items.append({
                "name": os.path.relpath(path, base).replace("\\", "/"),
                "kind": "video" if ext == ".mp4" else "image",
                "size_kb": round(os.path.getsize(path) / 1024, 1),
                "mtime": os.path.getmtime(path),
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def media_mime(path: str) -> str | None:
    """按扩展名给出媒体 MIME；不支持的返回 None。"""
    return _BLOCK_MEDIA.get(os.path.splitext(path)[1].lower())


__all__ = ["blocking_artifacts", "media_mime"]
