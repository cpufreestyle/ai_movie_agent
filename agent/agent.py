"""Agent 编排器：把'编剧→导演→引擎'串成持续创作循环。

持续创作（无限时长）的实现：
  维护一个不断增长的影片文件 outputs/film.mp4。
  每一轮：编剧决定下一镜 -> 导演写成提示词 -> 引擎以 --video_path=film.mp4
  续写一小段。影片因此无缝变长，Agent 可一直创作直到用户停止。
"""
from __future__ import annotations

import json
import os
import shutil
import time

from .writer import Writer
from .director import Director
from .engine import SkyReelsEngine
from .editor import Editor
from .publisher import Publisher
from .collector import Collector
from .knowledge import Knowledge
from .planner import Planner
from .image_prompt import ImagePrompt
from .polisher import Polisher
from .keyframe import KeyframeGenerator
from .blocking import BlockingGenerator


class MovieAgent:
    def __init__(self, config: dict, workdir: str):
        self.config = config
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.writer = Writer(config)
        self.director = Director(config)
        self.engine = SkyReelsEngine(config, agent_root=os.path.dirname(os.path.dirname(__file__)))
        self.editor = Editor(fps=int(config.get("engine", {}).get("fps", 24)))
        self.publisher = Publisher(config, workdir)
        # A–D / F 阶段工具
        self.collector = Collector(config, workdir)
        self.knowledge = Knowledge(config, workdir)
        self.planner = Planner(config, workdir)
        self.image_prompt = ImagePrompt(config, workdir)
        self.polisher = Polisher(config, workdir)
        self.keyframe_gen = KeyframeGenerator(config, workdir)
        self.blocking = BlockingGenerator(config, workdir)
        self.image_prompts: list[str] = []
        self.keyframe_images: list[str] = []

        self.film = os.path.join(self.workdir, "film.mp4")
        self.state_path = os.path.join(self.workdir, "state.json")
        self.script_path = os.path.join(self.workdir, "script.jsonl")
        self.scenes_dir = os.path.join(self.workdir, "scenes")
        os.makedirs(self.scenes_dir, exist_ok=True)

        self.state = self._load_state()

    # ---------- 状态 ----------
    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        bible = self.writer.story_bible()
        state = {
            "title": self.config.get("project", {}).get("title", "未命名"),
            "bible": bible,
            "beats": [],
            "scene_count": 0,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._save_state(state)
        return state

    def _save_state(self, state: dict) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _log_beat(self, beat: dict, prompt: str, clip: str) -> None:
        with open(self.script_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"beat": beat, "prompt": prompt, "clip": clip},
                               ensure_ascii=False) + "\n")

    # ---------- 核心：生成一镜 ----------
    def generate_one_scene(self, seed: int | None = None) -> dict:
        n = self.state["scene_count"]
        beat = self.writer.next_beat(self.state["bible"], self.state["beats"])
        # F 阶段：去 AI 味润色（作用于分镜描述）
        raw = beat.get("description", "")
        beat["description"] = self.polisher.polish(raw)
        # D 阶段：若已规划关键帧提示词/出图，挂到分镜（供 I2V 使用）
        if self.image_prompts and n < len(self.image_prompts):
            beat["keyframe_prompt"] = self.image_prompts[n]
        keyframe = self.keyframe_images[n] if (n < len(self.keyframe_images)
                                               and self.keyframe_images[n]) else None
        if keyframe:
            beat["keyframe_image"] = keyframe
        prompt = self.director.beat_to_prompt(beat)
        print(f"[agent] 第 {n+1} 镜: {beat.get('title')} | 提示词: {prompt}")

        prev = self.film if (n > 0 and os.path.exists(self.film)) else None
        # 先生成到临时片段，再作为续写结果替换 film
        tmp = os.path.join(self.scenes_dir, f"scene_{n+1:03d}.mp4")
        self.engine.generate(prompt, tmp, prev_clip=prev, seed=seed,
                              image=keyframe, two_pass=self.engine.two_pass)

        # 备份当前长片，并把新片段设为影片（续写后的完整片）
        if n > 0 and os.path.exists(self.film):
            backup = os.path.join(self.scenes_dir, f"film_after_{n:03d}.mp4")
            try:
                shutil.copy(self.film, backup)
            except Exception:
                pass
        shutil.move(tmp, self.film)

        self.state["beats"].append(beat)
        self.state["scene_count"] = n + 1
        self._save_state(self.state)
        self._log_beat(beat, prompt, self.film)
        return beat

    # ---------- 循环 ----------
    def run(self, continuous: bool = True, max_scenes: int | None = None,
            auto: bool = True, seed: int | None = None,
            topic: str | None = None, do_research: bool = False) -> None:
        title = self.config.get("project", {}).get("title", "未命名")
        topic = topic or self.config.get("project", {}).get("theme", "")
        print(f"=== 开始创作《{title}》===")

        # ---- A→D 素材层 + 创意层前半 ----
        if do_research:
            material = self.collector.collect(topic)              # A 资料采集
            self.knowledge.ingest(material)                       # B 知识沉淀
            concept = self.planner.plan(topic, material, self.knowledge)  # C 概念企划
            concept = self.planner.enrich(concept, topic, self.knowledge)  # C+ 充实设定
            self.image_prompts = self.image_prompt.generate(concept)      # D 图像提示词
            self.keyframe_images = self.keyframe_gen.generate(self.image_prompts)  # D 关键帧出图
            self.state["bible"] = concept
            self.state["image_prompts"] = self.image_prompts
            self.state["keyframe_images"] = self.keyframe_images
            # Blender 白模分镜资产（previs / 控制图 / 灰模动画），未就绪则跳过
            if self.blocking.is_ready():
                print("[agent] 生成 Blender 白模分镜资产 ...")
                blk = self.blocking.render_assets(self.image_prompts)
                self.state["blocking_previs"] = blk["previews"]
                self.state["blocking_control"] = blk["controls"]
                self.state["blocking_anim"] = blk["anims"]
                if self.config.get("blender", {}).get("use_as_i2v_start") and blk["previews"]:
                    merged = list(self.keyframe_images)
                    for i, p in enumerate(blk["previews"]):
                        if p:
                            if i < len(merged):
                                merged[i] = p
                            else:
                                merged.append(p)
                    self.keyframe_images = merged
            self._save_state(self.state)
            print(f"世界观: {concept.get('logline', '')}")
            print(f"  规划分镜 {len(self.image_prompts)} 个关键帧提示词"
                  f"（已出图 {sum(1 for x in self.keyframe_images if x)} 张）")
        else:
            concept = self.state.get("bible") or self.writer.story_bible()
            self.state["bible"] = concept
            self.image_prompts = self.state.get("image_prompts", [])
            self.keyframe_images = self.state.get("keyframe_images", [])
            print(f"世界观: {concept.get('logline', '')}")

        # ---- E→G→H 创意层后半 + 发布 ----
        try:
            while True:
                if max_scenes and self.state["scene_count"] >= max_scenes:
                    print(f"已达到目标分镜数 {max_scenes}，停止。")
                    break
                beat = self.generate_one_scene(seed=seed)
                dur = self.editor.probe_duration(self.film)
                print(f"  [agent] 当前影片时长 ≈ {dur:.1f}s，"
                      f"共 {self.state['scene_count']} 镜 @ {self.film}")
                if not continuous:
                    break
                if not auto:
                    ans = input("继续创作下一镜? [y/N] ").strip().lower()
                    if ans not in ("y", "yes"):
                        break
        except KeyboardInterrupt:
            print("\n[agent] 用户中断，已保留当前影片。")
        self.finalize()

    def finalize(self) -> str:
        if not os.path.exists(self.film):
            return ""
        out = os.path.join(self.workdir, "movie_final.mp4")
        try:
            self.editor.finalize(self.film, out)
            print(f"[agent] 已封装最终影片: {out}")
        except Exception as e:
            print(f"[agent] 封装失败（不影响原始影片）: {e}")
            out = self.film
        # 自动投稿 B 站（config.publish.enabled 时）
        if self.publisher.enabled:
            res = self.publisher.publish_latest(self.state, out)
            if res.get("ok"):
                print(f"[agent] 已投稿 B 站: {res['title']}")
            else:
                print(f"[agent] 投稿失败: {res.get('error')}")
                if "login" in str(res.get("error", "")).lower() \
                        or "cookie" in str(res.get("error", "")).lower():
                    print(self.publisher.login_guide())
        return out

    def publish_only(self, video: str) -> dict:
        """单独发布某个已存在的影片（供 cli publish 子命令使用）。"""
        return self.publisher.upload(
            video,
            episode=self.state.get("scene_count", 1),
            title=self.state.get("title", "未命名"),
            logline=self.state.get("bible", {}).get("logline", ""),
        )

    def enrich_bible(self, xfade: float = 0.4, bgm=None) -> dict:
        """C+ 阶段：用真实数据充实 bible（人物小传/视觉风格/三幕）并重渲染 demo。

        不重跑研究，仅基于现有 bible 调 planner.enrich，再把规划 demo 重新渲染。
        """
        from agent.concept_video import render_concept_video, render_cover
        # 始终从磁盘重新加载最新 state，避免 WebUI 缓存的旧 state 覆盖用户数据
        state = self._load_state()
        concept = dict(state.get("bible", {}))
        topic = (self.config.get("project", {}) or {}).get("theme") or concept.get("logline") or ""
        concept = self.planner.enrich(concept, topic, self.knowledge)
        state["bible"] = concept
        self._save_state(state)
        self.state = state

        scenes_dir = os.path.join(self.workdir, "scenes")
        os.makedirs(scenes_dir, exist_ok=True)
        kf_dir = os.path.join(self.workdir, "keyframes")
        keyframes = (sorted(
            os.path.join(kf_dir, f) for f in os.listdir(kf_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ) if os.path.isdir(kf_dir) else [])
        blocking_previs = state.get("blocking_previs") or None
        video = render_concept_video(concept, keyframes,
                                     os.path.join(scenes_dir, "concept_demo.mp4"),
                                     xfade=xfade, bgm=bgm,
                                     blocking_images=blocking_previs)
        cover_kf = (blocking_previs[0] if (blocking_previs and blocking_previs[0])
                    else (keyframes[0] if keyframes else None))
        cover = render_cover(concept, cover_kf,
                             os.path.join(scenes_dir, "concept_cover.png"))
        return {"video": video, "cover": cover, "bible": concept}

    def status(self) -> dict:
        dur = self.editor.probe_duration(self.film) if os.path.exists(self.film) else 0.0
        return {
            "title": self.state["title"],
            "scene_count": self.state["scene_count"],
            "duration_sec": round(dur, 1),
            "film": self.film if os.path.exists(self.film) else None,
            "beats": [b.get("title") for b in self.state["beats"]],
        }
