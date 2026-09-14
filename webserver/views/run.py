"""运行监控：逐镜续写 / 一键全自动出片 / 停止。"""
from __future__ import annotations

import json
import os
import shutil

from flask import Blueprint, request

from ..services.pipeline import run_script, storyboard_from_bible
from ..state import (
    MEDIA,
    STORYBOARD_PATH,
    WORKDIR,
    _lock,
    _state,
    _stop_requested,
    get_agent,
    json_resp,
    load_config,
    logbuf,
    run_in_background,
)

bp = Blueprint("run", __name__)


@bp.route("/api/run", methods=["POST"])
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
                  topic=topic, do_research=do_research,
                  should_stop=_stop_requested)
    try:
        run_in_background(_job)
    except RuntimeError as e:
        return json_resp({"ok": False, "error": str(e)}, status=500)
    return json_resp({"ok": True, "msg": "已启动（后台线程）"})


@bp.route("/api/run/full", methods=["POST"])
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
            # 投稿前体检门禁（P2-⑬）：配置开启自动投稿时，体检未通过就不投。
            # 成片已在上一路 copy 到 movie_final.mp4，拦截不会丢产物。
            allow, _pf = agent.publish_guard(MEDIA["movie_final"])
            if not allow:
                print("[warn] 投稿前体检未通过，已跳过自动投稿（成片已保留）")
                result["steps"].append({"step": "H 投稿跳过",
                                        "reason": "preflight 未通过"})
            else:
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


@bp.route("/api/stop", methods=["POST"])
def api_stop():
    """请求停止当前后台任务。

    两条链路都会被响应：
      - 进程内（/api/run 的 agent 循环）：should_stop=_stop_requested，每镜检查一次；
      - 子进程（/api/run/full 的 run_script）：轮询本标志后 terminate 子进程。
    检查粒度是「当前镜」，单次引擎渲染是阻塞调用，无法在渲染中途打断。
    """
    with _lock:
        _state["stop"] = True
        running = bool(_state["running"])
    logbuf.append(
        "[webui] 已请求停止：进程内任务将在当前镜结束后退出；"
        "子进程任务会立即终止（当镜产物可能不完整）。\n" if running
        else "[webui] 当前没有正在运行的任务。\n")
    return json_resp({"ok": True, "running": running})
