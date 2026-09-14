"""视频引擎：ComfyUI + MiniMax H3（默认 Turbo 4 步），替代 / 并列 LTX-2.5。

与其它引擎共用统一契约（见 agent/video_engine.py），可被 run_series.py（三集出片）
与 agent.py（G 阶段）透明替换。本引擎 CAPABILITIES 覆盖全部白模条件
（ref_images / ref_video / control_video / fc_strength）。

H3 相对 LTX 的两大差异：
  - 原生音视频联合生成（自带立体声），无需外接音轨；
  - 配 Turbo LoRA 后 4 步即可出片（LTX 需 20~30 步）。

两个硬约束已在属性 setter 里自动修正（否则管线里传入非法值会静默出错）：
  1. width/height 必须被 32 整除 —— MiniMaxH3AudioConditioningT8 会直接
     抛 ValueError("MiniMax H3 width and height must be divisible by 32")；
  2. length 必须落在 17n+5 网格（5, 22, 39, 56, 73, 90, 107, 124 ...）——
     节点会向上吸附，这里显式吸附，保证 eng.num_frames 与真实出片帧数一致，
     否则 run_series.concat_shots 用 num_frames/fps 算单镜时长会整体错位。

配置（config.engine.comfyui_mmH3）：
    api           ComfyUI 地址，默认 http://127.0.0.1:8188
    unet / text_encoder / video_vae / audio_vae   权重文件名
    lora          Turbo 加速 LoRA 文件名（置空=关闭 Turbo）
    video_steps / audio_steps   Turbo 步数（默认 4 / 8）
    steps         非 Turbo 时的统一步数（默认 30）
    resolution    "WxH"，默认 768x448
    fps / num_frames / seed
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from tools.comfyui_client import ComfyUIClient

from . import comfyui_post
from .llmutil import log
from .node_ids import NodeAllocator
from .video_engine import H3_SECTION_ALIASES, VideoEngine, pick_engine_section

#: H3 默认权重文件名（放 ComfyUI 对应 models 子目录）。
#: 抽成常量而不是散在构造函数里：升级权重只改一处，也便于比对「跑的是哪一版」。
DEFAULT_MODELS = {
    "unet": "minimax_h3_fl2va_pruned_int4_convrot.safetensors",
    "text_encoder": "qwen3vl_32b_minimax_h3_int4_convrot.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
    "lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    "fun_control_net":
        "minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors",
    "latent_upscaler": "minimax_h3_latent_upscaler_3d_fp16.safetensors",
}


class MMH3Engine(VideoEngine):
    # H3 的条件节点要求分辨率 32 整除、长度落在 17n+5 网格
    ALIGN = 32
    LEN_BASE = 5
    LEN_STEP = 17

    TAG = "mmh3"
    #: H3 是唯一支持全部白模条件的引擎（多参考图 / 参考视频 / Fun Control 走位）
    CAPABILITIES = frozenset({"ref_images", "ref_video",
                              "control_video", "fc_strength"})

    def __init__(self, config: dict, agent_root: str = ""):
        # 配置段别名（comfyui_mmH3 / minimax_h3 / comfyui_h3）统一由
        # pick_engine_section 解析：原先各模块各写一遍 or 链，blocking.py 甚至
        # 只认 comfyui_mmH3，别名配置下白模控制视频帧数会悄悄错位。
        h3 = pick_engine_section(config or {}, *H3_SECTION_ALIASES)
        self.h3 = h3
        eng = (config or {}).get("engine", {}) or {}
        self.api = h3.get("api") or eng.get("api") or "http://127.0.0.1:8188"
        self.unet = h3.get("unet") or DEFAULT_MODELS["unet"]
        self.text_encoder = h3.get("text_encoder") or DEFAULT_MODELS["text_encoder"]
        self.video_vae = h3.get("video_vae") or DEFAULT_MODELS["video_vae"]
        self.audio_vae = h3.get("audio_vae") or DEFAULT_MODELS["audio_vae"]
        # Turbo LoRA：给 pruned 架构做的，形状与量化位数无关，故 pruned INT4 可直接用
        # 注意用 is None 判断（与其它权重不同）：显式置空串 = 关闭 Turbo
        self.lora = h3.get("lora")
        if self.lora is None:
            self.lora = DEFAULT_MODELS["lora"]
        self.lora_strength = float(h3.get("lora_strength", 1.0))
        self.video_steps = int(h3.get("video_steps", 4))
        self.audio_steps = int(h3.get("audio_steps", 8))
        self.steps = int(h3.get("steps", 30))
        self.shift_video = float(h3.get("shift_video", 12.0))
        self.shift_audio = float(h3.get("shift_audio", 3.0))
        self.fps = int(h3.get("fps", 24))
        self.seed = int(h3.get("seed", 0))
        self.timeout = int(h3.get("timeout", 1800))
        self.filename_prefix = h3.get("filename_prefix") or "H3/pipe"

        # 后处理（质量增强）：默认全部关闭，避免无超分模型/自定义节点时影响默认管线。
        #   post.upscale_model: ESRGAN 模型文件名（放 ComfyUI models/upscale_models），
        #                       留空 = 不做超分；如 "4x-UltraSharp.pth"
        #   post.sharpen:       0~1，>0 启用内置 ImageSharpen 锐化；0 = 关闭
        self.post_upscale, self.post_sharpen = comfyui_post.read_post_cfg(h3.get("post"))

        # BlockCache（缓存加速）：H3 专用 F1B0 residual 缓存，目标音视频都稳定时
        # 跳过 Block 1-49，只重算 Block 0。依赖自定义节点 comfyui-minimax-h3-blockcache-T8
        # （节点 MiniMaxH3BlockCacheT8）。与 SageAttention 兼容（不替换 H3 Block）。
        # 近似缓存：不保证同 seed 无损，运动小的镜头加速明显；默认关闭。
        bc = h3.get("block_cache") or {}
        self.block_cache = bool(bc.get("enable", False))
        self.bc_threshold = float(bc.get("threshold", 0.12))
        self.bc_cache_device = str(bc.get("cache_device", "cpu"))

        # 学习型 latent 二采放大：一采低清 → 3D latent upscaler 放大 → 二采高清。
        # 依赖 H3 内置二采节点（MiniMaxH3LearnedLatentUpscaleT8Advanced 等）+ 模型
        # DEFAULT_MODELS["latent_upscaler"]（放 ComfyUI models/latent_upscale_models）。
        # EXP 路线：单样本不证画质增益；开启后一采走 DualClock（非 Turbo 双速率）。
        tp = h3.get("two_pass") or {}
        self.two_pass_latent = bool(tp.get("enable", False))
        self.tp_upscaler = str(tp.get("model_name") or DEFAULT_MODELS["latent_upscaler"])
        self.tp_scale = float(tp.get("scale_by", 1.5))
        self.tp_base = int(tp.get("base_steps", 8))
        self.tp_coarse = int(tp.get("coarse_steps", 4))
        self.tp_refine = int(tp.get("refine_steps", 4))

        # Fun Control（控制走位）：白模 depth/pose 序列作逐帧稠密条件注入 DiT，
        # 依赖 H3 内置节点 MiniMaxH3FunControl*T8Advanced + 控制权重（放 ComfyUI
        # models/model_patches 或 controlnet）。默认关闭；generate(control_video=...)
        # 亦可逐镜覆盖。实测 strength≈0.8~1.2（≥1.5 画面崩坏）。
        fcfg = h3.get("fun_control") or {}
        self.fun_control_enable = bool(fcfg.get("enable", False))
        self.fc_control_net = str(
            fcfg.get("control_net") or DEFAULT_MODELS["fun_control_net"])
        self.fc_control_kind = str(fcfg.get("control_kind", "depth"))
        self.fc_fit_mode = str(fcfg.get("fit_mode", "exact"))
        self.fc_strength = float(fcfg.get("strength", 0.8))
        self.fc_end_percent = float(fcfg.get("end_percent", 0.85))
        self.fc_video = str(fcfg.get("video") or "")

        self._num_frames = self.snap_length(int(h3.get("num_frames", 56)))
        self._resolution = self.snap_resolution(h3.get("resolution", "768x448"))

        self.client = ComfyUIClient(self.api, timeout=self.timeout)
        self.two_pass = False  # H3 自带时长/运动控制，不需要 SkyReels 式两遍续写

    # ---------- 约束修正 ----------
    @classmethod
    def snap_length(cls, n: int) -> int:
        """把帧数向上吸附到 17n+5 网格（H3 的条件节点要求）。"""
        n = max(int(n), cls.LEN_BASE)
        k = -(-(n - cls.LEN_BASE) // cls.LEN_STEP)  # ceil
        return cls.LEN_BASE + k * cls.LEN_STEP

    @classmethod
    def snap_resolution(cls, res: str) -> str:
        """把 WxH 的每个维度向下取整到 32 的倍数（最小 32）。"""
        try:
            w, h = (int(x) for x in str(res).lower().split("x"))
        except Exception:
            w = h = 0
        w = max(cls.ALIGN, (max(w, cls.ALIGN) // cls.ALIGN) * cls.ALIGN)
        h = max(cls.ALIGN, (max(h, cls.ALIGN) // cls.ALIGN) * cls.ALIGN)
        return f"{w}x{h}"

    @property
    def num_frames(self) -> int:
        return self._num_frames

    @num_frames.setter
    def num_frames(self, v):
        snapped = self.snap_length(v)
        if snapped != v:
            log(f"  [mmh3] 帧数 {v} 不在 17n+5 网格，已吸附为 {snapped}")
        self._num_frames = snapped

    @property
    def resolution(self) -> str:
        return self._resolution

    @resolution.setter
    def resolution(self, v):
        snapped = self.snap_resolution(v)
        if snapped != str(v):
            log(f"  [mmh3] 分辨率 {v} 非 32 整除，已修正为 {snapped}")
        self._resolution = snapped

    @property
    def turbo(self) -> bool:
        return bool(self.lora)

    # ---------- 接口 ----------
    def is_ready(self) -> bool:
        return self.client.is_ready()

    def generate(self, prompt: str, out_path: str, prev_clip: str | None = None,
                 seed: int | None = None, image: str | None = None,
                 two_pass: bool | None = None,
                 ref_video: str | None = None,
                 ref_images: list | None = None,
                 control_video: str | None = None,
                 fc_strength: float | None = None) -> str:
        """生成一段视频。

        ref_video: 参考视频（白模走位等），经 VHS_LoadVideoPath 加载成 IMAGE 帧批次后
            接到条件节点的 ref_videos。与 image(首帧) 同时给则走 Hybrid：
            首帧锁形象/场景，参考视频锁走位与镜头运动。
        ref_images: 额外参考图（最多 9 张），接 ref_images.ref_image_i。
            Hybrid 下只靠 1 张 first_frame 锁形象时，身份信号会被参考视频的运动
            信号压过导致人物形态崩坏；补多张同角色参考图可显著增强身份一致性。
        control_video: 白模逐帧控制视频（depth/pose 等），经 Fun Control 注入 DiT
            锁定走位/构图（与 ref_video 不同：ref_video 已证传不动运动，Fun Control 可以）。
            帧数须 ≥ num_frames 且落 17n+5 网格；geometry 须与出片一致（fit_mode=exact）。
            传入的路径不可用会**直接报错**（不再静默忽略，避免走位悄悄失效）。
        fc_strength: Fun Control 强度覆盖（默认取 config，实测 0.8~1.2，≥1.5 崩坏）。
        """
        # 前置校验放在 is_ready 之前：控制视频/强度这类入参错误应先于
        # 「ComfyUI 未就绪」报出来，否则现场容易误判成服务问题。
        self._validate_control_video(control_video, fc_strength)
        if not self.client.is_ready():
            raise RuntimeError(
                "ComfyUI 未就绪：请启动 ComfyUI（8188）并安装 comfyui-minimax-h3-audio-T8 "
                "与 ComfyUI-VideoHelperSuite 节点。"
            )
        wf = self._build_workflow(prompt, seed, image, ref_video, ref_images,
                                  control_video=control_video, fc_strength=fc_strength)
        dest = os.path.dirname(os.path.abspath(out_path)) or "."
        os.makedirs(dest, exist_ok=True)
        w, h = (int(x) for x in self.resolution.split("x"))
        desc = (f"Turbo {self.video_steps}v/{self.audio_steps}a"
                if self.turbo else f"{self.steps}步")
        # 提交日志印**真实**任务类型：原先只按 image 有无印 I2VA/T2VA，
        # 会把白模的 Ref2VA / Hybrid 误印成 T2VA / I2VA，排查时极易误判。
        def _exists(p):
            return bool(p and os.path.exists(p))

        _has_img = _exists(image)
        _has_any_ref = (_exists(ref_video)
                        or any(_exists(p) for p in (ref_images or [])))
        task = self.resolve_task(_has_img, _has_any_ref)
        log(f"  [mmh3] 提交 H3 工作流（{desc}, {w}x{h}, {self.num_frames}帧 "
            f"@{self.fps}fps, {task}）")
        paths = self.client.run_workflow(wf, dest, timeout=self.timeout)
        videos = [p for p in paths
                  if p.lower().endswith((".mp4", ".webm", ".mov"))]
        if not videos:
            raise RuntimeError(
                "MiniMax H3 未产出视频，请检查 VHS_VideoCombine 是否接到 "
                "MiniMaxH3AVDecodeT8 的 IMAGE / AUDIO 输出。"
            )
        # VHS 同时给出带音轨与无声版时优先带音轨的（H3 原生音频是主要卖点）
        with_audio = [p for p in videos if "audio" in os.path.basename(p).lower()]
        src = max(with_audio or videos, key=os.path.getmtime)
        if os.path.abspath(src) != os.path.abspath(out_path):
            shutil.move(src, out_path)
        log(f"  [mmh3] 已生成片段: {out_path}")
        return out_path

    # ---------- workflow ----------
    @staticmethod
    def resolve_task(has_img: bool, has_any_ref: bool) -> str:
        """任务类型判定（唯一来源：_build_workflow 与实际提交日志共用）。

        Hybrid : 首帧(image) + 任意参考媒体(ref_video / ref_images)
        Ref2VA : 只有参考媒体、无首帧 —— 白模 ref_video / ref_images 走这条
        I2VA   : 只有首帧（I2VA 禁止携带任何参考媒体，节点会抛错）
        T2VA   : 纯文生视频
        """
        if has_img and has_any_ref:
            return "Hybrid"
        if has_any_ref:
            return "Ref2VA"
        if has_img:
            return "I2VA"
        return "T2VA"

    # ---------- Fun Control 前置校验 ----------
    @staticmethod
    def _probe_frame_count(path: str) -> int | None:
        """尽力探测视频帧数：优先 ffprobe 的 nb_frames，否则 时长×帧率。

        本机 ffmpeg 来自 imageio-ffmpeg（不带 ffprobe），所以探测不到时返回 None，
        调用方降级为「只打日志、不阻断」，绝不让校验本身成为新的失败源。
        """
        probe = shutil.which("ffprobe")
        if not probe:
            return None
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=nb_frames,duration,avg_frame_rate",
                 "-of", "json", path],
                capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                return None
            streams = (json.loads(out.stdout or "{}") or {}).get("streams") or []
            if not streams:
                return None
            st = streams[0]
            nf = str(st.get("nb_frames") or "")
            if nf.isdigit() and int(nf) > 0:
                return int(nf)
            dur, rate = st.get("duration"), str(st.get("avg_frame_rate") or "")
            if dur and "/" in rate:
                num, den = (float(x) for x in rate.split("/", 1))
                if den > 0:
                    return int(round(float(dur) * num / den))
        except Exception:                 # noqa: BLE001 - 探测失败一律视为未知
            return None
        return None

    def _validate_control_video(self, control_video: str | None,
                                fc_strength: float | None = None) -> None:
        """Fun Control 入参校验，把三类「静默失败」挡在提交 ComfyUI 之前。

        1) 调用方**显式**传了 control_video 但文件不可用 —— 原实现直接当没传，
           整镜悄悄不加控制，走位错得毫无提示（与 blocking 里「杜绝静默失败」同源）；
        2) 控制视频比出片短 —— FunControlApply 的 frame_load_cap 会截断，只锁住
           前几帧，走位只对一半，肉眼极难发现；
        3) strength 越界 —— 实测 ≥1.5 画面崩坏。
        """
        fc_video = control_video or self.fc_video
        if control_video:
            usable = bool(fc_video) and os.path.isfile(fc_video)
            if usable:
                try:
                    usable = os.path.getsize(fc_video) > 0
                except OSError:
                    usable = False
            if not usable:
                raise RuntimeError(
                    f"Fun Control 控制视频不可用（不存在或为空）: {control_video}"
                    "；若本镜不需要走位控制，请不要传 control_video。")
        if not (fc_video and os.path.isfile(fc_video)):
            return
        strength = self.fc_strength if fc_strength is None else float(fc_strength)
        if strength <= 0:
            raise RuntimeError(f"Fun Control strength 必须 > 0，当前 {strength}")
        if strength >= 1.5:
            log(f"  [mmh3] 警告: Fun Control strength={strength} ≥ 1.5，"
                f"实测画面会崩坏（建议 0.8~1.2）")
        got = self._probe_frame_count(fc_video)
        if got is None:
            log(f"  [mmh3] 控制视频帧数未知（无 ffprobe），跳过帧数校验: "
                f"{os.path.basename(fc_video)}")
            return
        if got < self.num_frames:
            raise RuntimeError(
                f"Fun Control 控制视频仅 {got} 帧 < 出片 {self.num_frames} 帧："
                f"H3 会按 frame_load_cap 截断，走位只能锁住前 {got} 帧（控制形同虚设）。"
                f"请让白模控制序列对齐出片帧数（blender.anim_frames=auto）: {fc_video}")
        if got > self.num_frames:
            log(f"  [mmh3] 控制视频 {got} 帧 → 按出片 {self.num_frames} 帧截断")

    def _build_workflow(self, prompt: str, seed: int | None,
                        image: str | None,
                        ref_video: str | None = None,
                        ref_images: list | None = None,
                        control_video: str | None = None,
                        fc_strength: float | None = None) -> dict:
        """直接拼 API Format 工作流（不依赖外部 json，避免节点 ID 漂移）。

        节点 ID 一律经 NodeAllocator 按语义名分配，不再出现裸数字：分配器保证
        名字唯一、ID 唯一，构建完成后再 audit() 一次，杜绝撞号——历史上
        ref_images 的 20~28 与后处理的 30/31/32 撞号会形成依赖环。
        """
        ids = NodeAllocator()
        seed = seed if seed is not None else self.seed
        w, h = (int(x) for x in self.resolution.split("x"))
        has_img = bool(image and os.path.exists(image))
        has_ref = bool(ref_video and os.path.exists(ref_video))
        # ref_images 也是「参考媒体」，必须计入 has_refs。否则只传 image+ref_images
        # 时 task 会被判成 I2VA，而 H3 的 I2VA 禁止携带任何参考媒体
        # （conditioning.py: resolve_task_type → "I2VA cannot include reference
        # media; use Auto or Hybrid"），直接抛错。计入后走 Hybrid 即可，
        # 且无需白模 ref_video（避免参考视频的运动信号压过身份）。
        has_refimg = bool([p for p in (ref_images or [])
                           if p and os.path.exists(p)])
        has_any_ref = has_ref or has_refimg
        # 任务类型：首帧锁形象/场景，参考视频锁走位与镜头运动，同时给走 Hybrid
        task = self.resolve_task(has_img, has_any_ref)

        n_unet = ids.alloc("unet")
        n_clip = ids.alloc("clip")
        n_vae_v = ids.alloc("vae_video")
        n_vae_a = ids.alloc("vae_audio")
        nodes: dict = {
            n_unet: {"class_type": "UNETLoader",
                     "inputs": {"unet_name": self.unet, "weight_dtype": "default"}},
            n_clip: {"class_type": "CLIPLoader",
                     "inputs": {"clip_name": self.text_encoder, "type": "minimax"}},
            n_vae_v: {"class_type": "VAELoader", "inputs": {"vae_name": self.video_vae}},
            n_vae_a: {"class_type": "VAELoader", "inputs": {"vae_name": self.audio_vae}},
        }
        # 模型源：Turbo 时经 LoRA 注入
        if self.turbo:
            n_lora = ids.alloc("lora")
            nodes[n_lora] = {"class_type": "LoraLoaderBypassModelOnly",
                             "inputs": {"model": [n_unet, 0], "lora_name": self.lora,
                                        "strength_model": self.lora_strength}}
            model_src = [n_lora, 0]
        else:
            model_src = [n_unet, 0]

        # BlockCache（缓存加速）：插在模型源与采样器之间。仅当 config 开启时接入，
        # 缺失节点时 ComfyUI 会报 class_type 不存在，故默认关闭。
        if self.block_cache:
            n_bc = ids.alloc("block_cache")
            nodes[n_bc] = {"class_type": "MiniMaxH3BlockCacheT8", "inputs": {
                "model": model_src,
                "residual_diff_threshold": self.bc_threshold,
                "start_percent": 0.08, "end_percent": 0.95,
                "max_consecutive_hits": 2,
                "cache_device": self.bc_cache_device,
                "metric_stride": 8, "verbose": False}}
            model_src = [n_bc, 0]

        # 条件节点（I2VA 时挂首帧）
        cond_in = {
            "clip": ["3", 0], "video_vae": ["4", 0], "audio_vae": ["5", 0],
            "prompt": prompt, "width": w, "height": h, "length": self.num_frames,
            "task_type": task,
            "audio_mode": "native", "audio_denoise_strength": 1.0,
            "add_source_as_reference": False, "prompt_primary_audio_ordinal": 0,
            "strict_prompt_tags": True, "ref_image_size": "match",
            "reference_video_policy": "official_2_to_15s",
        }
        if has_img:
            meta = self.client.upload_image(image)
            img_name = (meta or {}).get("name") or (meta or {}).get("filename")
            if not img_name:
                raise RuntimeError(f"起始帧上传失败: {image}")
            n_first = ids.alloc("first_frame")
            nodes[n_first] = {"class_type": "LoadImage", "inputs": {"image": img_name}}
            cond_in["first_frame"] = [n_first, 0]
        if has_ref:
            # 参考视频：本地绝对路径直接加载（免上传），输出 IMAGE 帧批次接 ref_videos
            n_refv = ids.alloc("ref_video")
            nodes[n_refv] = {"class_type": "VHS_LoadVideoPath", "inputs": {
                "video": os.path.abspath(ref_video),
                "force_rate": float(self.fps),
                "custom_width": 0, "custom_height": 0,
                "frame_load_cap": 0, "skip_first_frames": 0,
                "select_every_nth": 1,
            }}
            # Autogrow 在 API prompt 里是**带父级前缀的路径键**（finalize_prefix 用 "." 连接）：
            #   f"{autogrow_input_id}.{prefix}{i}"，i 从 0 开始 → "ref_videos.ref_video_0"
            # 写成嵌套 dict 或裸 ref_video_1 都会被丢弃（节点不执行，报
            # "requires at least one reference media input"）。
            cond_in["ref_videos.ref_video_0"] = [n_refv, 0]
        # 多参考图（最多 9 张）：增强身份/形象信号，缓解 Hybrid 下人物形态崩坏。
        # 同样是 Autogrow：键名带父级前缀 ref_images.ref_image_i，i 从 0 开始。
        for i, rp in enumerate((ref_images or [])[:9]):
            if not (rp and os.path.exists(rp)):
                continue
            meta = self.client.upload_image(rp)
            nm = (meta or {}).get("name") or (meta or {}).get("filename")
            if not nm:
                continue
            nid = ids.alloc_range("ref_image", i)    # 20..28，区间由分配器保证不越界
            nodes[nid] = {"class_type": "LoadImage", "inputs": {"image": nm}}
            cond_in[f"ref_images.ref_image_{i}"] = [nid, 0]
        n_cond = ids.alloc("cond")
        nodes[n_cond] = {"class_type": "MiniMaxH3AudioConditioningT8", "inputs": cond_in}

        # 采样器：Turbo 走双速率（4 视频 / 8 音频），否则统一步数双时钟。
        # 二采模式下一采固定用 DualClock（统一 sigma 轨迹 → ParityPlan 切 coarse/refine）。
        n_sampler = ids.alloc("sampler")
        if self.two_pass_latent:
            nodes[n_sampler] = {"class_type": "MiniMaxH3DualClockSamplerT8", "inputs": {
                "model": model_src, "av_latent": [n_cond, 1], "steps": self.tp_base,
                "shift_video": self.shift_video, "shift_audio": self.shift_audio,
                "sampler_name": "dual_clock_euler", "scheduler": "native_flow"}}
            # 二采 sigma 计划：coarse 段给一采，refine 段给二采（base = coarse + refine）
            n_par = ids.alloc("tp_parity")
            nodes[n_par] = {"class_type": "MiniMaxH3LearnedTwoPassParityPlanT8Advanced",
                            "inputs": {"model": [n_sampler, 0], "base_steps": self.tp_base,
                                       "coarse_steps": self.tp_coarse,
                                       "refine_steps": self.tp_refine}}
            first_sigmas = [n_par, 0]
        elif self.turbo:
            nodes[n_sampler] = {"class_type": "MiniMaxH3MultiRateSamplerEXPT8", "inputs": {
                "model": model_src, "av_latent": [n_cond, 1],
                "video_steps": self.video_steps, "audio_steps": self.audio_steps,
                "shift_video": self.shift_video, "shift_audio": self.shift_audio}}
            first_sigmas = [n_sampler, 2]
        else:
            nodes[n_sampler] = {"class_type": "MiniMaxH3DualClockSamplerT8", "inputs": {
                "model": model_src, "av_latent": [n_cond, 1], "steps": self.steps,
                "shift_video": self.shift_video, "shift_audio": self.shift_audio,
                "sampler_name": "dual_clock_euler", "scheduler": "native_flow"}}
            first_sigmas = [n_sampler, 2]
        # 一采 guider model：Turbo / 二采 => 采样器 wrapper 输出(7.0)；非 Turbo 直连模型源
        guider_model = ([n_sampler, 0] if (self.turbo or self.two_pass_latent)
                        else model_src)

        # ---------- Fun Control：把「走位/构图」真正交给白模 ----------
        # 逐帧 depth/pose 控制视频经 FunControlApply 注入 DiT(第0/10/20/30/40层)。
        # 实测：能把走位方向从 H3 默认「左移」纠正为「跟随白模」；strength 0.8~1.2，≥1.5 崩坏。
        cond_src = [n_cond, 0]
        fc_video = control_video or self.fc_video
        if ((self.fun_control_enable or control_video)
                and fc_video and os.path.exists(fc_video)):
            fc_str = self.fc_strength if fc_strength is None else float(fc_strength)
            n_fc_l = ids.alloc("fc_loader")
            n_fc_v = ids.alloc("fc_video")
            n_fc_a = ids.alloc("fc_apply")
            nodes[n_fc_l] = {"class_type": "MiniMaxH3FunControlLoaderT8Advanced",
                             "inputs": {"control_net_name": self.fc_control_net}}
            nodes[n_fc_v] = {"class_type": "VHS_LoadVideoPath", "inputs": {
                "video": os.path.abspath(fc_video),
                "force_rate": float(self.fps),
                "custom_width": w, "custom_height": h,
                "frame_load_cap": self.num_frames, "skip_first_frames": 0,
                "select_every_nth": 1}}
            nodes[n_fc_a] = {"class_type": "MiniMaxH3FunControlApplyT8Advanced",
                             "inputs": {"model": guider_model, "positive": [n_cond, 0],
                                        "control_net": [n_fc_l, 0], "vae": [n_vae_v, 0],
                                        "control_video": [n_fc_v, 0],
                                        "width": w, "height": h, "length": self.num_frames,
                                        "control_kind": self.fc_control_kind,
                                        "fit_mode": self.fc_fit_mode,
                                        "strength": fc_str,
                                        "start_percent": 0.0,
                                        "end_percent": self.fc_end_percent}}
            guider_model = [n_fc_a, 0]
            cond_src = [n_fc_a, 1]
            log(f"  [mmh3] Fun Control 注入：{os.path.basename(fc_video)} "
                f"({self.fc_control_kind}, strength={fc_str}, end={self.fc_end_percent})")

        # ---------- 后处理（质量增强，由 config.engine.comfyui_mmH3.post 控制）----------
        # decode(11) 输出 [IMAGE 帧批次, AUDIO]；音频不动，只增强图像分辨率/锐度。
        # 帧插值(RIFE)故意不接此处：会改变帧率导致音画不同步，作为离线增强单独提供。
        # 具体实现统一在 agent/comfyui_post.py（与 LTX-2.5 共用）。
        n_decode = ids.alloc("decode")
        images_src = [n_decode, 0]
        if self.post_upscale or self.post_sharpen > 0:
            images_src = comfyui_post.build_post_nodes(
                nodes, images_src,
                upscale=self.post_upscale, sharpen=self.post_sharpen,
                alloc=ids.alloc)          # 30/31/32，由分配器保证不与 20~28 撞号

        n_guider = ids.alloc("guider")
        n_noise = ids.alloc("noise")
        n_final = ids.alloc("sampler_final")
        nodes.update({
            n_guider: {"class_type": "BasicGuider",
                       "inputs": {"model": guider_model, "conditioning": cond_src}},
            n_noise: {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            n_final: {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": [n_noise, 0], "guider": [n_guider, 0],
                "sampler": [n_sampler, 1],
                "sigmas": first_sigmas, "latent_image": [n_cond, 1]}},
        })
        # 解码源：单采用一采结果；二采用二采采样器
        decoded_src = [n_final, 0]
        if self.two_pass_latent:
            # 二采：一采 denoised(10.1) → 3D latent 放大 → 高清 Conditioning
            #      → Reconcile → DetailMixer → 二采采样
            # 放大必须接一采的 denoised_output(1)，不能用中间噪声状态的 output(0)。
            n_tp_up = ids.alloc("tp_upscale")
            n_cond2 = ids.alloc("cond2")
            n_rec = ids.alloc("tp_reconcile")
            n_mix = ids.alloc("tp_mixer")
            n_g2 = ids.alloc("tp_guider")
            n_n2 = ids.alloc("tp_noise")
            n_s2 = ids.alloc("tp_sampler")
            nodes[n_tp_up] = {"class_type": "MiniMaxH3LearnedLatentUpscaleT8Advanced",
                              "inputs": {
                                  "av_latent": [n_final, 1], "model_name": self.tp_upscaler,
                                  "size_mode": "scale_by", "scale_by": self.tp_scale,
                                  "target_megapixels": 1.0, "target_width": 1024,
                                  "target_height": 576, "aspect_policy": "preserve_source",
                                  "max_anisotropy": 1.05, "precision": "fp16",
                                  "release_policy": "offload_after"}}
            # 二采 Conditioning：同 prompt/参考媒体，宽高接放大输出的 width/height
            cond2 = dict(cond_in)
            cond2.pop("width", None)
            cond2.pop("height", None)
            cond2["width"] = [n_tp_up, 1]
            cond2["height"] = [n_tp_up, 2]
            nodes[n_cond2] = {"class_type": "MiniMaxH3AudioConditioningT8",
                              "inputs": cond2}
            nodes[n_rec] = {"class_type": "MiniMaxH3TwoPassLatentReconcileT8Advanced",
                            "inputs": {
                                "learned_latent": [n_tp_up, 0],
                                "highres_template": [n_cond2, 1], "positive": [n_cond2, 0],
                                "audio_policy": "auto",
                                "second_pass_audio_source": "legacy_policy",
                                "second_pass_audio_strength": 0.0}}
            # DetailMixer 的 model 用原始模型源（非一采采样器 wrapper 输出）
            nodes[n_mix] = {"class_type": "MiniMaxH3TwoPassDetailMixerT8Advanced",
                            "inputs": {
                                "model": model_src, "av_latent": [n_rec, 0],
                                "refine_sigmas": [n_par, 1],
                                "shift_video": self.shift_video,
                                "shift_audio": self.shift_audio,
                                "enable_tail": False, "extra_tail_steps": 3,
                                "tail_spacing": "video_sigma_linear",
                                "enable_model_time_bias": False, "bias": -0.025,
                                "bias_start_progress": 0.7, "bias_end_progress": 0.95,
                                "bias_domain": "video_sigma",
                                "enable_stg": False, "stg_scale": 0.35,
                                "stg_double_blocks": "25",
                                "stg_start_progress": 0.25, "stg_end_progress": 0.85,
                                "enable_restart": False, "restart_video_sigma": 0.15,
                                "restart_steps": 3, "restart_seed": seed}}
            nodes[n_g2] = {"class_type": "BasicGuider",
                           "inputs": {"model": [n_mix, 0], "conditioning": [n_rec, 1]}}
            nodes[n_n2] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
            nodes[n_s2] = {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": [n_n2, 0], "guider": [n_g2, 0], "sampler": [n_mix, 1],
                "sigmas": [n_mix, 2], "latent_image": [n_rec, 0]}}
            decoded_src = [n_s2, 0]
        n_combine = ids.alloc("combine")
        nodes.update({
            n_decode: {"class_type": "MiniMaxH3AVDecodeT8", "inputs": {
                "av_latent": decoded_src, "video_vae": [n_vae_v, 0],
                "audio_vae": [n_vae_a, 0]}},
            n_combine: {"class_type": "VHS_VideoCombine", "inputs": {
                "images": images_src, "audio": [n_decode, 1], "frame_rate": self.fps,
                "filename_prefix": self.filename_prefix,
                "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 18,
                "loop_count": 0, "pingpong": False, "save_output": True}},
        })
        ids.audit(nodes)      # 自检：无未登记 / 无悬空 ID
        return nodes
