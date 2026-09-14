"""创意策划 / A–H 流程控制台 / 投稿 / 三集看板。"""
from __future__ import annotations

import glob
import os
import shutil

from flask import Blueprint, request

from ..services.pipeline import probe_media, series_script
from ..state import (
    EP_TITLES,
    HERE,
    MEDIA,
    STAGE_NAMES,
    WORKDIR,
    _state,
    get_agent,
    json_resp,
    load_config,
    load_material,
    pipeline_snapshot,
    run_in_background,
    save_agent_state,
    start_stage,
)

bp = Blueprint("pipeline", __name__)


# ---------------- 创意策划 ----------------
@bp.route("/api/enrich", methods=["POST"])
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


@bp.route("/api/publish_concept", methods=["POST"])
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


# ---------------- A-H 流程控制台 ----------------
@bp.route("/api/pipeline")
def api_pipeline():
    try:
        return json_resp(pipeline_snapshot())
    except RuntimeError as e:
        return json_resp({"agent_error": str(e)}, status=200)


@bp.route("/api/pipeline/bible", methods=["POST"])
def api_pipeline_bible():
    body = request.get_json(force=True, silent=True) or {}
    bible = body.get("bible")
    if not isinstance(bible, dict):
        return json_resp({"ok": False, "error": "bible 必须是 JSON 对象"}, status=400)
    agent = get_agent()
    agent.state["bible"] = bible
    save_agent_state(agent)
    return json_resp({"ok": True})


@bp.route("/api/pipeline/draft", methods=["POST"])
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


@bp.route("/api/pipeline/image-prompts", methods=["POST"])
def api_pipeline_image_prompts():
    body = request.get_json(force=True, silent=True) or {}
    prompts = body.get("prompts")
    if not isinstance(prompts, list) or not all(isinstance(x, str) for x in prompts):
        return json_resp({"ok": False, "error": "prompts 必须是字符串数组"}, status=400)
    agent = get_agent()
    agent.state["image_prompts"] = [p.strip() for p in prompts if p.strip()]
    save_agent_state(agent)
    return json_resp({"ok": True, "count": len(agent.state["image_prompts"])})


@bp.route("/api/pipeline/stage/<stage>", methods=["POST"])
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


@bp.route("/api/publish", methods=["POST"])
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


# ---------------- 看板：三集成片总览 ----------------
@bp.route("/api/board")
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
