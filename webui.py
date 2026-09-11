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
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import yaml
import requests
from config_env import apply_env_overrides
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
    # 第一集成片（LTX-2.5 链路）：英文配音+中英双语字幕
    "ep1_vo": os.path.join(WORKDIR, "ep1_vo.mp4"),
}

# WebUI 编辑分镜/解说后的保存位置；生成脚本检测到它就覆盖内置分镜
STORYBOARD_PATH = os.path.join(WORKDIR, "storyboard.json")

# 时间轴编辑（WebUI 时间轴页 + cli timeline 共用）：镜头顺序/启停/裁剪
TIMELINE_PATH = os.path.join(WORKDIR, "timeline.json")

# 三集剧集标题（series_script.json 缺失时的兜底）
EP_TITLES = {1: "进城", 2: "觉醒", 3: "对抗"}

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
STAGE_NAMES = {
    "A": "资料采集",
    "B": "知识沉淀",
    "C": "概念企划",
    "D": "关键帧",
    "E": "剧本分镜",
    "F": "文本润色",
    "G": "视频导演",
    "H": "封装发布",
}


# ---------------- 工具 ----------------
def json_resp(data, status=200):
    # 显式声明 charset：不写的话部分客户端(如 PowerShell/ConvertFrom-Json)会按本地编码解，中文变乱码
    return Response(json.dumps(data, ensure_ascii=False),
                    mimetype="application/json; charset=utf-8", status=status)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return apply_env_overrides(yaml.safe_load(f) or {})


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


def start_stage(stage: str, fn):
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)

    def _job():
        print(f"\n=== {stage} 阶段：{STAGE_NAMES[stage]} ===")
        result = fn()
        print(f"=== {stage} 阶段完成 ===")
        return result

    run_in_background(_job)
    return json_resp({"ok": True, "msg": f"{stage} 阶段已启动"})


def load_material() -> list[dict]:
    items = []
    for path in sorted(glob.glob(os.path.join(WORKDIR, "material", "*.md"))):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        heading, _, content = text.partition("\n")
        items.append({"url": heading.lstrip("# ").strip(), "text": content.strip()})
    return items


def save_agent_state(agent):
    agent._save_state(agent.state)


def pipeline_snapshot():
    agent = get_agent()
    state = agent.state
    material = load_material()
    kb_path = os.path.join(WORKDIR, "kb", "chunks.jsonl")
    keyframes = sorted(glob.glob(os.path.join(WORKDIR, "keyframes", "*.*")))
    stages = {
        "A": {"done": bool(material), "count": len(material)},
        "B": {"done": os.path.exists(kb_path), "count": sum(1 for _ in open(kb_path, encoding="utf-8")) if os.path.exists(kb_path) else 0},
        "C": {"done": bool(state.get("bible", {}).get("outline")), "count": len(state.get("bible", {}).get("outline", []))},
        "D": {"done": bool(state.get("image_prompts")), "count": len(keyframes), "ready": agent.keyframe_gen.is_ready()},
        "E": {"done": bool(state.get("draft_beat") or state.get("beats")), "count": len(state.get("beats", []))},
        "F": {"done": bool(state.get("draft_beat", {}).get("polished")), "count": 1 if state.get("draft_beat", {}).get("polished") else 0},
        "G": {"done": bool(state.get("draft_prompt") or state.get("scene_count")), "count": state.get("scene_count", 0), "ready": agent.engine.is_ready()},
        "H": {"done": os.path.exists(MEDIA["movie_final"]), "count": 1 if os.path.exists(MEDIA["movie_final"]) else 0},
    }
    return {
        "stages": stages,
        "material": [{"url": x["url"], "text": x["text"][:1200]} for x in material],
        "bible": state.get("bible", {}),
        "image_prompts": state.get("image_prompts", []),
        "keyframes": [os.path.basename(x) for x in keyframes],
        "draft_beat": state.get("draft_beat"),
        "draft_prompt": state.get("draft_prompt", ""),
        "beats": state.get("beats", []),
        "engine_ready": agent.engine.is_ready(),
    }


