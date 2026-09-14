"""白模模块：Blender blocking → H3 Fun Control。"""
from __future__ import annotations

import os

from flask import Blueprint, request, send_file

from ..services.blocking import blocking_artifacts, media_mime
from ..state import HERE, WORKDIR, _state, get_agent, json_resp, load_config, run_in_background

bp = Blueprint("blocking", __name__)


@bp.route("/api/blocking")
def api_blocking():
    try:
        agent = get_agent()
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)
    blk = agent.blocking
    cfg = load_config().get("blender", {}) or {}
    return json_resp({
        "enabled": bool(blk.enabled),
        "ready": bool(blk.is_ready()),
        "host": blk.host,
        "port": blk.port,
        "out_dir": os.path.relpath(blk.out_dir, HERE).replace("\\", "/"),
        "engine": blk.engine,
        "samples": blk.samples,
        "width": blk.width,
        "height": blk.height,
        "anim_frames": blk.anim_frames,
        "fc_enabled": bool(blk.fc_enabled),
        "fc_walk": blk.fc_walk,
        "fc_frames": blk.fc_frames,
        "fc_size": f"{blk.fc_w}×{blk.fc_h}",
        "switches": {k: bool(cfg.get(k, False)) for k in (
            "use_as_i2v_start", "use_as_ref_images", "use_as_ref_video", "use_as_fun_control")},
        "artifacts": blocking_artifacts(),
    })


@bp.route("/api/blocking/parse", methods=["POST"])
def api_blocking_parse():
    body = request.get_json(force=True, silent=True) or {}
    try:
        agent = get_agent()
        return json_resp({"ok": True, "spec": agent.blocking.parse_spec(str(body.get("beat") or ""))})
    except RuntimeError as e:
        return json_resp({"ok": False, "error": str(e)})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@bp.route("/api/blocking/run", methods=["POST"])
def api_blocking_run():
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    beat = str(body.get("beat") or "").strip()
    mode = str(body.get("mode") or "block")
    frames = body.get("frames")

    def _job():
        agent = get_agent()
        blk = agent.blocking
        if not blk.is_ready():
            raise RuntimeError("Blender 未就绪：请安装 Blender + BlenderMCP 并启动 MCP Server(9876)")
        spec = blk.parse_spec(beat)
        print(f"[blocking] spec={spec}")
        if mode == "anim":
            d = os.path.join(blk.out_dir, "anim")
            blk.render_animation(spec, d, int(frames) if frames else blk.anim_frames)
            blk.export_anim_video(d)
            return {"ok": True, "mode": mode, "out": os.path.relpath(d, HERE)}
        if mode == "fc":
            d = os.path.join(blk.out_dir, "fc")
            blk.render_fc_anim(spec, d, int(frames) if frames else blk.fc_frames)
            blk.export_fc_video(d)
            return {"ok": True, "mode": mode, "out": os.path.relpath(d, HERE)}
        if mode == "previs":
            out = blk.render_previs(spec, os.path.join(blk.out_dir, "preview.png"))
            return {"ok": True, "mode": mode, "out": os.path.relpath(out, HERE)}
        res = blk.render_block(spec, blk.out_dir)
        return {"ok": True, "mode": mode,
                "out": {k: os.path.relpath(v, HERE) for k, v in res.items()}}

    run_in_background(_job)
    return json_resp({"ok": True, "msg": f"已启动白模渲染（{mode}），看下方日志"})


@bp.route("/api/blocking/file")
def api_blocking_file():
    name = request.args.get("name", "")
    base = os.path.join(WORKDIR, "blocking")
    path = os.path.normpath(os.path.join(base, name))
    if path != base and not path.startswith(base + os.sep):
        return json_resp({"error": "非法路径"}, status=400)
    if not os.path.isfile(path):
        return json_resp({"error": "文件不存在"}, status=404)
    mime = media_mime(path)
    if not mime:
        return json_resp({"error": "不支持的媒体类型"}, status=400)
    return send_file(path, mimetype=mime, conditional=True)
