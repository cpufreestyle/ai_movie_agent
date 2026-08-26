#!/usr/bin/env python3
"""AI 电影 Agent · 本地 WebUI（零构建，纯 Flask + 原生前端）。

启动：  python webui.py            # 默认 http://127.0.0.1:8000
      python webui.py --port 9000  # 自定义端口

四个页签：
  1) 概览配置  - 影片状态 + biliup 登录态 + config.yaml 在线编辑
  2) 运行监控  - 启动 A–H 流水线 / 续写，实时日志 + 进度
  3) 创意策划  - enrich-bible 充实设定 + 预览 concept demo + 投稿到 B 站
  4) 发布      - biliup 登录引导 + 投稿正式成片

后端把耗时操作放到后台线程，print 日志被捕获后经 /api/logs 轮询给前端。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import threading

import yaml
from flask import Flask, Response, request, send_file

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")
WORKDIR = os.path.join(HERE, "outputs")

# 媒体文件白名单（仅允许预览这些，避免任意路径遍历）
MEDIA = {
    "concept_demo": os.path.join(WORKDIR, "scenes", "concept_demo.mp4"),
    "concept_cover": os.path.join(WORKDIR, "scenes", "concept_cover.png"),
    "film": os.path.join(WORKDIR, "film.mp4"),
    "movie_final": os.path.join(WORKDIR, "movie_final.mp4"),
}

app = Flask(__name__)
app.json.ensure_ascii = False  # 中文不乱码

_state = {
    "agent": None,
    "agent_error": None,
    "thread": None,
    "running": False,
    "stop": False,
    "logs": [],
    "result": None,
}
_lock = threading.Lock()


# ---------------- 工具 ----------------
def json_resp(data, status=200):
    return Response(json.dumps(data, ensure_ascii=False),
                    mimetype="application/json", status=status)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_agent():
    """惰性构造并重用 MovieAgent（构造失败也只影响相关接口，不拖垮服务）。"""
    if _state["agent"] is not None or _state["agent_error"] is not None:
        if _state["agent_error"]:
            raise RuntimeError(_state["agent_error"])
        return _state["agent"]
    try:
        from agent.agent import MovieAgent
        _state["agent"] = MovieAgent(load_config(), WORKDIR)
    except Exception as e:  # 例如缺少 torch / SkyReels 依赖
        _state["agent_error"] = (f"Agent 初始化失败（依赖或环境缺失，"
                                  f"不影响配置/创意策划等接口）：{e}")
        raise RuntimeError(_state["agent_error"])
    return _state["agent"]


class _LogSink(io.TextIOBase):
    def write(self, s: str) -> int:
        with _lock:
            _state["logs"].append(s)
        return len(s)

    def flush(self):
        pass


def run_in_background(fn):
    """在后台线程跑 fn，捕获 stdout/stderr 到 _state['logs']。"""
    def _wrapped():
        with _lock:
            _state["running"] = True
            _state["stop"] = False
            _state["logs"] = []
            _state["result"] = None
        sink = _LogSink()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                _state["result"] = fn()
        except Exception as e:  # noqa: BLE001
            with _lock:
                _state["logs"].append(f"[webui] 任务异常: {e}\n")
            _state["result"] = {"error": str(e)}
        finally:
            with _lock:
                _state["running"] = False
    t = threading.Thread(target=_wrapped, daemon=True)
    with _lock:
        _state["thread"] = t
    t.start()


# ---------------- 页面 ----------------
@app.route("/")
def index():
    html_path = os.path.join(HERE, "webui", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


# ---------------- 概览 / 配置 ----------------
@app.route("/api/overview")
def overview():
    try:
        agent = get_agent()
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)
    st = agent.status()
    bili = agent.publisher
    st["concept_demo"] = os.path.exists(MEDIA["concept_demo"])
    st["concept_cover"] = os.path.exists(MEDIA["concept_cover"])
    st["film_exists"] = os.path.exists(MEDIA["film"])
    st["movie_final_exists"] = os.path.exists(MEDIA["movie_final"])
    st["biliup_ready"] = bili.is_ready()
    st["cookies_exist"] = os.path.exists(os.path.join(WORKDIR, "cookies.json"))
    return json_resp(st)


@app.route("/api/config", methods=["GET"])
def get_config():
    return json_resp(load_config())


@app.route("/api/config", methods=["POST"])
def save_config():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return json_resp({"ok": False, "error": "body 必须是 JSON 对象"}, status=400)
    # 备份原配置
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH + ".bak", "w", encoding="utf-8") as f:
                f.write(open(CONFIG_PATH, "r", encoding="utf-8").read())
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
        # 重置已缓存的 agent，使其使用新配置
        with _lock:
            _state["agent"] = None
            _state["agent_error"] = None
        return json_resp({"ok": True})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


# ---------------- 运行监控 ----------------
@app.route("/api/run", methods=["POST"])
def api_run():
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    topic = body.get("topic") or None
    max_scenes = int(body.get("max_scenes", 3) or 3)
    do_research = bool(body.get("do_research", False))

    def _job():
        agent = get_agent()
        agent.run(continuous=False, max_scenes=max_scenes, auto=True,
                  topic=topic, do_research=do_research)
    try:
        run_in_background(_job)
    except RuntimeError as e:
        return json_resp({"ok": False, "error": str(e)}, status=500)
    return json_resp({"ok": True, "msg": "已启动（后台线程）"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    with _lock:
        _state["stop"] = True
        _state["logs"].append("[webui] 已请求停止（阻塞式任务将在本轮结束后生效）\n")
    return json_resp({"ok": True})


@app.route("/api/logs")
def api_logs():
    with _lock:
        return json_resp({
            "running": _state["running"],
            "logs": "".join(_state["logs"]),
            "result": _state["result"],
        })


# ---------------- 创意策划 ----------------
@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    bgm = bool(body.get("bgm", False))
    xfade = float(body.get("xfade", 0.4) or 0.4)

    def _job():
        agent = get_agent()
        return agent.enrich_bible(xfade=xfade, bgm=bgm)
    try:
        run_in_background(_job)
    except RuntimeError as e:
        return json_resp({"ok": False, "error": str(e)}, status=500)
    return json_resp({"ok": True, "msg": "已启动 enrich-bible"})


@app.route("/api/publish_concept", methods=["POST"])
def api_publish_concept():
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    submit = bool(body.get("submit", True))
    bgm = bool(body.get("bgm", False))
    xfade = float(body.get("xfade", 0.4) or 0.4)

    def _job():
        agent = get_agent()
        return agent.publisher.publish_concept(submit=submit, xfade=xfade, bgm=bgm)
    try:
        run_in_background(_job)
    except RuntimeError as e:
        return json_resp({"ok": False, "error": str(e)}, status=500)
    return json_resp({"ok": True, "msg": "已启动投稿"})


# ---------------- 发布 ----------------
@app.route("/api/biliup")
def api_biliup():
    try:
        agent = get_agent()
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)
    ready = agent.publisher.is_ready()
    return json_resp({
        "ready": ready,
        "cookies_exist": os.path.exists(os.path.join(WORKDIR, "cookies.json")),
        "guide": agent.publisher.login_guide(),
    })


@app.route("/api/publish", methods=["POST"])
def api_publish():
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    video = body.get("video") or MEDIA["movie_final"]
    if not os.path.exists(video):
        return json_resp({"ok": False, "error": f"影片不存在: {video}"}, status=400)

    def _job():
        agent = get_agent()
        return agent.publish_only(video)
    try:
        run_in_background(_job)
    except RuntimeError as e:
        return json_resp({"ok": False, "error": str(e)}, status=500)
    return json_resp({"ok": True, "msg": "已启动投稿"})


# ---------------- 媒体预览 ----------------
@app.route("/api/media")
def api_media():
    name = request.args.get("name", "")
    path = MEDIA.get(name)
    if not path or not os.path.exists(path):
        return json_resp({"error": f"无媒体: {name}"}, status=404)
    return send_file(path, mimetype="video/mp4" if path.endswith(".mp4") else "image/png")


def main():
    p = argparse.ArgumentParser(description="AI 电影 Agent WebUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    print(f"[webui] 启动于 http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
