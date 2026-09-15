"""视频引擎：ComfyUI + LTX-2.5（NVFP4 量化）替代 SkyReels-V2。

与 SkyReelsEngine 保持同一接口：
    generate(prompt, out_path, prev_clip=None, seed=None, image=None, two_pass=None)
因此可被 agent.py 透明替换为 G 阶段视频引擎。

工作原理：
- 通过 ComfyUI 跑 LTX-2.5 文生视频(T2V) / 图生视频(I2V)；
- Blackell（RTX 50 系）上把模型加载精度设为 fp4（NVFP4），利用 5 代 Tensor Core 提速；
- 提示词(prompt)由上游本地 LLM（Ollama）产出，经 Director 灌入本引擎，
  形成「本地大模型写提示词 → ComfyUI 跑 LTX-2.5(FP4) → 视频」的闭环。

配置（config.engine.comfyui_ltx）：
    api         ComfyUI 地址，默认 http://127.0.0.1:8188
    workflow    LTX-2.5 的 ComfyUI API Format workflow JSON 路径（必填）
    precision   "fp4"(NVFP4) / "fp8" / "default"，默认 fp4
    resolution  "WxH"，默认 768x768
    fps / num_frames / seed

workflow 注入策略（best-effort，无需硬编码节点名）：
- 把 prompt 填入任意含 text / positive 字符串输入的节点；
- 把帧率 / 帧数 / 分辨率 / seed 填入同名输入；
- 把 precision 填入模型加载节点的 precision 输入（启用 NVFP4）；
- 若提供 image，上传后以文件名填入 image 输入节点做 I2V。
用户应在自己的 ComfyUI 里把 LTX-2.5 工作流“另存为 API Format”后指向本配置，
节点字段名只要匹配上述约定即可被正确注入。
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import struct
import tempfile
import zlib

from tools.comfyui_client import ComfyUIClient

from . import comfyui_post
from . import prompting
from .llmutil import log
from .video_engine import VideoEngine


# ---------- workflow 注入用的纯函数（放模块级，避免嵌套函数堆高圈复杂度） ----------

def _pick_node(wf: dict, nodes_cfg: dict, role: str,
               *class_types: str) -> dict | None:
    """按配置的语义名取节点；未配置时按 class_type 兜底找第一个匹配节点。"""
    nid = nodes_cfg.get(role)
    if nid and nid in wf:
        return wf[nid]
    for ct in class_types:
        for nd in wf.values():
            if isinstance(nd, dict) and nd.get("class_type") == ct:
                return nd
    return None


def _set_in(node: dict | None, key: str, value) -> None:
    """给节点的 inputs[key] 赋值（节点为 None 时静默跳过）。"""
    if node is not None:
        node.setdefault("inputs", {})[key] = value


def _inject_unet(wf: dict, nodes_cfg: dict, gguf: str, checkpoint: str) -> None:
    """transformer 注入：配了 unet_gguf 就换成 UnetLoaderGGUF，否则 UNETLoader。

    GGUF 节点只接受 unet_name，故整体替换 inputs，避免遗留 UNETLoader 字段。
    """
    nd = _pick_node(wf, nodes_cfg, "unet", "UNETLoader")
    if nd is None:
        return
    if gguf:
        nd["class_type"] = "UnetLoaderGGUF"
        nd["inputs"] = {"unet_name": gguf}
    else:
        _set_in(nd, "unet_name", checkpoint)


def _inject_clip(wf: dict, nodes_cfg: dict, gguf: str, text_encoder: str) -> None:
    """文本编码器注入：配了 clip_gguf 换 CLIPLoaderGGUF，否则 CLIPLoader + int8。"""
    nd = _pick_node(wf, nodes_cfg, "clip_12b", "CLIPLoader")
    if nd is None:
        return
    if gguf:
        nd["class_type"] = "CLIPLoaderGGUF"
        nd["inputs"] = {"clip_name": gguf,
                        "type": (nd.get("inputs") or {}).get("type", "ltx")}
    else:
        _set_in(nd, "clip_name", text_encoder)


def _inject_resolution(wf: dict, nodes_cfg: dict, resolution: str, snap) -> None:
    """分辨率注入：吸附到 32 整除并提示。

    LTX-2.5 要求宽高被 32 整除，非法值会到 ComfyUI 才报错；这里先吸附并提示，
    避免「提交成功但出片尺寸不对」的隐蔽问题。
    """
    try:
        rw, rh = (int(x) for x in resolution.lower().split("x"))
    except Exception:
        return
    if not (rw and rh):
        return
    srw, srh = snap(rw), snap(rh)
    if (srw, srh) != (rw, rh):
        log(f"  [ltx] 分辨率 {rw}x{rh} 非 32 整除，已吸附为 {srw}x{srh}")
    latent = _pick_node(wf, nodes_cfg, "latent", "EmptyLTXVLatentVideo")
    _set_in(latent, "width", srw)
    _set_in(latent, "height", srh)


def _cloud_branch_nodes(wf: dict, nodes_cfg: dict) -> set:
    """云端/增强提示词分支的全部节点 ID（编码器 + 相关开关 + 2B 编码器）。"""
    delete = {nid for nid, n in wf.items()
              if n.get("class_type") in ("GemmaAPITextEncode",
                                         "TextGenerateLTX2Prompt")}
    delete |= _upstream_switch_ids(wf, delete)
    delete |= _named_ids(wf, nodes_cfg, "clip_2b", "prompt_switch")
    return delete


def _upstream_switch_ids(wf: dict, targets: set) -> set:
    """找出 on_true/on_false 指向 targets 的 ComfySwitchNode。"""
    out: set = set()
    for nid, n in wf.items():
        if n.get("class_type") != "ComfySwitchNode":
            continue
        for k in ("on_true", "on_false"):
            v = (n.get("inputs") or {}).get(k)
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in targets:
                out.add(nid)
    return out


def _named_ids(wf: dict, nodes_cfg: dict, *keys: str) -> set:
    """按语义名取节点 ID，仅保留确实存在于 wf 的。"""
    out: set = set()
    for key in keys:
        nid = nodes_cfg.get(key)
        if nid and nid in wf:
            out.add(nid)
    return out


def _dangling(node: dict, key: str, delete: set) -> bool:
    """node.inputs[key] 是否指向被删除的节点（或压根没接）。"""
    src = (node.get("inputs") or {}).get(key)
    if src is None:
        return True
    return isinstance(src, list) and len(src) == 2 and str(src[0]) in delete


def _rewire_guider(wf: dict, cond) -> None:
    """CFGGuider 的 positive/negative 改接本地 conditioning。"""
    if not cond:
        return
    for n in wf.values():
        if n.get("class_type") == "CFGGuider":
            n["inputs"]["positive"] = [cond, 0]
            n["inputs"]["negative"] = [cond, 0]


def _rewire_after_strip(wf: dict, delete: set, prompt_id: str) -> None:
    """把删掉云端分支后悬空的连线重接到本地提示词/conditioning。"""
    cond = next((nid for nid, n in wf.items()
                 if n.get("class_type") == "LTXVConditioning"), None)
    _rewire_guider(wf, cond)
    # 提示词来源与 PreviewAny 重指到本地提示词，避免悬空引用
    for n in wf.values():
        if n.get("class_type") == "CLIPTextEncode" and _dangling(n, "text", delete):
            n["inputs"]["text"] = [prompt_id, 0]
        elif n.get("class_type") == "PreviewAny" and _dangling(n, "source", delete):
            n["inputs"]["source"] = [prompt_id, 0]


class LTXEngine(VideoEngine):
    TAG = "ltx"
    #: LTX-2.5 是 T2V/I2V 引擎，白模条件（参考图/参考视频/Fun Control）全不支持。
    CAPABILITIES = frozenset()

    def __init__(self, config: dict, agent_root: str = ""):
        eng = config.get("engine", {}) or {}
        ltx = eng.get("comfyui_ltx", {}) or {}
        self.ltx = ltx
        self.api = ltx.get("api") or eng.get("api") or "http://127.0.0.1:8188"
        self.workflow_path = ltx.get("workflow", "")
        self.precision = (ltx.get("precision") or "fp4").lower()
        self.fps = int(ltx.get("fps", 24))
        self.num_frames = int(ltx.get("num_frames", 121))
        self.resolution = ltx.get("resolution", "960x544")
        self.seed = int(ltx.get("seed", 0))
        self.checkpoint = (ltx.get("checkpoint")
                           or "ltx-2.5-22b-distilled-transformer-nvfp4.safetensors")
        self.text_encoder = (ltx.get("text_encoder")
                             or "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors")
        # 16GB 显存专用：官方最小组合（NVFP4 18.7G + int8 编码器 15.4G）合计 34GB+，
        # 16GB 卡唯一路径是社区 GGUF 量化，由 ComfyUI-GGUF 的 UnetLoaderGGUF 加载。
        # 配了 unet_gguf 时 transformer 走 GGUF（文件放 models/diffusion_models 或 models/unet）。
        self.unet_gguf = ltx.get("unet_gguf") or ""
        self.clip_gguf = ltx.get("clip_gguf") or ""
        # LTX-2.5 的音/视频 VAE 是两个独立权重，缺任一都会在
        # LTXVEmptyLatentAudio 处抛 'PixelspaceConversionVAE' has no attribute
        # 'latent_frequency_bins'（工作流里的 VAELoader 会退化成 pixel_space）
        self.video_vae = (ltx.get("video_vae")
                          or "ltx-2.5-video-vae-bf16.safetensors")
        self.audio_vae = (ltx.get("audio_vae")
                          or "ltx-2.5-audio-vae-bf16.safetensors")
        # 负向提示词：引擎级 > config.prompting.negative > 默认 quality 预设
        self.negative = prompting.resolve_negative(
            config, engine_negative=ltx.get("negative") or "")
        self.client = ComfyUIClient(self.api)
        self.two_pass = False  # LTX 自带时长/运动控制，不依赖 SkyReels 式两遍续写

    def is_ready(self) -> bool:
        # ComfyUI 必须在线；workflow 在 generate 时再校验（便于给出明确报错）
        return self.client.is_ready()

    def generate(self, prompt: str, out_path: str, prev_clip: str | None = None,
                 seed: int | None = None, image: str | None = None,
                 two_pass: bool | None = None,
                 ref_video: str | None = None,
                 ref_images: list | None = None,
                 control_video: str | None = None,
                 fc_strength: float | None = None) -> str:
        # 签名与 MiniMax H3 引擎保持一致（见 agent/video_engine.py）；本引擎不支持
        # 白模条件，统一由 _warn_ignored 记一条日志，绝不静默丢弃。
        self._warn_ignored(ref_images=ref_images, ref_video=ref_video,
                           control_video=control_video, fc_strength=fc_strength)
        if not self.client.is_ready():
            raise RuntimeError(
                "ComfyUI 未就绪：请启动 ComfyUI 并安装 LTX-2.5 节点（见 docs/ltx_comfyui_nvfp4.md）。"
            )
        if not self.workflow_path or not os.path.exists(self.workflow_path):
            raise RuntimeError(
                f"未配置 LTX-2.5 workflow：config.engine.comfyui_ltx.workflow 指向的\n"
                f"  {self.workflow_path or '(空)'}\n不存在。请在 ComfyUI 导出 API Format 工作流后填入路径。"
            )
        wf = self._build_workflow(prompt, seed, image)
        wf = self._apply_post(wf)
        dest = os.path.dirname(os.path.abspath(out_path))
        model_desc = f"GGUF:{self.unet_gguf}" if self.unet_gguf else self.checkpoint
        log(f"  [ltx] 提交 LTX-2.5 工作流（{model_desc}, precision={self.precision}, "
            f"{self.num_frames} 帧 @ {self.fps}fps, {'I2V' if image else 'T2V'}）")
        paths = self.client.run_workflow(wf, dest, timeout=1800)
        videos = [p for p in paths
                  if p.lower().endswith((".mp4", ".webm", ".mov", ".gif"))]
        if not videos:
            raise RuntimeError(
                "LTX-2.5 未产出视频，请检查 ComfyUI workflow 的输出（SaveAnimatedWEBM / "
                "VideoCombine 等）节点是否连接。"
            )
        src = max(videos, key=os.path.getmtime)
        os.makedirs(dest, exist_ok=True)
        if os.path.abspath(src) != os.path.abspath(out_path):
            shutil.move(src, out_path)
        log(f"  [ltx] 已生成片段: {out_path}")
        return out_path

    # ---------- workflow 构建 / 注入 ----------
    @staticmethod
    def _snap_dim(v: int) -> int:
        """把维度向下吸附到 32 的倍数（最小 32），LTX-2.5 的硬性要求。"""
        return max(32, (max(int(v), 32) // 32) * 32)

    def _build_workflow(self, prompt: str, seed: int | None, image: str | None) -> dict:
        with open(self.workflow_path, encoding="utf-8") as f:
            wf = json.load(f)
        wf = self._inject(wf, prompt, seed, image)
        return self._strip_cloud_prompt_branch(wf)

    AUDIO_VAE_USERS = {"LTXVEmptyLatentAudio", "LTXVAudioVAEDecode"}

    def _resolve_vae_nodes(self, wf: dict) -> dict:
        """定位音频 VAE / 视频 VAE 各自的 VAELoader 节点：{"audio": id, "video": id}。

        LTX-2.5 工作流里两个 VAELoader 长得一模一样（转换后都退化成默认的
        "pixel_space"），只能靠下游连线区分：被 LTXVEmptyLatentAudio /
        LTXVAudioVAEDecode 引用的那个才是音频 VAE。config.comfyui_ltx.nodes
        里给了显式映射时优先用映射。
        """
        nodes_cfg = self.ltx.get("nodes") or {}
        loaders = [nid for nid, n in wf.items()
                   if isinstance(n, dict) and n.get("class_type") == "VAELoader"]
        out: dict[str, str] = {}
        for role, key in (("audio", "audio_vae"), ("video", "video_vae")):
            nid = nodes_cfg.get(key)
            if nid in wf and wf[nid].get("class_type") == "VAELoader":
                out[role] = nid
        if "audio" in out and "video" in out:
            return out

        audio_link = None
        for n in wf.values():
            if isinstance(n, dict) and n.get("class_type") in self.AUDIO_VAE_USERS:
                src = (n.get("inputs") or {}).get("audio_vae")
                if isinstance(src, list) and len(src) == 2:
                    audio_link = str(src[0])
                    break
        if audio_link is None:
            return out
        out.setdefault("audio", audio_link)
        others = [nid for nid in loaders if nid != audio_link]
        if others:
            out.setdefault("video", others[0])
        return out

    def _inject(self, wf: dict, prompt: str, seed: int | None,
                image: str | None) -> dict:
        """按 config.comfyui_ltx.nodes 的节点 ID 注入参数（兼容无映射时按类型兜底）。

        各参数的注入细节拆到模块级 _inject_* 里；本函数只做编排。
        """
        wf = copy.deepcopy(wf)
        seed = seed if seed is not None else self.seed
        nodes_cfg = self.ltx.get("nodes") or {}

        _inject_unet(wf, nodes_cfg, self.unet_gguf, self.checkpoint)
        _inject_clip(wf, nodes_cfg, self.clip_gguf, self.text_encoder)

        # 音视频 VAE：两个 VAELoader 必须分别填各自权重（不能都落默认值 pixel_space）
        vae_nodes = self._resolve_vae_nodes(wf)
        if "audio" in vae_nodes:
            wf[vae_nodes["audio"]].setdefault("inputs", {})["vae_name"] = self.audio_vae
        if "video" in vae_nodes:
            wf[vae_nodes["video"]].setdefault("inputs", {})["vae_name"] = self.video_vae

        # 提示词 / 负向提示词（PrimitiveStringMultiline.value）
        _set_in(_pick_node(wf, nodes_cfg, "prompt", "PrimitiveStringMultiline"),
                "value", prompt)
        _set_in(_pick_node(wf, nodes_cfg, "negative", "PrimitiveStringMultiline"),
                "value", self.negative or "")

        # 帧率 / 时长（秒）= 帧数 / fps
        _set_in(_pick_node(wf, nodes_cfg, "fps", "PrimitiveFloat"), "value", self.fps)
        _set_in(_pick_node(wf, nodes_cfg, "duration", "PrimitiveFloat"),
                "value", round(self.num_frames / self.fps, 3))

        # 随机种子
        _set_in(_pick_node(wf, nodes_cfg, "seed", "RandomNoise"), "noise_seed", seed)

        _inject_resolution(wf, nodes_cfg, self.resolution, self._snap_dim)

        # 帧数：latent 的总帧数 = length(或 frames_number) × batch_size。
        # 本 workflow 把视频/音频两个 latent 的 batch_size 都硬编码成 121
        # （与 config.num_frames 同值的遗留写法），而 length 由 duration×fps 推导，
        # 二者相乘会把时长放大 121 倍
        # （实测 --frames 33 产出了 121×33=3993 帧 / 166 秒）。
        # 这里把两个 latent 的 batch_size 都强制为 1，使总帧数 == config.num_frames。
        # 注意必须同时改两个：只改视频会让 AV 拼接形状不匹配而报
        # "Sizes of tensors must match ... Expected size 1 but got size 121"。
        _set_in(_pick_node(wf, nodes_cfg, "latent", "EmptyLTXVLatentVideo"),
                "batch_size", 1)
        _set_in(_pick_node(wf, nodes_cfg, "audio_latent", "LTXVEmptyLatentAudio"),
                "batch_size", 1)

        # 图像分支：I2V 用真实起始帧；T2V 上传占位图仅用于让 LoadImage 通过校验
        self._inject_image_branch(wf, nodes_cfg, image)
        # 提示词来源：始终走本地提示词（5508），不依赖 Gemma API（需 api_key）
        _set_in(_pick_node(wf, nodes_cfg, "prompt_switch", "ComfySwitchNode"),
                "switch", False)
        return wf

    def _inject_image_branch(self, wf: dict, nodes_cfg: dict,
                             image: str | None) -> bool:
        """图像分支注入，返回是否使用真实起始帧（I2V）。

        I2V：上传真实起始帧；T2V：上传占位图（图像链由 bypass 控制，占位图不影响成片）。
        """
        limg = _pick_node(wf, nodes_cfg, "load_image", "LoadImage")
        has_img = bool(image and os.path.exists(image))
        if limg is not None:
            meta = self.client.upload_image(image) if has_img else self._upload_placeholder()
            img_name = (meta or {}).get("name") or (meta or {}).get("filename")
            if img_name:
                limg.setdefault("inputs", {})["image"] = img_name
        # I2V 使能：有图=True（使用起始帧），无图=False（T2V bypass）
        _set_in(_pick_node(wf, nodes_cfg, "i2v_enable", "PrimitiveBoolean"),
                "value", has_img)
        return has_img

    def _upload_placeholder(self) -> dict:
        """上传 1x1 占位图（T2V 下让 LoadImage 通过 ComfyUI 校验）。"""
        ph = os.path.join(tempfile.gettempdir(), "ltx_placeholder.png")
        self._make_placeholder_png(ph)
        return self.client.upload_image(ph)

    def _apply_post(self, wf: dict) -> dict:
        """在最终视频保存节点前插入质量后处理（超分 + 锐化）。

        默认只开轻度锐化（post.sharpen>0）；超分需配置 post.upscale_model 模型名。

        实现统一在 agent/comfyui_post.py（与 MiniMax H3 共用），避免两处各写一遍
        再次出现「节点 ID 冲突 / ImageSharpen 参数名错」这类问题。
        """
        upscale, sharpen = comfyui_post.read_post_cfg(self.ltx.get("post"))
        if not upscale and sharpen <= 0:
            return wf
        sid = comfyui_post.find_video_saver(wf)
        if not sid:
            log("  [ltx] 未找到视频保存节点，跳过质量后处理")
            return wf
        # 用户工作流的节点 ID 是任意的（如 5508 / 5014_5506），固定 ID 可能撞上真实节点，
        # 故从高位起找空闲 ID。
        bases = {"upscale_loader": 90001, "upscale_apply": 90002, "sharpen": 90003}

        def _alloc(name: str) -> str:
            n = bases[name]
            while str(n) in wf:
                n += 1
            return str(n)

        if comfyui_post.apply_post(wf, sid, upscale=upscale, sharpen=sharpen,
                                  alloc=_alloc) is None:
            return wf
        log(f"  [ltx] 已接入质量后处理：{comfyui_post.describe(upscale, sharpen)}")
        return wf

    def _make_placeholder_png(self, path: str) -> None:
        """生成 1x1 纯黑 RGBA PNG（仅用于 T2V 下让 LoadImage 通过 ComfyUI 校验）。"""
        raw = b"\x00\x00\x00\x00\x00"  # filter=0 + 1 像素 RGBA(黑)
        idat = zlib.compress(raw)

        def _chunk(typ, data):
            return struct.pack(">I", len(data)) + typ + data + struct.pack(
                ">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

        png = (b"\x89PNG\r\n\x1a\n"
               + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
               + _chunk(b"IDAT", idat)
               + _chunk(b"IEND", b""))
        with open(path, "wb") as f:
            f.write(png)

    def _strip_cloud_prompt_branch(self, wf: dict) -> dict:
        """剔除云端/增强提示词分支（GemmaAPITextEncode + TextGenerateLTX2Prompt +
        相关开关 + 2B 文本编码器），使管线纯本地：只用 5508/5509 提示词。

        这些节点在 switch=False 下本就被绕过，但 ComfyUI 仍会校验其（缺失的）模型
        输入；剔除后管线只依赖 transformer(nvfp4) + 12B 文本编码器，更省显存/下载，
        且与“本地 LLM 写提示词”的目标一致（无需云 api_key / 额外大模型）。
        """
        nodes_cfg = self.ltx.get("nodes") or {}
        delete = _cloud_branch_nodes(wf, nodes_cfg)
        if not delete:
            return wf
        # 重接：本地 conditioning -> CFGGuider；本地提示词 -> CLIPTextEncode
        prompt_id = nodes_cfg.get("prompt", "5508")
        _rewire_after_strip(wf, delete, prompt_id)
        for nid in delete:
            wf.pop(nid, None)
        return wf
