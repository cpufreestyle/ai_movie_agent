"""成片：列表 / 播放 / 分镜编辑 / 重新生成 / 模型下拉框。"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import requests
from flask import Blueprint, request, send_file

from ..services.pipeline import (
    film_candidates,
    load_storyboard,
    storyboard_from_bible,
)
from ..state import HERE, STORYBOARD_PATH, WORKDIR, _state, json_resp, load_config, run_in_background

bp = Blueprint("film", __name__)


@bp.route("/api/films")
def api_films():
    return json_resp({"films": film_candidates()})


@bp.route("/api/film/play")
def api_film_play():
    """播放 outputs 下任意成片（支持子目录相对路径，带穿越防护）。conditional 支持拖动进度。"""
    name = request.args.get("name", "")
    if not name.endswith(".mp4"):
        return json_resp({"error": "仅支持 mp4"}, status=400)
    path = os.path.normpath(os.path.join(WORKDIR, name))
    # 防路径穿越：必须落在 WORKDIR 内
    if path != WORKDIR and not path.startswith(WORKDIR + os.sep):
        return json_resp({"error": "非法路径"}, status=400)
    if not os.path.isfile(path):
        return json_resp({"error": f"影片不存在: {name}"}, status=404)
    return send_file(path, mimetype="video/mp4", conditional=True)


@bp.route("/api/storyboard", methods=["GET"])
def api_storyboard_get():
    try:
        return json_resp(load_storyboard())
    except Exception as e:  # noqa: BLE001
        return json_resp({"error": str(e)}, status=500)


@bp.route("/api/storyboard", methods=["POST"])
def api_storyboard_post():
    body = request.get_json(force=True, silent=True) or {}
    shots = body.get("shots")
    narration = body.get("narration")
    if not isinstance(shots, list) or not all(isinstance(x, str) for x in shots) or not shots:
        return json_resp({"ok": False, "error": "shots 必须是非空字符串数组"}, status=400)
    if not isinstance(narration, list) or not all(isinstance(x, str) for x in narration):
        return json_resp({"ok": False, "error": "narration 必须是字符串数组"}, status=400)
    try:
        with open(STORYBOARD_PATH, "w", encoding="utf-8") as f:
            json.dump({"shots": shots, "narration": narration},
                      f, ensure_ascii=False, indent=2)
        return json_resp({"ok": True, "shots": len(shots), "narration": len(narration)})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@bp.route("/api/storyboard/generate", methods=["POST"])
def api_storyboard_generate():
    """用当前企划(bible)+素材，让 LLM 扩写成 18 镜英文分镜 + 9 段中文解说，写入 storyboard.json。"""
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)

    run_in_background(storyboard_from_bible)
    return json_resp({"ok": True, "msg": "已启动（LLM 生成分镜，后台运行，看下方日志）"})


@bp.route("/api/models")
def api_models():
    """列出 Ollama 已装模型（实时查询 /api/tags），用于配置弹窗的模型下拉框。"""
    base_url = request.args.get("base_url") or (load_config().get("llm", {}).get("base_url"))
    if not base_url:
        return json_resp({"ok": False, "error": "未配置 base_url"})
    tags_url = base_url.rstrip("/").replace("/v1", "") + "/api/tags"
    try:
        r = requests.get(tags_url, timeout=8)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
        return json_resp({"ok": True, "models": models, "base_url": base_url})
    except Exception as e:
        return json_resp({"ok": False, "error": f"无法获取模型列表（{tags_url}）：{e}"})


@bp.route("/api/film/render", methods=["POST"])
def api_film_render():
    """按（可能被改过的）分镜重新生成全片，并重新合成解说+字幕。"""
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    only = str(body.get("only") or "all")   # all | shots | narration

    # LTX-2.3 入口已下线，视频链路固定走 LTX-2.5
    steps = []
    if only in ("all", "shots"):
        steps.append(("生成分镜并拼接(LTX-2.5)", "run_ltx25_multishot.py", []))
    if only in ("all", "narration"):
        steps.append(("解说配音+字幕(LTX-2.5)", "make_narration.py",
                      ["--film", "outputs/ltx25_film.mp4",
                       "--out", "outputs/ep3_vo.mp4", "--auto-dur"]))

    def _job():
        result = {"ok": True, "steps": []}
        for title, script, args in steps:
            print(f"\n=== {title}：{script} ===")
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            r = subprocess.run([sys.executable, "-u", script, *args], cwd=HERE,
                               capture_output=True, text=True, env=env,
                               encoding="utf-8", errors="replace")
            if r.stdout:
                print(r.stdout)
            if r.stderr:
                print(r.stderr[-4000:])
            result["steps"].append({"step": title, "code": r.returncode})
            if r.returncode != 0:
                result["ok"] = False
                result["error"] = f"{title} 失败（code={r.returncode}）"
                print("=== 中断 ===")
                return result
        print("=== 全部完成 ===")
        return result

    run_in_background(_job)
    return json_resp({"ok": True, "msg": "已启动重新生成（后台运行，看下方日志）"})
