#!/usr/bin/env python
"""LTX-2.5 多镜头成片：复用 agent/ltx_engine.py 的 LTXEngine 按分镜渲染每镜并拼接。

特点：
  - 直接复用 LTXEngine（走 workflows/ltx2_5_t2v_api.json + 注入逻辑），不手写 workflow；
  - LTX-2.5 原生音视频联合生成，每镜自带音轨，拼接时同时处理音频（xfade + acrossfade）；
  - 支持 --width/--height/--frames/--fps 覆盖 config.comfyui_ltx，便于做分辨率/时长稳定性验证；
  - 分镜默认取自 shots.py（18 镜剧本），并同样支持 outputs/storyboard.json 覆盖。

用法:
  python run_ltx25_multishot.py                  # 依次生成全部镜头并拼接
  python run_ltx25_multishot.py --shot 3         # 只生成第 3 镜（断点续跑）
  python run_ltx25_multishot.py --concat         # 只做拼接（镜头已生成完）
  python run_ltx25_multishot.py --width 768 --height 432 --frames 49   # 更高分辨率+更长单镜
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "outputs", "shots")
os.makedirs(WORK, exist_ok=True)
MANIFEST = os.path.join(WORK, "ltx25_manifest.json")

# 多镜剧本（《看见未来之前》科幻短片，18 镜）：LTX-2.3 运行器归档后抽成独立模块
from shots import SHOTS  # noqa: E402

# ---- 生成参数：默认沿用 config.comfyui_ltx，可被 CLI 覆盖 ----
BASE_SEED = 20260905
XFADE = 0.5               # 转场时长
DEFAULT_OUT = os.path.join(ROOT, "outputs", "ltx25_film.mp4")


# ---- WebUI 编辑覆盖：outputs/storyboard.json ----
STORYBOARD_FILE = os.path.join(ROOT, "outputs", "storyboard.json")


def load_storyboard_override() -> None:
    """WebUI 改完分镜会存到 outputs/storyboard.json；存在就用它覆盖内置分镜。"""
    global SHOTS
    if not os.path.exists(STORYBOARD_FILE):
        return
    try:
        with open(STORYBOARD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # 文件坏了就回退内置分镜
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


# ---------- manifest：记录 镜号 -> 文件 ----------
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
SHOTS_HASH_FILE = os.path.join(WORK, ".ltx25_shots_hash")


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
    h = _shots_hash()
    if not os.path.exists(SHOTS_HASH_FILE):
        _write_hash(h)
        return
    try:
        old = open(SHOTS_HASH_FILE, encoding="utf-8").read().strip()
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


def build_engine(width=None, height=None, frames=None, fps=None):
    """构造 LTXEngine，并按 CLI 覆盖分辨率/帧数/帧率。"""
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    from agent.ltx_engine import LTXEngine
    eng = LTXEngine(cfg, agent_root=ROOT)
    if width and height:
        eng.resolution = f"{width}x{height}"
    if fps:
        eng.fps = int(fps)
    if frames:
        eng.num_frames = int(frames)
    return eng


def run_shot(eng, idx: int) -> str | None:
    man = load_manifest()
    key = str(idx)
    if key in man and os.path.exists(man[key]):
        print(f"[shot {idx}] 已存在，跳过 -> {man[key]}")
        return man[key]

    out_path = os.path.join(WORK, f"ltx25_shot{idx}.mp4")
    prompt = SHOTS[idx - 1]
    print(f"[shot {idx}/{len(SHOTS)}] T2V generate "
          f"{eng.resolution} {eng.num_frames}帧@{eng.fps}fps seed={BASE_SEED + idx}")
    try:
        produced = eng.generate(prompt, out_path, seed=BASE_SEED + idx)
    except Exception as e:
        print(f"[shot {idx}] 生成失败: {e}")
        return None
    if not produced or not os.path.exists(produced):
        print(f"[shot {idx}] 未产出文件")
        return None
    man = load_manifest()
    man[key] = produced
    save_manifest(man)
    print(f"[shot {idx}] done -> {produced}")
    return produced


def probe_audio(path: str) -> bool:
    r = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True, timeout=60)
    return "Audio:" in ((r.stderr or "") + (r.stdout or ""))


def concat(eng, out: str) -> str | None:
    man = load_manifest()
    srcs = [man.get(str(i)) for i in range(1, len(SHOTS) + 1)]
    miss = [i + 1 for i, p in enumerate(srcs) if not p or not os.path.exists(p)]
    if miss:
        print("[fatal] 缺少镜头:", miss, " manifest:", MANIFEST)
        return None

    os.makedirs(os.path.dirname(out), exist_ok=True)
    # 单镜实际时长 = 帧数 / 帧率
    T = eng.num_frames / eng.fps
    has_audio = all(probe_audio(p) for p in srcs)
    print(f"[concat] {len(srcs)} 镜, 单镜{T:.3f}s, 音轨={'有' if has_audio else '无'}")

    parts = []
    for p in srcs:
        parts += ["-i", p]
    n = len(srcs)

    chain = ""
    for i in range(n):
        chain += f"[{i}:v]fps={eng.fps},format=yuv420p,setsar=1[vi{i}];"
    chain += f"[vi0][vi1]xfade=transition=fade:duration={XFADE}:offset={T - XFADE:.4f}[x1];"
    for k in range(2, n):
        off = k * T - k * XFADE
        chain += f"[x{k-1}][vi{k}]xfade=transition=fade:duration={XFADE}:offset={off:.4f}[x{k}];"

    if has_audio:
        for i in range(n):
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
           "-pix_fmt", "yuv420p", "-r", str(eng.fps), out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if os.path.exists(out) and os.path.getsize(out) > 0:
        total = n * T - (n - 1) * XFADE
        print(f"[OK] 成片 {out}  {os.path.getsize(out)/2**20:.2f}MB  "
              f"{eng.resolution} 约{total:.1f}s")
        return out
    print("[err] ffmpeg 失败:", (r.stderr or "")[-1800:])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=int, default=0, help="只生成第 N 镜；0=全部")
    ap.add_argument("--concat", action="store_true", help="只做拼接")
    ap.add_argument("--width", type=int, default=0, help="覆盖分辨率宽（需与 --height 同给）")
    ap.add_argument("--height", type=int, default=0, help="覆盖分辨率高")
    ap.add_argument("--frames", type=int, default=0, help="覆盖单镜帧数")
    ap.add_argument("--fps", type=int, default=0, help="覆盖帧率")
    ap.add_argument("--out", default=DEFAULT_OUT, help="成片输出路径")
    a = ap.parse_args()

    eng = build_engine(a.width or None, a.height or None,
                       a.frames or None, a.fps or None)

    # 失败必须非零退出：上层 webui.run_script() 按 returncode 判定成败，
    # 若这里失败仍 return 0，整条链路会"假成功"拿旧成片继续封装。
    if a.concat:
        return 0 if concat(eng, a.out) else 1
    if a.shot:
        return 0 if run_shot(eng, a.shot) else 1

    for i in range(1, len(SHOTS) + 1):
        p = run_shot(eng, i)
        if not p:
            print(f"[abort] 第 {i} 镜失败")
            return 1
        if i < len(SHOTS):
            time.sleep(2)
    return 0 if concat(eng, a.out) else 1


if __name__ == "__main__":
    sys.exit(main())
