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
from .ltx_engine import LTXEngine
from .mmh3_engine import MMH3Engine
from .sol_h3_engine import SolH3Engine
from .editor import Editor
from .publisher import Publisher
from .collector import Collector
from .knowledge import Knowledge
from .planner import Planner
from .image_prompt import ImagePrompt
from .polisher import Polisher
from .keyframe import KeyframeGenerator
from .configutil import for_stage
from .blocking import BlockingGenerator
from .llmutil import log
from .video_engine import filter_engine_kwargs


class MovieAgent:
    def __init__(self, config: dict, workdir: str):
        self.config = config
        self.workdir = os.path.abspath(workdir)
        os.makedirs(self.workdir, exist_ok=True)
        self.writer = Writer(for_stage(config, "E"))
        self.director = Director(for_stage(config, "G"))
        eng_cfg = for_stage(config, "G")
        backend = (eng_cfg.get("engine", {}) or {}).get("backend")
        agent_root = os.path.dirname(os.path.dirname(__file__))
        if backend == "comfyui_ltx":
            self.engine = LTXEngine(eng_cfg, agent_root=agent_root)
        elif backend == "comfyui_mmH3":
            # MiniMax H3（Turbo 4 步，原生立体声）
            self.engine = MMH3Engine(eng_cfg, agent_root=agent_root)
        elif backend == "sol_h3":
            # 远程 DGX Spark 上的 Sol-H3-Spark（HTTP 服务封装）
            self.engine = SolH3Engine(eng_cfg, agent_root=agent_root)
        else:
            self.engine = SkyReelsEngine(eng_cfg, agent_root=agent_root)
        self.editor = Editor(fps=int(for_stage(config, "G").get("engine", {}).get("fps", 24)))
        self.publisher = Publisher(for_stage(config, "H"), workdir)
        # A–D / F 阶段工具（fork 的按阶段配置）
        self.collector = Collector(for_stage(config, "A"), workdir)
        self.knowledge = Knowledge(for_stage(config, "B"), workdir)
        self.planner = Planner(for_stage(config, "C"), workdir)
        self.image_prompt = ImagePrompt(for_stage(config, "D"), workdir)
        self.polisher = Polisher(for_stage(config, "F"), workdir)
        self.keyframe_gen = KeyframeGenerator(for_stage(config, "D"), workdir)
        # Blender 白模分镜（远程重构新增，未就绪自动跳过）
        self.blocking = BlockingGenerator(config, workdir)
        # 白模资产与关键帧索引见下方 property（单一来源 = self.state，不在此初始化）
        # 逐镜质检累积结果（内存态；每打分一次就增量写 outputs/qa_report.json）
        self._qa_entries: list = []

        self.film = os.path.join(self.workdir, "film.mp4")
        self.state_path = os.path.join(self.workdir, "state.json")
        self.script_path = os.path.join(self.workdir, "script.jsonl")
        self.scenes_dir = os.path.join(self.workdir, "scenes")
        os.makedirs(self.scenes_dir, exist_ok=True)

        self.state = self._load_state()

    # ---------- 白模 / 关键帧资产索引：单一来源 = self.state ----------
    # 原先这些既是实例属性、又单独写进 self.state，于是有两个真相：
    #   - 重启后 state.json 里载入的是 state 那份，实例属性仍是 []，白模条件
    #     （控制图 -> ref_images / 灰模动画 -> ref_video / depth 序列 -> Fun Control）
    #     会静默失效，日志上一片正常；
    #   - previs 合并后的关键帧只写实例属性、没回写 state，_save_state 存的是旧值。
    # 统一改成 property，读写都落到 self.state，两边不可能再漂移。
    @property
    def blocking_control(self) -> list:
        return self.state.get("blocking_control") or []

    @blocking_control.setter
    def blocking_control(self, value) -> None:
        self.state["blocking_control"] = list(value or [])

    @property
    def blocking_anim(self) -> list:
        return self.state.get("blocking_anim") or []

    @blocking_anim.setter
    def blocking_anim(self, value) -> None:
        self.state["blocking_anim"] = list(value or [])

    @property
    def blocking_fc(self) -> list:
        return self.state.get("blocking_fc") or []

    @blocking_fc.setter
    def blocking_fc(self, value) -> None:
        self.state["blocking_fc"] = list(value or [])

    @property
    def image_prompts(self) -> list:
        return self.state.get("image_prompts") or []

    @image_prompts.setter
    def image_prompts(self, value) -> None:
        self.state["image_prompts"] = list(value or [])

    @property
    def keyframe_images(self) -> list:
        return self.state.get("keyframe_images") or []

    @keyframe_images.setter
    def keyframe_images(self, value) -> None:
        self.state["keyframe_images"] = list(value or [])

    # ---------- 质检 / 投稿体检的配置入口 ----------
    def _character_anchor(self) -> str:
        """角色锚定图路径（相对路径按仓库根解析）。

        `normpath`：config 里惯用正斜杠（`outputs/anchor/...`），直接 join 会得到
        `...\\ai_movie_agent\\outputs/anchor/x.png` 这种混用分隔符。语义上等价，但
        与 ref_images 去重比较、日志、record 落盘都会带上两种写法，故统一。
        """
        anchor = ((self.config.get("series", {}) or {}).get("character_anchor")
                  or "outputs/anchor/mira_anchor.png")
        if not os.path.isabs(anchor):
            anchor = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), anchor)
        return os.path.normpath(anchor)

    def _qa_policy(self) -> tuple:
        """返回 (是否对 MovieAgent 出片链路启用质检, 策略)。

        默认**关**。WebUI / `cli run` 的逐镜续写链路一旦开启质检，就会多出打分开销
        与「换 seed 重 roll」（一次重 roll 就是几分钟 GPU），默认行为必须与既有出片
        完全一致，所以这里用独立开关 `qa.agent_enabled`，而不是复用 run_series 批量
        链路那份 `qa.enabled`（它历史上默认就是开的）。
        阈值仍复用 `qa.*` 同一段配置，不必维护两套。
        """
        from . import qa
        on = bool((self.config.get("qa") or {}).get("agent_enabled", False))
        return on, (qa.load_policy(self.config) if on else {})

    def _score_shot(self, path: str, n: int) -> dict:
        """给刚生成的镜头片段打分并判定，返回可直接落盘的 entry。"""
        from . import qa
        _, policy = self._qa_policy()
        # 「是否含主角」复用出片侧判据：本镜挂了关键帧/白模首帧即视为含角色。
        # 人脸类指标默认关闭（opencv 的 Haar 对动漫脸基本失效，见 qa.py 模块注释）。
        is_char = bool(self.keyframe_images[n] if n < len(self.keyframe_images) else None)
        score = qa.score_video(path, policy, is_char_shot=is_char,
                               anchor=self._character_anchor())
        ok, reasons = qa.evaluate(score, policy, is_char_shot=is_char)
        return {**score, "shot": f"scene_{n+1:03d}", "ok": ok, "reasons": reasons}

    def _preflight(self, video: str) -> dict:
        """投稿前体检（只读静态校验；读 outputs/state.json 组装标题/标签）。"""
        if not bool((self.config.get("preflight") or {}).get("enabled", True)):
            return {}
        from . import preflight as pf
        try:
            return pf.check_from_outputs(self.workdir, video=video)
        except Exception as e:          # noqa: BLE001 - 体检本身不该拖垮出片
            return {"ok": True, "errors": [],
                    "warnings": [f"体检执行失败（已忽略）: {e}"]}

    def publish_guard(self, video: str) -> tuple:
        """自动投稿前的门禁：返回 (是否放行, 体检结果)。

        只用于「配置开启自动投稿」的链路（finalize 的自动投稿、WebUI 一键全自动）。
        显式投稿（cli publish / 发布页点按钮）不走这里 —— 用户已经明确点了，不该被
        静默拦下。`preflight.block_publish: false` 可整体关掉拦截。
        """
        result = self._preflight(video)
        for e in (result or {}).get("errors", []):
            log(f"[agent] 投稿体检错误: {e}")
        for w in (result or {}).get("warnings", []):
            log(f"[agent] 投稿体检提醒: {w}")
        if not result:
            return True, {}
        block = bool((self.config.get("preflight") or {}).get("block_publish", True))
        return (not (block and not result.get("ok"))), result

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
        log(f"[agent] 第 {n+1} 镜: {beat.get('title')} | 提示词: {prompt}")

        if not self.engine.is_ready():
            log("[agent] 视频引擎未就绪，停止创作（请安装对应后端或检查 engine.backend 配置）。")
            return None
        prev = self.film if (n > 0 and os.path.exists(self.film)) else None
        # ---- 白模 -> 视频 流程闭环：控制图/灰模动画接入引擎 ----
        #   控制图(depth/normal/line) -> ref_images；灰模运镜 mp4 -> ref_video；
        #   白模 depth 序列 -> control_video(Fun Control)
        #   能力过滤由 filter_engine_kwargs 按引擎的 CAPABILITIES 声明完成
        #   （SkyReels/LTX 不支持则跳过并记日志，不报错）
        bcfg = self.config.get("blender", {}) or {}
        ref_images = None
        ref_video = None
        if bcfg.get("use_as_ref_images"):
            ctrl = self.blocking_control[n] if n < len(self.blocking_control) else None
            if isinstance(ctrl, dict):
                cands = [ctrl.get(k) for k in ("depth", "normal", "line")]
                ref_images = [p for p in cands if p and os.path.exists(p)] or None
        # 白模首帧(I2V start)是灰模，必须配角色锚定图作 ref_image(Hybrid)，否则 H3 会
        # 把灰模渲染成灰色角色。「use_as_i2v_start 开启」即表示本镜首帧已是白模 previs。
        if bcfg.get("use_as_i2v_start"):
            _anchor = self._character_anchor()
            if os.path.exists(_anchor) and _anchor not in (ref_images or []):
                ref_images = (ref_images or []) + [_anchor]
        if bcfg.get("use_as_ref_video"):
            anim = self.blocking_anim[n] if n < len(self.blocking_anim) else None
            if anim and str(anim).lower().endswith(".mp4") and os.path.exists(anim):
                # H3 参考视频走官方 2~15s 策略：过短的灰模动画喂不进去（运动引导无效或直接
                # 报错）。这里预检后不传，保证本镜照常出片；把 blender.anim_frames 设为 auto
                # 即可自动对齐出片帧数并兜底 2s。
                dur = self.editor.probe_duration(anim)
                floor = float(getattr(self.blocking, "MIN_REF_SECONDS", 2.0) or 2.0)
                if 0 < dur < floor:
                    log(f"  [agent] 跳过 ref_video：灰模动画 {dur:.2f}s 低于 {floor:g}s 下限"
                        f"（把 blender.anim_frames 设为 auto 可修）")
                else:
                    ref_video = anim
        # Fun Control 走位控制视频（白模 depth 序列，来自 blocking.render_assets 的 fcvideos）
        control_video = None
        if bcfg.get("use_as_fun_control"):
            fc = self.blocking_fc[n] if n < len(self.blocking_fc) else None
            if fc and str(fc).lower().endswith(".mp4") and os.path.exists(fc):
                control_video = fc
        # Fun Control 强度：白模走位序列 -> H3 逐帧注入（真正能控走位；ref_video 已证无效）
        fc_strength = None
        if control_video:
            try:
                fc_strength = float(bcfg.get("fun_control_strength", 1.2))
            except (TypeError, ValueError):
                fc_strength = None
        # 传什么由引擎**显式声明**（VideoEngine.CAPABILITIES），不再用
        # inspect.signature 反射猜签名：反射让补全/类型检查/静态分析全失效，
        # 新增能力还得改这里。被引擎忽略的条件照样记日志，避免「走位悄悄没生效」。
        extra, ignored = filter_engine_kwargs(
            self.engine,
            ref_images=ref_images, ref_video=ref_video,
            control_video=control_video, fc_strength=fc_strength)
        if extra:
            log("  [agent] 白模条件已接入引擎: " + ", ".join(sorted(extra)))
        if ignored:
            log(f"  [agent] {type(self.engine).__name__} 不支持的条件已忽略: "
                + ", ".join(sorted(ignored)))
        # ---- 逐镜生成（含可选质检重 roll，P2-⑬）----
        # 默认不质检（见 _qa_policy）；开启后：不达标就换 seed 重出（限次），仍不达标
        # 则采用最后一次并如实记录，绝不因为质检而中断整条出片链。
        tmp = os.path.join(self.scenes_dir, f"scene_{n+1:03d}.mp4")
        qa_on, qa_policy = self._qa_policy()
        if qa_on and seed is None:
            # 要重 roll 就必须有确定的基准 seed：否则每镜都换随机数，重 roll 之间
            # 不可比，事后也无法复现失败样本。这里现取一个并打进日志。
            seed = int(time.time()) & 0x7FFFFFFF
            log(f"  [qa] 未指定 seed，本次基准 seed={seed}（便于复现与换 seed 重 roll）")
        rolls = 1 + (max(0, int(qa_policy.get("max_rerolls") or 0)) if qa_on else 0)
        qa_score = None
        attempt = 0
        cur_seed = seed
        for attempt in range(rolls):
            cur_seed = seed if seed is None else seed + attempt * 7919
            self.engine.generate(prompt, tmp, prev_clip=prev, seed=cur_seed,
                                 image=keyframe, two_pass=self.engine.two_pass, **extra)
            if not qa_on:
                break
            qa_score = self._score_shot(tmp, n)
            qa_score["attempt"] = attempt
            if qa_score["ok"]:
                break
            if attempt + 1 < rolls:
                log(f"  [qa] 第 {n+1} 镜未达标（{'；'.join(qa_score['reasons'])}）"
                    f" → 换 seed 重 roll（{attempt + 1}/{rolls - 1}）")
            else:
                log(f"  [qa] 第 {n+1} 镜仍未达标（{'；'.join(qa_score['reasons'])}），"
                    f"采用本次结果继续")
        if qa_on:
            seed = cur_seed          # 落盘的 seed 必须是真正用上的那个

        # 备份当前长片，并把新片段设为影片（续写后的完整片）
        if n > 0 and os.path.exists(self.film):
            backup = os.path.join(self.scenes_dir, f"film_after_{n:03d}.mp4")
            try:
                shutil.copy(self.film, backup)
            except Exception:
                pass
        shutil.move(tmp, self.film)

        # 质检结果：增量落盘（含分布汇总），并把紧凑摘要写进 state 供 WebUI/看板读
        if qa_score:
            from . import qa as qa_mod
            self._qa_entries.append(qa_score)
            report = qa_mod.write_report(self.workdir, self._qa_entries)
            self.state["qa"] = {
                "total": len(self._qa_entries),
                "failed": sum(1 for e in self._qa_entries if not e.get("ok")),
                "last_ok": bool(qa_score.get("ok")),
                "last_attempts": attempt + 1,
                "report": os.path.basename(report) if report else "",
            }

        # 生成参数落盘（可复现 / 供 A/B 与回归）：含白模参考素材 ref_images / ref_video
        # 以及 Fun Control 的 control_video / strength —— 缺了 control_video 这一镜
        # 无法复现（它是当前唯一能锁走位的输入），故必须一并落盘。
        from . import record
        record.save(self.workdir, f"scene_{n+1:03d}", record.collect(
            self.engine, prompt=prompt,
            seed=seed if seed is not None else getattr(self.engine, "seed", None),
            attempt=attempt if qa_on else 0,
            image=keyframe, ref_images=extra.get("ref_images"),
            ref_video=extra.get("ref_video"),
            control_video=extra.get("control_video"),
            fc_strength=extra.get("fc_strength"),
            qa_policy=qa_policy if qa_on else None,
            qa_score=qa_score))

        self.state["beats"].append(beat)
        self.state["scene_count"] = n + 1
        self._save_state(self.state)
        self._log_beat(beat, prompt, self.film)
        return beat

    # ---------- 循环 ----------
    def run(self, continuous: bool = True, max_scenes: int | None = None,
            auto: bool = True, seed: int | None = None,
            topic: str | None = None, do_research: bool = False,
            should_stop=None) -> None:
        """持续创作循环。

        should_stop: 可选的无参回调，返回 True 表示请求停止（WebUI「停止」按钮）。
            注入式而非全局标志：CLI 不传即行为不变，也不会让两个并发任务互相干扰。
            检查粒度是「每镜」，因为单次引擎渲染是阻塞调用，无法在渲染中途打断；
            已生成的分镜与 film.mp4 全部保留。
        """
        title = self.config.get("project", {}).get("title", "未命名")
        topic = topic or self.config.get("project", {}).get("theme", "")
        log(f"=== 开始创作《{title}》===")

        def _stopped() -> bool:
            try:
                return bool(should_stop and should_stop())
            except Exception:          # 停止回调异常不应中断创作
                return False

        # ---- A→D 素材层 + 创意层前半 ----
        if do_research:
            material = self.collector.collect(topic)              # A 资料采集
            self.knowledge.ingest(material)                       # B 知识沉淀
            concept = self.planner.plan(topic, material, self.knowledge)  # C 概念企划
            concept = self.planner.enrich(concept, topic, self.knowledge)  # C+ 充实设定
            self.image_prompts = self.image_prompt.generate(concept)      # D 图像提示词
            self.keyframe_images = self.keyframe_gen.generate(self.image_prompts)  # D 关键帧出图
            self.state["bible"] = concept
            # Blender 白模分镜资产（previs / 控制图 / 灰模动画），未就绪则跳过
            if self.blocking.is_ready():
                log("[agent] 生成 Blender 白模分镜资产 ...")
                try:
                    blk = self.blocking.render_assets(self.image_prompts)
                except Exception as e:
                    log(f"[agent] 白模渲染异常，跳过（不影响出片）: {e}")
                    blk = {"previews": [], "controls": [], "anims": [], "fcvideos": []}
                self.state["blocking_previs"] = blk["previews"]
                # 三个 property 直接写进 self.state，无需再各写一份
                self.blocking_control = blk["controls"]
                self.blocking_anim = blk["anims"]
                self.blocking_fc = blk.get("fcvideos", [])
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
            log(f"世界观: {concept.get('logline', '')}")
            log(f"  规划分镜 {len(self.image_prompts)} 个关键帧提示词"
                  f"（已出图 {sum(1 for x in self.keyframe_images if x)} 张）")
        else:
            concept = self.state.get("bible") or self.writer.story_bible()
            self.state["bible"] = concept
            # image_prompts / keyframe_images / blocking_* 全由 property 直读 state，
            # 不再需要「再回填一份到实例属性」——原来的回填漏了 white-model 三个索引，
            # 正是白模条件静默失效的根因。
            log(f"世界观: {concept.get('logline', '')}")

        # ---- E→G→H 创意层后半 + 发布 ----
        # while not _stopped()：停止请求既能在「进入循环前」生效（企划阶段点了停止
        # 就不会再启动一次渲染），也能在每镜之间生效。
        try:
            while not _stopped():
                if max_scenes and self.state["scene_count"] >= max_scenes:
                    log(f"已达到目标分镜数 {max_scenes}，停止。")
                    break
                beat = self.generate_one_scene(seed=seed)
                if beat is None:
                    log("[agent] 引擎未就绪，已停止创作。")
                    break
                dur = self.editor.probe_duration(self.film)
                log(f"  [agent] 当前影片时长 ≈ {dur:.1f}s，"
                      f"共 {self.state['scene_count']} 镜 @ {self.film}")
                if not continuous:
                    break
                if not auto:
                    ans = input("继续创作下一镜? [y/N] ").strip().lower()
                    if ans not in ("y", "yes"):
                        break
        except KeyboardInterrupt:
            log("\n[agent] 用户中断，已保留当前影片。")
        if _stopped():
            log("[agent] 已按停止请求退出循环（已生成的分镜与 film.mp4 全部保留）。")
        self.finalize()

    def finalize(self) -> str:
        if not os.path.exists(self.film):
            return ""
        out = os.path.join(self.workdir, "movie_final.mp4")
        try:
            self.editor.finalize(self.film, out)
            log(f"[agent] 已封装最终影片: {out}")
        except Exception as e:
            log(f"[agent] 封装失败（不影响原始影片）: {e}")
            out = self.film
        # 自动投稿 B 站（config.publish.enabled 时）
        if self.publisher.enabled:
            # 投稿前体检：不通过就拦下自动投稿（config.preflight.block_publish=false 可放行）。
            # 成片本身已经产出并保留，用户修完标题/封面后仍可手工作废。
            allow, _pf = self.publish_guard(out)
            if not allow:
                log("[agent] 体检未通过，已拦截自动投稿"
                    "（成片已保留；可修正后用 `python cli.py publish` 手动投稿，"
                    "或设 config.preflight.block_publish=false 放行）")
                return out
            res = self.publisher.publish_latest(self.state, out)
            if res.get("ok"):
                log(f"[agent] 已投稿 B 站: {res['title']}")
            else:
                log(f"[agent] 投稿失败: {res.get('error')}")
                if "login" in str(res.get("error", "")).lower() \
                        or "cookie" in str(res.get("error", "")).lower():
                    log(self.publisher.login_guide())
        return out

    def publish_only(self, video: str) -> dict:
        """单独发布某个已存在的影片（供 cli publish 子命令使用）。"""
        ep = self.state.get("scene_count", 1) or 1
        film_title = self.state.get("title", "未命名")
        template = self.publisher.cfg.get("title_template", "{title} · 第{n}集")
        title = self.publisher._fill(template, title=film_title, n=ep)
        title = title.replace(f"第{ep}集", f"第{Publisher._cn_episode(ep)}集")
        return self.publisher.upload(
            video,
            episode=ep,
            title=title,
            film_title=film_title,
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
