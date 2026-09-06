#!/usr/bin/env python
"""LTX-2.3 多镜头成片：按《看见未来之前》科幻短片生成 18 镜完整短片（约 61 秒）并拼接。

与 Wan2.2 版的关键区别：
  1. LTX-2.3 是**真 T2V**——用 EmptyLTXVLatentVideo 直接吃 prompt，
     不需要起始图，也就不必做"上一镜尾帧 -> I2V 续写"那一套。
  2. LTX-2.3 **音视频联合生成**，每镜都自带音轨，因此拼接时必须同时处理
     音频（video 用 xfade，audio 用 acrossfade），这是 Wan 版没有的。

用法:
  python run_ltx23_multishot.py              # 依次生成全部镜头并拼接
  python run_ltx23_multishot.py --shot 3     # 只生成第 3 镜（断点续跑）
  python run_ltx23_multishot.py --concat     # 只做拼接（镜头已生成完）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import requests

S = requests.Session()
S.trust_env = False  # 避免把 127.0.0.1 也走代理(7897)导致 502


def _detect_comfy_url() -> str:
    """自动探测本机 ComfyUI 端口。

    历史坑：本脚本曾硬编码 8200，而本机 ComfyUI 常驻 8188，结果每镜都
    "ComfyUI 不可达"、脚本却仍以 0 退出，上层 WebUI 因此把失败渲染当成成功，
    继续拿几天前的旧成片配音封装，用户看到的是老片且毫不知情。
    改为依次探测常见端口，杜绝端口漂移导致的静默失败。
    """
    explicit = os.environ.get("COMFY_URL")
    if explicit:
        return explicit.rstrip("/")
    for port in (8188, 8200, 8189, 8288):
        u = f"http://127.0.0.1:{port}"
        try:
            if S.get(u + "/", timeout=3).status_code == 200:
                print(f"[comfy] 探测到 ComfyUI @ {u}")
                return u
        except Exception:
            continue
    print("[comfy] 未探测到 ComfyUI(8188/8200/8189/8288)，将按 8188 尝试")
    return "http://127.0.0.1:8188"


URL = _detect_comfy_url()

OUT_DIR = "D:/ComfyUI/output"          # ComfyUI 输出根目录
ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "outputs", "shots")
os.makedirs(WORK, exist_ok=True)
MANIFEST = os.path.join(WORK, "ltx23_film_manifest.json")

# ---- 模型 ----
UNET = "ltx-2.3-22b-distilled-1.1-Q3_K_M.gguf"
VIDEO_VAE = "ltx-2.3-video-vae.safetensors"
AUDIO_VAE = "ltx-2.3-dev-audio-vae.safetensors"
TEXT_ENC = "gemma_3_12B_it_fp4_mixed.safetensors"
CONNECTORS = "ltx-2.3-embeddings-connectors.safetensors"

# ---- 生成参数（LTX-2.3 distilled，实测 768x512x97 约 2.5 分钟/镜）----
WIDTH, HEIGHT = 768, 512
LENGTH = 97
FPS = 25
STEPS = 8                 # distilled 模型 8 步即可
CFG = 1.0                 # distilled 必须 cfg=1.0
BASE_SEED = 20260903
NEGATIVE = "pc game, console game, video game, cartoon, childish, ugly, blurry, low quality"

T = LENGTH / FPS          # 单镜时长 3.88s
XFADE = 0.5               # 转场时长

# ---- 分镜：一场连续的戏（雨夜赛博都市，主角穿行、记忆闪现、走向发光门）----
# 主角一致性锚点：每个分镜都带上，尽量让 18 个独立 T2V 镜头里的人物外观稳定
HERO = ("a lone protagonist in a long dark coat, short dark hair, "
        "a faint glowing scar along the left cheek")
# 项目统一视觉风格（取自 config.yaml project.style）
STYLE = "cinematic, film grain, 35mm, dramatic lighting"

# ---- 完整短片分镜：18 镜 / 四幕 / 约 61 秒 ----
SHOTS = [
    # ===== 第一幕 · 开端：建立世界与主角 =====
    # 1 全景 · 赛博都市
    "A vast rain-slicked neon street in a cyberpunk metropolis at night, towering "
    "holographic billboards in cyan and magenta, crowds with augmented-reality overlays, "
    "flying vehicles above, a lone figure in a long dark coat walking in the far distance, "
    f"{STYLE}, slow camera movement",
    # 2 中景 · 主角登场
    f"{HERO}, walking down a narrow neon alley, rain falling through volumetric light, "
    f"holographic advertisements flickering overhead, {STYLE}, slow tracking shot",
    # 3 特写 · 记忆母题
    "Extreme close-up of a gloved hand opening, a cluster of glowing memory fragments "
    "floating above the palm like tiny holographic shards, rain in the background, "
    f"{STYLE}, shallow depth of field",
    # 4 全景 · 城市
    "Wide shot of the cyberpunk skyline at night, flying vehicles streaming between "
    "megatowers, giant holographic advertisements reflecting in rain clouds, cyan and "
    f"magenta neon, {STYLE}, slow camera pan",
    # 5 中景 · 进入记忆诊所
    f"{HERO}, pushing open the door of a small memory clinic, warm amber light spilling "
    f"onto the wet street, a faded neon sign above, {STYLE}, slow camera movement",

    # ===== 第二幕 · 发展：记忆交易与身份危机 =====
    # 6 内景 · 记忆扫描
    "Interior of a dim memory clinic, the protagonist seated in a chrome chair, a ring of "
    "blue scanning light sweeping across the face, cables and old monitors around, "
    f"{STYLE}, slow camera push in",
    # 7 特写 · 身份数据
    "Close-up of a cracked monitor displaying cascading streams of the protagonist's memory "
    "data, green and amber code, a silhouette of a human profile formed of flowing particles, "
    f"{STYLE}",
    # 8 中景 · 交易筹码
    "A masked memory dealer in a dark clinic interior extending a hand holding a small "
    "glowing memory chip, amber backlight, the protagonist's face half-lit in the foreground, "
    f"{STYLE}, shallow depth of field",
    # 9 特写 · 犹豫
    "Close-up of the protagonist's face, eyes wide with hesitation, reflections of flowing "
    "data streams in the wet eyes, cyan and amber light, mysterious and emotional, "
    f"{STYLE}, shallow depth of field",
    # 10 主观 · 看见未来（点题）
    "The protagonist's point of view, a vision of an older version of himself standing in "
    "the rain across the street, translucent and flickering like a hologram, neon "
    f"reflections, {STYLE}, slow camera push forward",
    # 11 闪回 · 失落的记忆
    "A warm memory flashback, a young child laughing in a sunlit field of tall grass, "
    f"golden hour light, soft bokeh, nostalgic and tender, {STYLE}, slow camera movement",
    # 12 特写 · 门（母题）
    "Close-up of an old wooden door glowing faintly at its edges, standing alone in a dark "
    f"void filled with drifting memory fragments, hope and mystery, {STYLE}, slow camera push in",

    # ===== 第三幕 · 高潮：抉择与对抗 =====
    # 13 中景 · 逃离
    f"{HERO}, bursting out of the memory clinic into the rain-soaked neon street, coat "
    f"flaring, running away from camera, reflections scattering in puddles, {STYLE}, "
    "fast tracking shot",
    # 14 全景 · 系统苏醒
    "Wide shot of the city's surveillance system awakening, security drones rising from "
    "megatowers, red scanning lights sweeping through the rain, the tiny figure of the "
    f"protagonist far below, {STYLE}, slow crane shot",
    # 15 特写 · 捏碎芯片（选择保留自我）
    "Extreme close-up of a hand crushing the glowing memory chip, sparks and shards of light "
    f"falling like rain, determined, cyan and amber light, {STYLE}, shallow depth of field",
    # 16 中景 · 对峙
    f"{HERO}, standing in the rain looking up, surrounded by hovering security drones with "
    f"red scanning beams, coat whipping in the wind, defiant, {STYLE}, low angle hero shot",

    # ===== 第四幕 · 结尾：主题收束 =====
    # 17 主观 · 光之门
    "The protagonist's point of view, a distant glowing gate of light appearing through rain "
    "and fog at the end of the street, holographic signs and flying vehicles in the sky, "
    f"{STYLE}, slow camera push forward",
    # 18 全景 · 走向光门，拉远
    "The lone figure in a long dark coat walking toward the glowing gate down the "
    "rain-slicked street, the vast cyberpunk skyline fading into fog behind, camera slowly "
    f"pulling back, hope and mystery, {STYLE}",
]

# ---- WebUI 编辑覆盖：outputs/storyboard.json ----
STORYBOARD_FILE = os.path.join(ROOT, "outputs", "storyboard.json")


def load_storyboard_override() -> None:
    """WebUI 改完分镜会存到 outputs/storyboard.json；存在就用它覆盖内置分镜。

    这样"改分镜 -> 重新生成"不需要动脚本源码。
    """
    global SHOTS
    if not os.path.exists(STORYBOARD_FILE):
        return
    try:
        with open(STORYBOARD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # 文件坏了就回退内置分镜，别把生成搞挂
        print(f"[warn] 读取分镜覆盖失败({e})，使用内置分镜")
        return
    shots = data.get("shots")
    if isinstance(shots, list) and shots and all(isinstance(x, str) for x in shots):
        SHOTS = shots
        print(f"[storyboard] 使用 outputs/storyboard.json 覆盖分镜（{len(SHOTS)} 镜）")


load_storyboard_override()


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FFMPEG = ffmpeg_exe()


# ---------- manifest：记录 镜号 -> 文件（ComfyUI 文件名带全局自增序号，无法预测）----------
def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(m: dict) -> None:
    json.dump(m, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ---------- 分镜内容指纹：分镜变了就让镜头缓存失效 ----------
SHOTS_HASH_FILE = os.path.join(WORK, ".shots_hash")


def _shots_hash() -> str:
    import hashlib
    return hashlib.sha256("\n".join(SHOTS).encode("utf-8")).hexdigest()


def _write_hash(h: str) -> None:
    try:
        with open(SHOTS_HASH_FILE, "w", encoding="utf-8") as f:
            f.write(h)
    except Exception:
        pass


def sync_shot_cache() -> None:
    """分镜内容变更时作废镜头缓存，避免「新解说配旧画面」。

    run_shot 按镜号复用 manifest 里的片段；若 storyboard.json 被改写（WebUI 编辑
    或 C→分镜桥接重新生成），缓存必须整体作废并重新渲染，否则成片画面与新分镜对不上。
    旧片段文件保留在 ComfyUI 输出目录，不删除。

    首次启用指纹时不清缓存：此时无法判断既有片段是否匹配当前分镜，
    贸然作废会白白重渲一整轮（约数十分钟），故只记录指纹。
    """
    h = _shots_hash()
    if not os.path.exists(SHOTS_HASH_FILE):
        _write_hash(h)
        return
    try:
        with open(SHOTS_HASH_FILE, encoding="utf-8") as f:
            old = f.read().strip()
    except Exception:
        old = ""
    if old == h:
        return
    man = load_manifest()
    if man:
        print(f"[cache] 分镜已变更（{len(SHOTS)} 镜），作废 {len(man)} 个旧镜头缓存，将重新渲染")
        try:
            os.remove(MANIFEST)
        except Exception:
            pass
    _write_hash(h)


sync_shot_cache()


def build_workflow(idx: int) -> dict:
    """单镜 LTX-2.3 工作流：文本 -> 视频latent+音频latent -> 采样 -> 分离解码 -> 合成mp4。"""
    return {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": UNET}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "3": {"class_type": "LTXVAudioVAELoader", "inputs": {"ckpt_name": AUDIO_VAE}},
        "4": {"class_type": "LTXAVTextEncoderLoader",
              "inputs": {"text_encoder": TEXT_ENC, "ckpt_name": CONNECTORS,
                         "device": "default"}},
        "5": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["4", 0], "text": SHOTS[idx - 1]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"clip": ["4", 0], "text": NEGATIVE}},
        "7": {"class_type": "EmptyLTXVLatentVideo",
              "inputs": {"width": WIDTH, "height": HEIGHT, "length": LENGTH,
                         "batch_size": 1}},
        "8": {"class_type": "LTXVEmptyLatentAudio",
              "inputs": {"audio_vae": ["3", 0], "frames_number": LENGTH,
                         "frame_rate": FPS, "batch_size": 1}},
        "9": {"class_type": "LTXVConcatAVLatent",
              "inputs": {"video_latent": ["7", 0], "audio_latent": ["8", 0]}},
        "10": {"class_type": "LTXVConditioning",
               "inputs": {"positive": ["5", 0], "negative": ["6", 0],
                          "frame_rate": FPS}},
        "11": {"class_type": "CFGGuider",
               "inputs": {"model": ["1", 0], "positive": ["10", 0],
                          "negative": ["10", 1], "cfg": CFG}},
        "12": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler_cfg_pp"}},
        "13": {"class_type": "LTXVScheduler",
               "inputs": {"steps": STEPS, "max_shift": 2.05, "base_shift": 0.95,
                          "stretch": True, "terminal": 0.1}},
        "14": {"class_type": "RandomNoise", "inputs": {"noise_seed": BASE_SEED + idx}},
        "15": {"class_type": "SamplerCustomAdvanced",
               "inputs": {"noise": ["14", 0], "guider": ["11", 0],
                          "sampler": ["12", 0], "sigmas": ["13", 0],
                          "latent_image": ["9", 0]}},
        "16": {"class_type": "LTXVSeparateAVLatent",
               "inputs": {"av_latent": ["15", 0]}},
        "17": {"class_type": "VAEDecodeTiled",
               "inputs": {"samples": ["16", 0], "vae": ["2", 0],
                          "tile_size": 512, "overlap": 64,
                          "temporal_size": 32, "temporal_overlap": 4}},
        "18": {"class_type": "LTXVAudioVAEDecode",
               "inputs": {"samples": ["16", 1], "audio_vae": ["3", 0]}},
        "19": {"class_type": "CreateVideo",
               "inputs": {"images": ["17", 0], "audio": ["18", 0], "fps": FPS,
                          "bit_depth": "auto", "color_space": "sRGB"}},
        "20": {"class_type": "SaveVideo",
               "inputs": {"video": ["19", 0], "filename_prefix": f"ltx23/shot{idx}",
                          "format": "auto", "codec": "auto"}},
    }


def run_shot(idx: int) -> str | None:
    man = load_manifest()
    key = str(idx)
    if key in man and os.path.exists(man[key]):
        print(f"[shot {idx}] 已存在，跳过 -> {man[key]}")
        return man[key]

    try:
        S.get(f"{URL}/", timeout=8).raise_for_status()
    except Exception as e:
        print(f"[fatal] ComfyUI 不可达 {URL}: {type(e).__name__}  "
              f"（可用 COMFY_URL 环境变量指定，或先启动 ComfyUI）")
        return None

    wf = build_workflow(idx)
    r = S.post(f"{URL}/prompt", json={"prompt": wf, "client_id": f"ltxshot{idx}"},
               timeout=180)
    if r.status_code != 200:
        print(f"[shot {idx}] 提交失败 {r.status_code}: {r.text[:600]}")
        return None
    j = r.json()
    if "prompt_id" not in j:
        print(f"[shot {idx}] 校验失败: {json.dumps(j, ensure_ascii=False)[:1200]}")
        return None
    pid = j["prompt_id"]
    print(f"[shot {idx}/{len(SHOTS)}] T2V submit pid={pid} "
          f"{WIDTH}x{HEIGHT}x{LENGTH}@{FPS}fps steps={STEPS}")

    for i in range(1200):  # 单镜最多 60 分钟
        time.sleep(3)
        h = S.get(f"{URL}/history/{pid}", timeout=30).json()
        if pid not in h:
            if i % 20 == 0:
                q = S.get(f"{URL}/queue", timeout=30).json()
                print(f"  [wait] {i*3}s running={len(q.get('queue_running', []))}")
            continue
        rec = h[pid]
        out = rec.get("outputs", {})
        if "20" in out:
            item = (out["20"].get("images") or out["20"].get("gifs") or [{}])[0]
            fn = item.get("filename")
            sub = item.get("subfolder", "")
            path = os.path.join(OUT_DIR, sub, fn) if sub else os.path.join(OUT_DIR, fn)
            man = load_manifest()
            man[key] = path
            save_manifest(man)
            print(f"[shot {idx}] done -> {path}")
            return path
        st = rec.get("status", {})
        print(f"[shot {idx}] 失败: {json.dumps(st, ensure_ascii=False)[:800]}")
        return None
    print(f"[shot {idx}] timeout")
    return None


def probe_audio(path: str) -> bool:
    """探测该视频是否含音轨（LTX 理论上每镜都有，但保险起见逐个确认）。"""
    r = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True, timeout=60)
    return "Audio:" in ((r.stderr or "") + (r.stdout or ""))


def concat() -> str | None:
    man = load_manifest()
    srcs = [man.get(str(i)) for i in range(1, len(SHOTS) + 1)]
    miss = [i + 1 for i, p in enumerate(srcs) if not p or not os.path.exists(p)]
    if miss:
        print("[fatal] 缺少镜头:", miss, " manifest:", MANIFEST)
        return None

    out = os.path.join(ROOT, "outputs", "ltx23_film.mp4")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    has_audio = all(probe_audio(p) for p in srcs)
    print(f"[concat] {len(srcs)} 镜, 音轨={'有' if has_audio else '无'}")

    parts = []
    for p in srcs:
        parts += ["-i", p]
    n = len(srcs)

    chain = ""
    for i in range(n):
        chain += (f"[{i}:v]fps={FPS},format=yuv420p,setsar=1,"
                  f"scale={WIDTH}:{HEIGHT}[v{i}];")
    chain += f"[v0][v1]xfade=transition=fade:duration={XFADE}:offset={T - XFADE:.4f}[x1];"
    for k in range(2, n):
        off = k * T - k * XFADE
        chain += f"[x{k-1}][v{k}]xfade=transition=fade:duration={XFADE}:offset={off:.4f}[x{k}];"

    if has_audio:
        for i in range(n):  # 归一化采样率/声道，acrossfade 要求两端一致
            chain += (f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
                      f"channel_layouts=stereo[an{i}];")
        chain += f"[an0][an1]acrossfade=d={XFADE}:c1=tri:c2=tri[af1];"
        for k in range(2, n):
            chain += f"[af{k-1}][an{k}]acrossfade=d={XFADE}:c1=tri:c2=tri[af{k}];"
    chain = chain.rstrip(";")

    maps = ["-map", f"[x{n-1}]"]
    if has_audio:
        maps += ["-map", f"[af{n-1}]", "-c:a", "aac", "-b:a", "160k"]

    cmd = [FFMPEG, "-y", *parts, "-filter_complex", chain, *maps,
           "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", str(FPS), out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        total = n * T - (n - 1) * XFADE
        print(f"[OK] 成片 {out}  {os.path.getsize(out)/2**20:.2f}MB  "
              f"{WIDTH}x{HEIGHT} 约{total:.1f}s")
        return out
    print("[err] ffmpeg 失败:", (r.stderr or "")[-1800:])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=int, default=0, help="只生成第 N 镜；0=全部")
    ap.add_argument("--concat", action="store_true", help="只做拼接")
    a = ap.parse_args()

    # 关键：任何失败都必须以非零码退出。
    # 上层 webui.run_script() 依据 returncode 判定成败；若这里失败仍 return 0，
    # 整条链路会"假成功"继续往下跑，最后拿旧成片配音封装，用户完全无感知。
    if a.concat:
        return 0 if concat() else 1
    if a.shot:
        return 0 if run_shot(a.shot) else 1

    for i in range(1, len(SHOTS) + 1):
        p = run_shot(i)
        if not p:
            print(f"[abort] 第 {i} 镜失败")
            return 1
        if i < len(SHOTS):
            time.sleep(2)
    return 0 if concat() else 1


if __name__ == "__main__":
    sys.exit(main())
