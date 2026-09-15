"""按域拆分的 Blueprint 注册入口。

URL 规则与原 webui.py 完全一致（无 url_prefix）；endpoint 名会带上 blueprint 前缀
（如 `core.api_logs`），仓库内没有任何 url_for / endpoint 反向引用，故无影响。
"""
from __future__ import annotations

from . import bili, blocking, core, film, media, pipeline, run

# 顺序无关（各域 URL 不重叠），仅按可读性排列
BLUEPRINTS = (core.bp, run.bp, pipeline.bp, bili.bp, media.bp, blocking.bp, film.bp)


def register_blueprints(app) -> None:
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)


__all__ = ["register_blueprints"]
