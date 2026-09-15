"""WebUI 后端分层：state（共享状态 / 基础设施） + services（业务逻辑） + views（Blueprint）。

`webui.py` 只负责组装 app 与启动，本包不持有任何 Flask app 实例。

依赖方向严格单向，避免循环 import：

    webui.py  ->  webserver.views  ->  webserver.services  ->  webserver.state
"""
from __future__ import annotations

from .views import register_blueprints

__all__ = ["register_blueprints"]
