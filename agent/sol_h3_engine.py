"""视频引擎：远程 DGX Spark 上的 Sol-H3-Spark（NVIDIA Speed-of-Light MiniMax H3）。

与其它引擎共用统一契约（见 agent/video_engine.py）：
    generate(prompt, out_path, prev_clip=None, seed=None, image=None, two_pass=None,
             ref_video=None, ref_images=None, control_video=None, fc_strength=None)
因此可被 run_series.py（三集出片）与 agent.py（G 阶段）透明替换。
本引擎 CAPABILITIES = {ref_images, ref_video}，无 Fun Control 走位能力。

本机 agent 不直接跑 Sol-H3，而是经 HTTP 调 DGX Spark 上
deploy/sol_h3_spark/sol_h3_server.py 封装的服务（服务内部 subprocess 调 infer.py）。

Sol-H3 与 ComfyUI 版 H3 的关键差异：
  - 输出固定 1344x768 / 121 帧 / 24fps，模型常驻（无 --resolution/--num-frames/
    --fps/--offload/--lora CLI）；LoRA 写死在官方配置表。
  - 任务映射（由服务端按输入自动判定）：
        image(首帧)             -> fl2va  (--first-frame)
        ref_images/ref_video   -> ref2va (--reference image:/video:)
        两者皆有                -> fl2va + 附参考（best-effort，服务端记录日志）
        都无                    -> t2va
  - prev_clip（续写）Sol-H3 不支持，静默忽略（同 MMH3 行为）。
"""
from __future__ import annotations

import os

from tools.sol_h3_client import SolH3Client

from .llmutil import log
from .video_engine import SOL_H3_SECTION_ALIASES, VideoEngine, pick_engine_section


class SolH3Engine(VideoEngine):
    # Sol-H3-Spark 固定输出（官方写死，不可 CLI 覆盖）
    RES_W, RES_H = 1344, 768
    NUM_FRAMES = 121
    FPS = 24

    TAG = "sol_h3"
    #: 支持参考图 / 参考视频（映射到 Ref2VA 的 --reference）；无 Fun Control 走位能力
    CAPABILITIES = frozenset({"ref_images", "ref_video"})

    def __init__(self, config: dict, agent_root: str = ""):
        h3 = pick_engine_section(config or {}, *SOL_H3_SECTION_ALIASES)
        eng = (config or {}).get("engine", {}) or {}
        self.api = h3.get("api") or eng.get("api") or "http://127.0.0.1:8000"
        self.paths = h3.get("paths") or "paths.json"
        self.seed = int(h3.get("seed", 0))
        self.timeout = int(h3.get("timeout", 1800))
        # 只读展示属性（record.collect 会读），与 Sol-H3 固定输出一致
        self.resolution = h3.get("resolution") or f"{self.RES_W}x{self.RES_H}"
        self.num_frames = int(h3.get("num_frames") or self.NUM_FRAMES)
        self.fps = int(h3.get("fps") or self.FPS)
        self.steps = None
        self.lora = None
        self.negative = None
        self.two_pass = False
        self.block_cache = False
        self.client = SolH3Client(self.api, timeout=self.timeout)

    def is_ready(self) -> bool:
        return self.client.health()

    def generate(self, prompt: str, out_path: str, prev_clip: str | None = None,
                 seed: int | None = None, image: str | None = None,
                 two_pass: bool | None = None,
                 ref_video: str | None = None,
                 ref_images: list | None = None,
                 control_video: str | None = None,
                 fc_strength: float | None = None) -> str:
        """生成一段视频，写入 out_path，返回 out_path。

        ref_images：额外参考图（最多若干张），映射到 Ref2VA 的 --reference image:。
        ref_video ：参考视频，映射到 Ref2VA 的 --reference video:。
        两者与首帧 image 同时存在时，服务端走 fl2va + 附参考（best-effort）。
        control_video / fc_strength：Sol-H3 无 Fun Control 能力，会记日志后忽略。
        """
        self._warn_ignored(control_video=control_video, fc_strength=fc_strength)
        if not self.client.is_ready():
            raise RuntimeError(
                f"Sol-H3 服务未就绪：请确认 DGX Spark 上的 sol_h3_server.py 已启动"
                f"（{self.api}）。可在 DGX 上 `curl {self.api}/health` 自检。"
            )
        seed = seed if seed is not None else self.seed
        has_img = bool(image and os.path.exists(image))
        refs: list = []
        for p in (ref_images or []):
            if p and os.path.exists(p):
                refs.append(("image", p))
        if ref_video and os.path.exists(ref_video):
            refs.append(("video", ref_video))

        task = "fl2va" if has_img else ("ref2va" if refs else "t2va")
        log(f"  [sol_h3] 提交 Sol-H3（DGX Spark, {task}, "
            f"{self.resolution}, {self.num_frames}帧@{self.fps}fps"
            f"{', 带首帧' if has_img else ''}"
            f"{', 参考x' + str(len(refs)) if refs else ''}）")
        data = self.client.generate(
            prompt, seed=seed,
            first_frame=image if has_img else None,
            references=refs, task="auto")
        dest = os.path.dirname(os.path.abspath(out_path)) or "."
        os.makedirs(dest, exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(data)
        log(f"  [sol_h3] 已生成片段: {out_path}")
        return out_path
