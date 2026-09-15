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
    normalize_shot_indices,
    storyboard_from_bible,
    storyboard_shot_count,
    validate_storyboard,
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

    errors, warnings = validate_storyboard(shots, narration)
    if errors:
        return json_resp({"ok": False, "error": "；".join(errors),
                          "errors": errors, "warnings": warnings}, status=400)
    try:
        with open(STORYBOARD_PATH, "w", encoding="utf-8") as f:
            json.dump({"shots": shots, "narration": narration},
                      f, ensure_ascii=False, indent=2)
        return json_resp({"ok": True, "shots": len(shots), "narration": len(narration),
                          "warnings": warnings})
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


# LTX-2.3 入口已下线，视频链路固定走 LTX-2.5
_SHOT_STEP = ("生成分镜并拼接(LTX-2.5)", "run_ltx25_multishot.py", [])
_NARRATION_STEP = ("解说配音+字幕(LTX-2.5)", "make_narration.py",
                   ["--film", "outputs/ltx25_film.mp4",
                    "--out", "outputs/ep3_vo.mp4", "--auto-dur"])


def _shots_render_steps(body: dict) -> tuple[list, str]:
    """「只重出指定镜」的步骤：逐镜 --shot N --force，再 --concat 重新拼接。

    返回 (steps, error)；error 非空时 steps 为空。
    """
    idxs = normalize_shot_indices(body.get("shots"))
    if not idxs:
        return [], "请提供要重出的镜号（1 起的整数，如 1,3,5）"
    total = storyboard_shot_count()
    out_of_range = [i for i in idxs if total and i > total]
    if out_of_range:
        return [], f"镜号越界：{out_of_range}（当前分镜共 {total} 镜）"
    steps = [(f"重出第 {i} 镜(LTX-2.5)", "run_ltx25_multishot.py",
              ["--shot", str(i), "--force"]) for i in idxs]
    steps.append(("重新拼接成片(LTX-2.5)", "run_ltx25_multishot.py", ["--concat"]))
    return steps, ""


def _render_steps(only: str, body: dict) -> tuple[list, str]:
    """only -> 要顺序执行的子步骤列表。"""
    if only == "shots":
        return _shots_render_steps(body)
    if only == "all":
        return [_SHOT_STEP, _NARRATION_STEP], ""
    if only == "narration":
        return [_NARRATION_STEP], ""
    return [], f"only 只能是 all/shots/narration，收到 {only}"


def _run_steps(steps: list) -> dict:
    """顺序执行步骤，任一步非零退出即中断。返回结构会进 _state['result']。"""
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


@bp.route("/api/film/render", methods=["POST"])
def api_film_render():
    """按（可能被改过的）分镜重新生成全片 / 只重出指定镜 / 只重做解说。

    body.only:
      all       = 全部镜头 + 解说字幕（约 30-40 分钟）
      shots     = 只重出 body.shots 指定的镜号（强制重生成），再重新拼接成片
      narration = 只重做解说+字幕（用现有镜头）
    """
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    only = str(body.get("only") or "all")   # all | shots | narration

    steps, error = _render_steps(only, body)
    if error:
        return json_resp({"ok": False, "error": error}, status=400)

    run_in_background(lambda: _run_steps(steps))
    if only == "shots":
        msg = f"已启动：重出第 {body.get('shots')} 镜 → 重新拼接（后台运行，看下方日志）"
    else:
        msg = "已启动重新生成（后台运行，看下方日志）"
    return json_resp({"ok": True, "msg": msg})
