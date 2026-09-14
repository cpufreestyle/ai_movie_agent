#!/usr/bin/env python
"""第5集《雨城归途》真·白模管线 driver。

逐镜：BlockingGenerator 按分镜走位渲染白模 depth 控制视频(Blender MCP)
      -> MMH3Engine.generate(control_video=fc, fc_strength=1.2) 经 H3 Fun Control 锁走位出片
      -> ffmpeg xfade 拼接成 ep5 成片。

用法:
  python gen_ep5_fc.py --only 0         # 只跑第 1 镜(0基)做端到端验证
  python gen_ep5_fc.py                  # 跑全部 6 镜并拼接
  python gen_ep5_fc.py --strength 1.2   # 自定义 Fun Control 强度(0.8干净/1.2强/>=1.5崩坏)
"""
from __future__ import annotations
import sys, os, argparse, subprocess, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
RUN = time.strftime("%Y%m%d_%H%M%S")

import yaml
from agent.blocking import BlockingGenerator
from agent.mmh3_engine import MMH3Engine

cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
cfg.setdefault("blender", {})["use_as_fun_control"] = True

# (desc_cn: 触发走位解析, en: H3 视频提示词)
# 走位关键词必须含 左/右(方向) 或 走近镜头/远离镜头/来回/绕圈(语义)，parse_spec 才提取。
# ★ 质量关键：6 镜用【同一段角色 token + 同一段风格 token】，避免人物跨镜漂移/幽灵化。
#   角色 token 对齐已验证好看的 f01(黏土 3D 女性、深色短发、深色风衣)。
CHAR = ("a small 3D clay-render female character named Mira, Pixar-style clay texture, "
        "solid opaque matte clay body, short dark bob haircut, big soft expressive eyes, "
        "wearing a dark navy trench coat")
STYLE = ("cinematic sci-fi short film, rain-soaked neon city at night, wet reflective ground, "
         "soft bokeh neon signs, volumetric light, moody teal and magenta lighting, "
         "shallow depth of field, film grain, 3D clay-render, solid opaque forms, "
         "clean defined silhouette, no transparency, no ghosting, no double exposure")
SHOTS = [
    ("Mira 从画面左侧走到右侧，走入霓虹长街，雨中行走",
     f"{CHAR} walks from the left side of frame to the right along a rain-soaked neon street at night, medium shot, {STYLE}"),
    ("Mira 走近镜头，来到一家发光的记忆店铺前停下张望",
     f"{CHAR} walks toward the camera and stops in front of a small glowing memory shop in the rain, looks up at the warm window, medium shot, {STYLE}"),
    ("店内，Mira 绕着读忆椅走了一圈，环视四周",
     f"{CHAR} circles around a blue-lit memory reading chair inside a dim memory shop, looking slowly around, medium shot, {STYLE}"),
    ("Mira 把空白芯片放在柜台，向后退远离镜头离开店铺",
     f"{CHAR} places a small blank glowing chip on the shop counter, then steps backward away from the camera and leaves the shop, medium shot, {STYLE}"),
    ("Mira 沿雨街从画面右侧走向左侧，自由而笃定地远去",
     f"{CHAR} walks along the rainy neon street from the right side of frame toward the left, free and confident, wide shot with rain streaks, {STYLE}"),
    ("雨中，Mira 来回踱步，回头望向身后那扇亮起又熄灭的门",
     f"{CHAR} paces back and forth in the rain and turns to look back at a door behind her that glows then fades, medium shot, {STYLE}"),
]

XFADE = 0.5
FPS = 24
NUM_FRAMES = 56  # 17*3+5


def concat(shots: list, out: str) -> str:
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    T = NUM_FRAMES / FPS
    parts = []
    for p in shots:
        parts += ["-i", p]
    n = len(shots)
    chain = ""
    for i in range(n):
        chain += f"[{i}:v]fps={FPS},format=yuv420p,setsar=1[vi{i}];"
    chain += f"[vi0][vi1]xfade=transition=fade:duration={XFADE}:offset={T-XFADE:.4f}[x1];"
    for k in range(2, n):
        off = k * T - k * XFADE
        chain += f"[x{k-1}][vi{k}]xfade=transition=fade:duration={XFADE}:offset={off:.4f}[x{k}];"
    chain = chain.rstrip(";")
    cmd = [ff, "-y", *parts, "-filter_complex", chain, "-map", f"[x{n-1}]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-movflags", "+faststart", out]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=-1, help="只跑第 N 镜(0基)；-1=全部")
    ap.add_argument("--strength", type=float, default=0.85,
                    help="Fun Control 强度：0.85 干净(默认) / 1.2 强 / >=1.5 崩坏")
    ap.add_argument("--concat-only", action="store_true",
                    help="只重拼已有的 shot_*.mp4，不重新渲染（单镜补渲后用）")
    a = ap.parse_args()

    if a.concat_only:
        shots = [os.path.join(ROOT, "outputs/ep5", f"shot_{i+1:03d}.mp4")
                 for i in range(len(SHOTS))]
        shots = [s for s in shots if os.path.exists(s)]
        if len(shots) >= 2:
            film = os.path.join(ROOT, "outputs/ep5", "ep5_film.mp4")
            concat(shots, film)
            print(f"[ep5] 仅重拼成片 -> {film}  ({len(shots)} 镜)", flush=True)
        else:
            print("[ep5] 不足 2 镜，跳过拼接", flush=True)
        return

    blocking = BlockingGenerator(cfg, os.path.join(ROOT, "outputs/ep5/blocking"))
    eng = MMH3Engine(cfg, agent_root=ROOT)
    print(f"[ep5] Blender MCP ready={blocking.is_ready()}  fc_frames={blocking.fc_frames} "
          f"{blocking.fc_w}x{blocking.fc_h}", flush=True)

    idxs = [a.only] if a.only >= 0 else list(range(len(SHOTS)))
    out_paths: dict = {}
    for i in idxs:
        desc, en = SHOTS[i]
        print(f"\n[ep5] ===== 第 {i+1} 镜: {desc} =====", flush=True)
        spec = blocking.parse_spec(desc)
        print(f"[ep5]   walk={spec.get('walk')!r}  shot={spec.get('shot')}  cam={spec.get('camera')}",
              flush=True)
        fc_dir = os.path.join(ROOT, "outputs/ep5/blocking", RUN, f"shot_{i:03d}_fc")
        blocking.render_fc_anim(spec, fc_dir, blocking.fc_frames)
        fc = blocking.export_fc_video(fc_dir)
        if not fc:
            print(f"[ep5] 第 {i+1} 镜 白模控制视频生成失败，跳过", flush=True)
            continue
        out = os.path.join(ROOT, "outputs/ep5", f"shot_{i+1:03d}.mp4")
        seed = 20260907 + 5000 + i
        eng.generate(en, out, seed=seed, control_video=fc, fc_strength=a.strength)
        out_paths[i] = out
        print(f"[ep5] 第 {i+1} 镜 完成 -> {out}", flush=True)

    shots = [out_paths[i] for i in sorted(out_paths) if i in out_paths]
    if len(shots) >= 2:
        film = os.path.join(ROOT, "outputs/ep5", "ep5_film.mp4")
        concat(shots, film)
        print(f"[ep5] 成片 -> {film}  ({len(shots)} 镜)", flush=True)
    else:
        print("[ep5] 不足 2 镜，跳过拼接", flush=True)


if __name__ == "__main__":
    main()
