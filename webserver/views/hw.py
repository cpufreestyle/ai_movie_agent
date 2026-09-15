"""部署 · 硬件档位（WebUI 设置页的下拉框数据源）。

只提供 **读取**：`GET /api/hw`（可选 `?detect=1` 顺带探测本机硬件）。
保存仍然走既有的 `POST /api/config`（前端取回 config → 改 `hw_tier` / `auto_hardware`
→ 回写），这样配置写盘、缓存失效、agent 重建那套逻辑只有一份，不重复实现。
"""
from __future__ import annotations

from flask import Blueprint, request

from ..services import hw
from ..state import json_resp

bp = Blueprint("hw", __name__)

_TRUTHY = ("1", "true", "yes", "on")


@bp.route("/api/hw", methods=["GET"])
def api_hw():
    """硬件档位选项 + 当前值 + 各档位覆盖预览。

    `?detect=1` 时附带本机硬件探测结果（Windows 下会调 PowerShell，故默认不探）。
    """
    want = str(request.args.get("detect") or "").strip().lower() in _TRUTHY
    try:
        return json_resp(hw.payload(with_detect=want))
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)
