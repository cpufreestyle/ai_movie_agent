"""视频引擎：ComfyUI + MiniMax H3（默认 Turbo 4 步），替代 / 并列 LTX-2.5。

与 LTXEngine / SkyReelsEngine 保持同一接口：
    generate(prompt, out_path, prev_clip=None, seed=None, image=None, two_pass=None)
因此可被 run_series.py（三集出片）与 agent.py（G 阶段）透明替换。

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

import os
import shutil

from tools.comfyui_client import ComfyUIClient
from .llmutil import log


class MMH3Engine:
    # H3 的条件节点要求分辨率 32 整除、长度落在 17n+5 网格
    ALIGN = 32
    LEN_BASE = 5
    LEN_STEP = 17

    def __init__(self, config: dict, agent_root: str = ""):
        eng = config.get("engine", {}) or {}
        h3 = (eng.get("comfyui_mmH3") or eng.get("minimax_h3")
              or eng.get("comfyui_h3") or {})
        self.h3 = h3
        self.api = h3.get("api") or eng.get("api") or "http://127.0.0.1:8188"
        self.unet = (h3.get("unet")
                     or "minimax_h3_fl2va_pruned_int4_convrot.safetensors")
        self.text_encoder = (h3.get("text_encoder")
                             or "qwen3vl_32b_minimax_h3_int4_convrot.safetensors")
        self.video_vae = (h3.get("video_vae")
                          or "minimax_h3_video_vae_fp16.safetensors")
        self.audio_vae = (h3.get("audio_vae")
                          or "minimax_h3_audio_vae_fp32.safetensors")
        # Turbo LoRA：给 pruned 架构做的，形状与量化位数无关，故 pruned INT4 可直接用
        self.lora = h3.get("lora")
        if self.lora is None:
            self.lora = "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors"
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
                 two_pass: bool | None = None) -> str:
        if not self.client.is_ready():
            raise RuntimeError(
                "ComfyUI 未就绪：请启动 ComfyUI（8188）并安装 comfyui-minimax-h3-audio-T8 "
                "与 ComfyUI-VideoHelperSuite 节点。"
            )
        wf = self._build_workflow(prompt, seed, image)
        dest = os.path.dirname(os.path.abspath(out_path)) or "."
        os.makedirs(dest, exist_ok=True)
        w, h = (int(x) for x in self.resolution.split("x"))
        desc = (f"Turbo {self.video_steps}v/{self.audio_steps}a"
                if self.turbo else f"{self.steps}步")
        log(f"  [mmh3] 提交 H3 工作流（{desc}, {w}x{h}, {self.num_frames}帧 "
            f"@{self.fps}fps, {'I2VA' if image else 'T2VA'}）")
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
    def _build_workflow(self, prompt: str, seed: int | None,
                        image: str | None) -> dict:
        """直接拼 API Format 工作流（不依赖外部 json，避免节点 ID 漂移）。"""
        seed = seed if seed is not None else self.seed
        w, h = (int(x) for x in self.resolution.split("x"))
        has_img = bool(image and os.path.exists(image))

        nodes: dict = {
            "1": {"class_type": "UNETLoader",
                  "inputs": {"unet_name": self.unet, "weight_dtype": "default"}},
            "3": {"class_type": "CLIPLoader",
                  "inputs": {"clip_name": self.text_encoder, "type": "minimax"}},
            "4": {"class_type": "VAELoader",
                  "inputs": {"vae_name": self.video_vae}},
            "5": {"class_type": "VAELoader",
                  "inputs": {"vae_name": self.audio_vae}},
        }
        # 模型源：Turbo 时经 LoRA 注入
        if self.turbo:
            nodes["2"] = {"class_type": "LoraLoaderBypassModelOnly",
                          "inputs": {"model": ["1", 0], "lora_name": self.lora,
                                     "strength_model": self.lora_strength}}
            model_src = ["2", 0]
        else:
            model_src = ["1", 0]

        # 条件节点（I2VA 时挂首帧）
        cond_in = {
            "clip": ["3", 0], "video_vae": ["4", 0], "audio_vae": ["5", 0],
            "prompt": prompt, "width": w, "height": h, "length": self.num_frames,
            "task_type": "I2VA" if has_img else "T2VA",
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
            nodes["13"] = {"class_type": "LoadImage", "inputs": {"image": img_name}}
            cond_in["first_frame"] = ["13", 0]
        nodes["6"] = {"class_type": "MiniMaxH3AudioConditioningT8", "inputs": cond_in}

        # 采样器：Turbo 走双速率（4 视频 / 8 音频），否则统一步数双时钟
        if self.turbo:
            nodes["7"] = {"class_type": "MiniMaxH3MultiRateSamplerEXPT8", "inputs": {
                "model": model_src, "av_latent": ["6", 1],
                "video_steps": self.video_steps, "audio_steps": self.audio_steps,
                "shift_video": self.shift_video, "shift_audio": self.shift_audio}}
        else:
            nodes["7"] = {"class_type": "MiniMaxH3DualClockSamplerT8", "inputs": {
                "model": model_src, "av_latent": ["6", 1], "steps": self.steps,
                "shift_video": self.shift_video, "shift_audio": self.shift_audio,
                "sampler_name": "dual_clock_euler", "scheduler": "native_flow"}}
        # Turbo 路线的 model 由采样器输出（T8 官方工作流接法）；非 Turbo 直连模型源
        guider_model = ["7", 0] if self.turbo else model_src

        nodes.update({
            "8": {"class_type": "BasicGuider",
                  "inputs": {"model": guider_model, "conditioning": ["6", 0]}},
            "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "10": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["9", 0], "guider": ["8", 0], "sampler": ["7", 1],
                "sigmas": ["7", 2], "latent_image": ["6", 1]}},
            "11": {"class_type": "MiniMaxH3AVDecodeT8", "inputs": {
                "av_latent": ["10", 0], "video_vae": ["4", 0],
                "audio_vae": ["5", 0]}},
            "12": {"class_type": "VHS_VideoCombine", "inputs": {
                "images": ["11", 0], "audio": ["11", 1], "frame_rate": self.fps,
                "filename_prefix": self.filename_prefix,
                "format": "video/h264-mp4", "pix_fmt": "yuv420p", "crf": 18,
                "loop_count": 0, "pingpong": False, "save_output": True}},
        })
        return nodes