# ---------------- 页面 ----------------
@app.route("/")
def index():
    html_path = os.path.join(HERE, "webui", "pipeline.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


# ---------------- 时间轴 ----------------
@app.route("/timeline")
def timeline_page():
    html_path = os.path.join(HERE, "webui", "timeline.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return Response(f.read(), mimetype="text/html")


def _build_timeline():
    """从 series_manifest.json 镜头清单构造初始时间轴（全部启用、未裁剪）。

    没有 manifest 时回退到 storyboard.json 的镜头数；都没有则空时间轴。
    """
    man_path = os.path.join(WORKDIR, "series_manifest.json")
    keys = []
    if os.path.exists(man_path):
        try:
            keys = list(json.load(open(man_path, encoding="utf-8")).keys())
        except Exception:
            keys = []
    if not keys:
        keys = [f"shot{i + 1}" for i in range(len(load_storyboard().get("shots", [])))]
    shots = [{"key": k, "label": k, "enabled": True,
              "in_point": None, "out_point": None, "order": i}
             for i, k in enumerate(keys)]
    return {"shots": shots, "updated": None}


@app.route("/api/timeline", methods=["GET"])
def api_timeline_get():
    if os.path.exists(TIMELINE_PATH):
        try:
            return json_resp(json.load(open(TIMELINE_PATH, encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            return json_resp({"error": str(e)}, status=500)
    return json_resp(_build_timeline())


@app.route("/api/timeline", methods=["POST"])
def api_timeline_post():
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("shots"), list):
        return json_resp({"ok": False, "error": "body 需含 shots 数组"}, status=400)
    body["updated"] = int(time.time())
    try:
        with open(TIMELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        return json_resp({"ok": True, "shots": len(body["shots"])})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@app.route("/api/timeline/build", methods=["POST"])
def api_timeline_build():
    try:
        data = _build_timeline()
        with open(TIMELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return json_resp({"ok": True, "shots": len(data["shots"])})
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


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
            with open(CONFIG_PATH, "r", encoding="utf-8") as src:
                with open(CONFIG_PATH + ".bak", "w", encoding="utf-8") as f:
                    f.write(src.read())
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


@app.route("/api/run/full", methods=["POST"])
def api_run_full():
    """一键全自动出片：A 采集 → B 入库 → C 企划 → 桥接分镜 → G 渲染 → 解说配音 → H 封装(可选投稿)。

    与 /api/run 的区别：/api/run 走 agent 的「逐镜续写」循环，其 G 依赖 SkyReels /
    ComfyUI-LTX-2.5(8188) 工作流，本机未就绪会在 G 断链；本接口改用本机已跑通的
    批处理链路（ComfyUI + run_ltx25_multishot.py），一次调用直通成片。
    """
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    do_research = bool(body.get("do_research", True))
    regen_sb = bool(body.get("regenerate_storyboard", True))
    topic_in = body.get("topic") or None
    # LTX-2.3 入口已下线，视频链路固定走 LTX-2.5（不再读 body.model）

    def _job():
        result = {"ok": True, "steps": []}

        def done(name, **extra):
            result["steps"].append({"step": name, **extra})
            print(f"--- 完成：{name}")

        agent = get_agent()
        cfg = load_config()
        topic = topic_in or (cfg.get("project", {}) or {}).get("theme") or ""

        # ---- A/B/C：素材层 + 创意层（可关闭，直接沿用现有 bible）----
        if do_research:
            print("\n=== A 阶段：资料采集 ===")
            material = agent.collector.collect(topic)
            done("A 资料采集", count=len(material or []))
            print("\n=== B 阶段：知识沉淀 ===")
            agent.knowledge.ingest(material)
            done("B 知识沉淀")
            print("\n=== C 阶段：概念企划 ===")
            concept = agent.planner.plan(topic, material, agent.knowledge)
            concept = agent.planner.enrich(concept, topic, agent.knowledge)
            agent.state["bible"] = concept
            agent._save_state(agent.state)
            done("C 概念企划", logline=(concept or {}).get("logline", ""))

        # ---- 桥接：企划分镜 → 18 镜英文分镜 + 9 段中文解说 ----
        if regen_sb:
            print("\n=== 桥接：企划 → 18 镜分镜 + 9 段解说 ===")
            sb = storyboard_from_bible()
            done("桥接分镜", shots=sb.get("shots"), narration=sb.get("narration"))
        else:
            if not os.path.exists(STORYBOARD_PATH):
                raise RuntimeError("未找到 outputs/storyboard.json（请先生成或开启桥接）")
            with open(STORYBOARD_PATH, encoding="utf-8") as f:
                sb_data = json.load(f)
            print(f"\n=== 沿用现有分镜（{len(sb_data.get('shots', []))} 镜 / "
                  f"{len(sb_data.get('narration', []))} 段解说）===")
            done("沿用现有分镜", shots=len(sb_data.get("shots", [])),
                 narration=len(sb_data.get("narration", [])))

        # ---- G：LTX-2.5 渲染 18 镜并拼接（LTX-2.3 入口已下线）----
        print("\n=== G 阶段：LTX-2.5 渲染 18 镜并拼接 ===")
        run_script("run_ltx25_multishot.py")
        done("G 视频渲染(LTX-2.5)")

        # ---- 解说配音 + 字幕 ----
        print("\n=== 解说配音 + 字幕 ===")
        run_script("make_narration.py", args=[
            "--film", "outputs/ltx25_film.mp4",
            "--out", "outputs/ep3_vo.mp4",
            "--auto-dur"])
        done("解说配音(LTX-2.5)")

        # ---- H：封装成片（可选投稿）----
        print("\n=== H 阶段：封装成片 ===")
        src = (os.path.join(WORKDIR, "ep3_vo.mp4")
               if os.path.exists(os.path.join(WORKDIR, "ep3_vo.mp4"))
               else os.path.join(WORKDIR, "ltx25_film.mp4"))
        if not os.path.exists(src):
            raise RuntimeError("未找到成片（ep3_vo.mp4 / ltx25_film.mp4）")
        shutil.copy(src, MEDIA["movie_final"])
        done("H 封装", file="movie_final.mp4",
             size_mb=round(os.path.getsize(MEDIA["movie_final"]) / 2 ** 20, 2))

        if (load_config().get("publish", {}) or {}).get("enabled"):
            try:
                agent.publish_only(MEDIA["movie_final"])
                done("H 投稿 B 站")
            except Exception as e:  # 投稿失败不推翻已生成的成片
                print(f"[warn] 投稿失败：{e}")
                result["steps"].append({"step": "H 投稿失败", "error": str(e)})

        result["file"] = "movie_final.mp4"
        print("\n=== 全自动流程全部完成 ===")
        return result

    run_in_background(_job)
    return json_resp({"ok": True,
                      "msg": "已启动一键全自动出片（A→C→分镜→渲染→配音→封装，后台运行，看下方日志）"})


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


# ---------------- B 站稿件管理（列表 / 改标题 / 删除） ----------------
def _publisher_direct():
    """直接构造 Publisher（不依赖 MovieAgent 初始化，避免缺依赖拖垮稿件管理）。"""
    from agent.publisher import Publisher
    return Publisher(load_config(), WORKDIR)


@app.route("/api/bili/videos")
def api_bili_videos():
    try:
        return json_resp(_publisher_direct().list_my_videos())
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@app.route("/api/bili/video/<bvid>")
def api_bili_video_detail(bvid):
    try:
        return json_resp(_publisher_direct().get_video_detail(bvid))
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


@app.route("/api/bili/update", methods=["POST"])
def api_bili_update():
    body = request.get_json(force=True, silent=True) or {}
    bvid = str(body.get("bvid") or "").strip()
    title = body.get("title")
    if not bvid or not title or not str(title).strip():
        return json_resp({"ok": False, "error": "bvid 和 title 必填"}, status=400)
    try:
        return json_resp(_publisher_direct().update_video(
            bvid, title=str(title).strip(),
            desc=body.get("desc"), tag=body.get("tag")))
    except Exception as e:  # noqa: BLE001
        return json_resp({"ok": False, "error": str(e)}, status=500)


# 自动删除已放弃（2026-09-05）：B 站风控需人机验证（code=340022），无法脚本化。
# 删除请到创作中心 (https://member.bilibili.com) 手动操作。


# ---------------- A-H 流程控制台 ----------------
@app.route("/api/pipeline")
def api_pipeline():
    try:
        return json_resp(pipeline_snapshot())
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)


@app.route("/api/pipeline/bible", methods=["POST"])
def api_pipeline_bible():
    body = request.get_json(force=True, silent=True) or {}
    bible = body.get("bible")
    if not isinstance(bible, dict):
        return json_resp({"ok": False, "error": "bible 必须是 JSON 对象"}, status=400)
    agent = get_agent()
    agent.state["bible"] = bible
    save_agent_state(agent)
    return json_resp({"ok": True})


@app.route("/api/pipeline/draft", methods=["POST"])
def api_pipeline_draft():
    body = request.get_json(force=True, silent=True) or {}
    beat = body.get("draft_beat")
    if not isinstance(beat, dict):
        return json_resp({"ok": False, "error": "draft_beat 必须是 JSON 对象"}, status=400)
    agent = get_agent()
    agent.state["draft_beat"] = beat
    agent.state["draft_prompt"] = str(body.get("draft_prompt") or "")
    save_agent_state(agent)
    return json_resp({"ok": True})


@app.route("/api/pipeline/image-prompts", methods=["POST"])
def api_pipeline_image_prompts():
    body = request.get_json(force=True, silent=True) or {}
    prompts = body.get("prompts")
    if not isinstance(prompts, list) or not all(isinstance(x, str) for x in prompts):
        return json_resp({"ok": False, "error": "prompts 必须是字符串数组"}, status=400)
    agent = get_agent()
    agent.state["image_prompts"] = [p.strip() for p in prompts if p.strip()]
    save_agent_state(agent)
    return json_resp({"ok": True, "count": len(agent.state["image_prompts"])})


@app.route("/api/pipeline/stage/<stage>", methods=["POST"])
def api_pipeline_stage(stage):
    stage = stage.upper()
    if stage not in STAGE_NAMES:
        return json_resp({"ok": False, "error": "未知阶段"}, status=404)
    body = request.get_json(force=True, silent=True) or {}

    def _job():
        agent = get_agent()
        topic = str(body.get("topic") or
                    agent.config.get("project", {}).get("theme", ""))
        if stage == "A":
            items = agent.collector.collect(topic)
            return {"ok": True, "material_count": len(items)}
        if stage == "B":
            items = load_material()
            agent.knowledge.ingest(items)
            return {"ok": True, "chunk_count": sum(
                1 for _ in open(agent.knowledge.store_path, encoding="utf-8")
            ) if os.path.exists(agent.knowledge.store_path) else 0}
        if stage == "C":
            material = load_material()
            if material:
                agent.knowledge.ingest(material)
            concept = agent.planner.plan(topic, material, agent.knowledge)
            agent.state["bible"] = concept
            save_agent_state(agent)
            return {"ok": True, "bible": concept}
        if stage == "D":
            concept = agent.state.get("bible") or agent.writer.story_bible()
            prompts = agent.image_prompt.generate(concept)
            images = agent.keyframe_gen.generate(prompts)
            agent.state["image_prompts"] = prompts
            agent.state["keyframe_images"] = images
            save_agent_state(agent)
            return {"ok": True, "prompt_count": len(prompts),
                    "keyframe_count": sum(1 for x in images if x)}
        if stage == "E":
            beat = agent.writer.next_beat(agent.state.get("bible", {}),
                                          agent.state.get("beats", []))
            agent.state["draft_beat"] = beat
            agent.state["draft_prompt"] = ""
            save_agent_state(agent)
            return {"ok": True, "draft_beat": beat}
        if stage == "F":
            beat = dict(agent.state.get("draft_beat") or {})
            if not beat:
                raise RuntimeError("请先执行 E 阶段生成分镜草稿")
            beat["description"] = agent.polisher.polish(beat.get("description", ""))
            beat["polished"] = True
            agent.state["draft_beat"] = beat
            save_agent_state(agent)
            return {"ok": True, "draft_beat": beat}
        if stage == "G":
            beat = dict(agent.state.get("draft_beat") or {})
            if not beat:
                raise RuntimeError("请先执行 E 阶段生成或保存分镜草稿")
            prompt = agent.director.beat_to_prompt(beat)
            agent.state["draft_prompt"] = prompt
            save_agent_state(agent)
            if not body.get("generate", False):
                return {"ok": True, "prompt": prompt}
            n = agent.state.get("scene_count", 0)
            keyframes = agent.state.get("keyframe_images", [])
            keyframe = keyframes[n] if n < len(keyframes) else None
            tmp = os.path.join(agent.scenes_dir, f"scene_{n + 1:03d}.mp4")
            prev = agent.film if n > 0 and os.path.exists(agent.film) else None
            agent.engine.generate(prompt, tmp, prev_clip=prev, image=keyframe)
            if prev:
                shutil.copy(agent.film, os.path.join(agent.scenes_dir, f"film_after_{n:03d}.mp4"))
            shutil.move(tmp, agent.film)
            agent.state["beats"].append(beat)
            agent.state["scene_count"] = n + 1
            agent.state.pop("draft_beat", None)
            agent.state.pop("draft_prompt", None)
            save_agent_state(agent)
            agent._log_beat(beat, prompt, agent.film)
            return {"ok": True, "scene_count": n + 1}
        if stage == "H":
            output = agent.finalize()
            return {"ok": bool(output), "output": output}
        raise RuntimeError("未实现的阶段")

    return start_stage(stage, _job)


@app.route("/api/publish", methods=["POST"])
def api_publish():
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)
    body = request.get_json(force=True, silent=True) or {}
    video = body.get("video") or MEDIA["movie_final"]
    # 前端默认填的是相对路径（outputs/movie_final.mp4），但 publisher 跑 biliup 时
    # cwd=outputs/，相对路径会变成 outputs/outputs/... 故统一在项目根解析成绝对路径。
    if not os.path.isabs(video):
        video = os.path.abspath(os.path.join(HERE, video))
    if not os.path.exists(video):
        return json_resp({"ok": False, "error": f"影片不存在: {video}"}, status=400)
    episode = body.get("episode")
    episode = int(episode) if str(episode).strip().isdigit() else None
    subtitle = str(body.get("subtitle") or "").strip()
    submit = bool(body.get("submit", False))

    def _job():
        agent = get_agent()
        if episode is None:
            # 兼容旧调用（发布页手填路径）：标题沿用 agent.state 里的片名与集数
            return agent.publish_only(video)
        # 多集投稿：publish_only 的集数取自 state.scene_count，三集会全部标成同一集，
        # 因此这里按 episode 现算标题，并过一遍标题门禁。
        pub = agent.publisher
        film_title = (load_config().get("project", {}) or {}).get("title") or "未命名"
        template = pub.cfg.get("title_template", "{title} · 第{n}集")
        title = pub._fill(template, title=film_title, n=episode)
        title = title.replace(f"第{episode}集", f"第{pub._cn_episode(episode)}集")
        if subtitle:
            title = f"{title}：{subtitle}"
        err = pub.validate_title(title, episode=episode, film_title=film_title)
        if err:
            return {"ok": False, "error": f"标题校验未通过：{err}（{title}）"}
        return pub.upload(video, episode=episode, title=title,
                          film_title=film_title, submit=submit)
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


# ---------------- 成片：列表 / 播放 / 分镜编辑 / 重新生成 ----------------
def film_candidates() -> list[dict]:
    """outputs 下的成片 mp4，按修改时间倒序。"""
    items = []
    for path in glob.glob(os.path.join(WORKDIR, "*.mp4")):
        items.append({
            "name": os.path.basename(path),
            "size_mb": round(os.path.getsize(path) / 2 ** 20, 2),
            "mtime": os.path.getmtime(path),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def load_storyboard() -> dict:
    """优先用 outputs/storyboard.json（WebUI 改过的），否则读脚本内置默认分镜。"""
    overridden = os.path.exists(STORYBOARD_PATH)
    if overridden:
        with open(STORYBOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        shots = data.get("shots") or []
        narration = data.get("narration") or []
    else:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import importlib
        shots = list(importlib.import_module("shots").SHOTS)
        narration = list(importlib.import_module("make_narration").LINES)
    return {"shots": shots, "narration": narration, "overridden": overridden}


@app.route("/api/films")
def api_films():
    return json_resp({"films": film_candidates()})


@app.route("/api/film/play")
def api_film_play():
    """播放 outputs 下任意成片（只取文件名，防路径穿越）。conditional 支持拖动进度。"""
    name = os.path.basename(request.args.get("name", ""))
    if not name.endswith(".mp4"):
        return json_resp({"error": "仅支持 mp4"}, status=400)
    path = os.path.join(WORKDIR, name)
    if not os.path.isfile(path):
        return json_resp({"error": f"影片不存在: {name}"}, status=404)
    return send_file(path, mimetype="video/mp4", conditional=True)


@app.route("/api/storyboard", methods=["GET"])
def api_storyboard_get():
    try:
        return json_resp(load_storyboard())
    except Exception as e:  # noqa: BLE001
        return json_resp({"error": str(e)}, status=500)


@app.route("/api/storyboard", methods=["POST"])
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


def _zh_ratio(s: str) -> float:
    """字符串里中文字符的占比，用于校验解说是否被 LLM 写成了英文。"""
    if not s:
        return 0.0
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff") / len(s)


def storyboard_from_bible() -> dict:
    """C→分镜桥接：用当前企划(bible)+素材，让 LLM 扩写成 18 镜英文分镜 + 9 段中文解说。

    写入 outputs/storyboard.json。抽成函数以便「一键全自动」链路复用。
    """
    from agent.llmutil import make_client, chat, extract_json
    cfg = load_config()
    llm = cfg.get("llm", {}) or {}
    if llm.get("disabled"):
        raise RuntimeError("LLM 已禁用（config.llm.disabled=true）")
    client = make_client(cfg)
    if client is None:
        raise RuntimeError("无法创建 LLM 客户端（缺 openai 包或配置错误）")
    model = llm.get("model")
    agent = get_agent()
    bible = agent.state.get("bible") or {}
    theme = cfg.get("project", {}).get("theme") or bible.get("logline") or ""
    material = load_material()
    refs = "\n".join(f"- {m['text'][:600]}" for m in material[:3])
    system = (
        "你是资深科幻短片分镜编剧。任务：根据给定概念，产出可直接喂给图生视频模型"
        "(LTX-2.5，使用英文提示词)的镜头列表，以及一段配套的中文第一人称内心独白解说"
        "(用于配音+字幕)。\n"
        "要求：\n"
        "- shots：恰好 18 条英文镜头描述，每条一句，含 主体+动作+场景+光影/镜头运动+风格，"
        "默认日式动漫风格（anime style, cel-shaded, clean line art, vibrant colors），"
        "画面连续可拼接成约 60 秒短片，紧扣主题。\n"
        "- narration：恰好 9 条中文解说，第一人称内心独白，口语化、有情绪递进；"
        "关键：每句必须短（12-18 字），一口气能说完（约 4-5 秒），不要写复合长句或并列句，"
        "否则配音会被加速显得机械。\n"
        "- 只输出 JSON，形如：{\"shots\":[...18...],\"narration\":[...9...]}，不要多余文字。"
    )
    user = (
        f"主题概念：{theme}\n\n概念企划：\n{json.dumps(bible, ensure_ascii=False)[:1500]}\n\n"
        f"参考素材（节选）：\n{refs}\n\n请产出 18 镜英文分镜与 9 段中文解说。"
    )
    print("[storyboard] 调用 LLM 生成分镜 ...")
    out = chat(client, system, user, max_tokens=3000, temperature=0.85, model=model,
               extra_body={"enable_thinking": False})
    if not out:
        raise RuntimeError("LLM 返回为空（可能模型是推理模型且 max_tokens 不足，或模型未加载）")
    try:
        data = json.loads(extract_json(out))
    except Exception as e:
        raise RuntimeError(f"LLM 返回无法解析为 JSON：{e}\n原始：{out[:500]}")
    shots = data.get("shots")
    narration = data.get("narration")
    if not isinstance(shots, list) or not all(isinstance(x, str) for x in shots) or not shots:
        raise RuntimeError("shots 不是非空字符串数组")
    if not isinstance(narration, list) or not all(isinstance(x, str) for x in narration):
        raise RuntimeError("narration 不是字符串数组")
    shots = [s.strip() for s in shots if s.strip()]
    narration = [s.strip() for s in narration if s.strip()]
    if len(shots) < 18:
        shots = shots + [shots[-1]] * (18 - len(shots))
        print(f"[storyboard] shots 不足 18，已补至 18")
    elif len(shots) > 18:
        shots = shots[:18]
    if len(narration) < 9:
        narration = narration + [(narration[-1] if narration else "……")] * (9 - len(narration))
    elif len(narration) > 9:
        narration = narration[:9]
    # 校验解说语言：prompt 已明确要求中文，但小模型经常忽略指令直接吐英文。
    # 若静默写入，后续 TTS 会用中文语音念英文、字幕也是英文，成片报废且不易察觉，
    # 因此宁可在这里中断任务，也不产出英文解说。
    bad = [n for n in narration if _zh_ratio(n) < 0.3]
    if bad:
        raise RuntimeError(
            "解说必须是中文，但 LLM 产出了英文（示例："
            + " / ".join(x[:45] for x in bad[:2])
            + "）。请重试，或换更听话的模型（config.llm.model，推荐 gemma4:e2b）。")
    with open(STORYBOARD_PATH, "w", encoding="utf-8") as f:
        json.dump({"shots": shots, "narration": narration}, f, ensure_ascii=False, indent=2)
    print(f"[storyboard] 已写入，{len(shots)} 镜 / {len(narration)} 段解说")
    return {"ok": True, "shots": len(shots), "narration": len(narration)}


def run_script(script: str, timeout: int = 7200, args: list | None = None) -> None:
    """在后台作业里执行本项目脚本；失败即抛异常中断整条链。

    渲染 18 镜约需数十分钟，timeout 默认给到 2 小时。
    args: 传给脚本的额外命令行参数（如 make_narration 的 --film/--auto-dur）。
    """
    print(f"\n=== 运行脚本：{script} {' '.join(args or [])} ===")
    # 子进程默认按本地代码页(中文 Windows=GBK)输出 stdout；这里按 UTF-8 解码，
    # 必须强制子进程用 UTF-8 输出，否则日志里的中文全是乱码。
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # 输出必须**边跑边转发**：原先 subprocess.run(capture_output=True) 会把子进程
    # 输出憋到进程结束才一次性 print，WebUI 日志在长达几十分钟里一片空白，
    # 用户无法区分"正在跑"还是"已经卡死"。改成开线程边读边写 _state['logs']。
    proc = subprocess.Popen([sys.executable, "-u", script, *(args or [])], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, encoding="utf-8", errors="replace")

    def _pump():
        # 后台线程逐行转发；print 已被 redirect 到线程安全的 _LogSink
        for line in proc.stdout:
            print(line, end="")

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    try:
        proc.wait(timeout=timeout)   # 真超时：即使脚本卡死、一行不吐也能兜住
    except subprocess.TimeoutExpired:
        proc.kill()
        t.join(timeout=10)
        raise RuntimeError(f"{script} 执行超时（>{timeout}s）")
    t.join(timeout=30)               # 等泵把剩余输出读完
    if proc.returncode != 0:
        raise RuntimeError(f"{script} 执行失败（code={proc.returncode}）")


@app.route("/api/storyboard/generate", methods=["POST"])
def api_storyboard_generate():
    """用当前企划(bible)+素材，让 LLM 扩写成 18 镜英文分镜 + 9 段中文解说，写入 storyboard.json。"""
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)

    run_in_background(storyboard_from_bible)
    return json_resp({"ok": True, "msg": "已启动（LLM 生成分镜，后台运行，看下方日志）"})


@app.route("/api/models")
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


@app.route("/api/film/render", methods=["POST"])
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


# ---------------- 看板：三集成片总览 ----------------
_probe_cache: dict = {}


def probe_media(path: str) -> dict:
    """探视频规格（时长/分辨率/帧率/编码）。

    按 (path, mtime, size) 缓存：ffmpeg 探一次要 fork 进程，看板每刷新一次
    就要探 3~6 个文件，不缓存会明显卡顿；mtime/size 变了自动失效。
    """
    try:
        st = os.stat(path)
        key = (path, int(st.st_mtime), st.st_size)
    except OSError:
        return {}
    if key in _probe_cache:
        return _probe_cache[key]
    info: dict = {}
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([exe, "-i", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        err = r.stderr or ""
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
        if m:
            info["duration_sec"] = round(
                int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2)
        for line in err.splitlines():
            if " Video: " in line and "width" not in info:
                mm = re.search(r"(\d{2,5})x(\d{2,5})", line)
                if mm:
                    info["width"], info["height"] = int(mm.group(1)), int(mm.group(2))
                fm = re.search(r"([\d.]+) fps", line)
                if fm:
                    info["fps"] = round(float(fm.group(1)), 2)
                cm = re.search(r"Video: (\w+)", line)
                if cm:
                    info["vcodec"] = cm.group(1)
            if " Audio: " in line and "acodec" not in info:
                cm = re.search(r"Audio: (\w+)", line)
                if cm:
                    info["acodec"] = cm.group(1)
                sm = re.search(r"(\d+) Hz", line)
                if sm:
                    info["sample_rate"] = int(sm.group(1))
                chm = re.search(r"(mono|stereo|5\.1)", line)
                if chm:
                    info["channels"] = chm.group(1)
    except Exception as e:  # noqa: BLE001  探测失败只降级显示，不能拖垮看板
        info["probe_error"] = str(e)
    _probe_cache[key] = info
    return info


def series_script() -> dict:
    path = os.path.join(WORKDIR, "series_script.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


@app.route("/api/board")
def api_board():
    """看板数据源：三集（原始成片 + 旁白同步版）规格、镜头素材数、台词、同步状态。"""
    script = series_script()
    eps = []
    for n in (1, 2, 3):
        shots = sorted(glob.glob(
            os.path.join(WORKDIR, "series_shots_mmh3", f"ep{n}_shot*.mp4")))
        ep = script.get(f"ep{n}") or {}
        item = {
            "ep": n,
            "title": ep.get("title") or EP_TITLES.get(n, ""),
            "shots": len(shots),
            "narration": len(ep.get("narration") or []),
            "narration_en": len(ep.get("narration_en") or []),
            "lines_en": ep.get("narration_en") or [],
            "lines_zh": ep.get("narration") or [],
        }
        for kind, name in (("vo", f"ep{n}_vo_mmh3.mp4"),
                           ("raw", f"ep{n}_series_film_mmh3.mp4")):
            path = os.path.join(WORKDIR, name)
            if os.path.exists(path):
                item[kind] = {
                    "name": name,
                    "size_mb": round(os.path.getsize(path) / 2 ** 20, 2),
                    "mtime": os.path.getmtime(path),
                    **probe_media(path),
                }
        # 同步判据与 make_narration.py 的镜头块对齐一致：镜头数能被旁白段数整除
        seg = item["narration"]
        item["synced"] = bool(item.get("vo")) and seg > 0 and item["shots"] % seg == 0
        eps.append(item)

    ready = [e for e in eps if e.get("vo")]
    return json_resp({
        "series": script.get("series") or "",
        "note": script.get("note") or "",
        "episodes": eps,
        "summary": {
            "episodes": len(eps),
            "ready": len(ready),
            "total_duration_sec": round(
                sum(e["vo"].get("duration_sec") or 0 for e in ready), 2),
            "total_size_mb": round(
                sum(e["vo"].get("size_mb") or 0 for e in ready), 2),
            "shots": sum(e["shots"] for e in eps),
            "narration": sum(e["narration"] for e in eps),
            "engine": "MiniMax H3 (Turbo 4v/8a)",
        },
    })


def main():
    p = argparse.ArgumentParser(description="AI 电影 Agent WebUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    print(f"[webui] 启动于 http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
