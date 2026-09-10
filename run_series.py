#!/usr/bin/env python
"""三集连贯出片：默认每镜 T2V（精确匹配旁白场景），可选 --i2v 做尾帧续写。

v2（2026-09-07）重要修正：
  - 初版每镜都用上一镜尾帧做 I2V 续写以求连贯，但实测 **LTX 的 I2V 起始图权重
    过高，模型几乎忽略新 prompt 的场景变化**，导致 18 镜画面全部趋同为
    「雨中女人肖像」，而旁白讲的是店铺 / 老板 / 读忆椅 / 草地 / 芯片等多个场景
    → 画面与旁白严重脱节；
  - 故默认改为「每镜 T2V + 每集 18 个精确场景 prompt」，逐镜对应旁白描写的画面；
    角色一致性由 prompt 内固定的 Mira 外观 + 全局 style_anchor + 固定 seed 保证；
  - I2V 续写降级为可选开关 --i2v（连贯优先、语义让位）。

与 run_ltx25_multishot.py 的区别：
  - 分镜来自 outputs/series_shots.json（三集共用一份连贯剧本的视觉分镜），
    而非散落的 SHOTS 常量；
  - 统一追加 series_bible.json 的 style_anchor，保证视觉基调一致。

用法:
  python run_series.py            # 依次重出三集（默认 T2V 精确场景，耗时较长）
  python run_series.py --ep 1     # 只重出第 1 集
  python run_series.py --concat 1 # 只做第 1 集拼接（镜头已生成完）
  python run_series.py --i2v      # 改用 I2V 尾帧续写（连贯优先，画面易与旁白脱节）
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
BASE_WORK = os.path.join(ROOT, "outputs", "series_shots")
WORK = BASE_WORK
os.makedirs(WORK, exist_ok=True)
MANIFEST = os.path.join(WORK, "series_manifest.json")
# 非 ltx 引擎的成片后缀，避免覆盖同名的 LTX 成片
FILM_SUFFIX = ""


def set_workspace(engine: str) -> None:
    """按引擎隔离镜头目录与成片名。

    manifest 以「镜头 key -> 路径」缓存已生成的镜头。若不同引擎共用同一份
    manifest，换引擎后会命中上一引擎的缓存而 **静默复用旧画面**（出片与所选
    引擎不符，例如选了 mmh3 却复用 LTX 镜头）；成片若同名还会直接覆盖已有
    LTX 成片。故非 ltx 引擎使用独立镜头目录 + 成片后缀。
    """
    global WORK, MANIFEST, FILM_SUFFIX
    WORK = BASE_WORK if engine == "ltx" else f"{BASE_WORK}_{engine}"
    MANIFEST = os.path.join(WORK, "series_manifest.json")
    FILM_SUFFIX = "" if engine == "ltx" else f"_{engine}"
    os.makedirs(WORK, exist_ok=True)

SHOTS_FILE = os.path.join(ROOT, "outputs", "series_shots.json")
BIBLE_FILE = os.path.join(ROOT, "outputs", "series_bible.json")

BASE_SEED = 20260907
XFADE = 0.5
# 每个 base prompt 展开的运镜变体（决定每集镜数 = base 数 × 变体数）
CAM_VARIANTS = [", slow camera push in", ", gentle lateral pan"]


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


FFMPEG = ffmpeg_exe()


# ---------- manifest ----------
def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(m: dict) -> None:
    json.dump(m, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_engine(width=None, height=None, frames=None, fps=None, name="ltx"):
    """按 name 选择视频引擎：ltx=LTX-2.5，mmh3=MiniMax H3(Turbo 4 步, 原生立体声)。

    两者接口一致（resolution / num_frames / fps / generate），故上层出片逻辑无需改动。
    """
    cfg = load_config()
    if name == "mmh3":
        from agent.mmh3_engine import MMH3Engine
        eng = MMH3Engine(cfg, agent_root=ROOT)
    else:
        from agent.ltx_engine import LTXEngine
        eng = LTXEngine(cfg, agent_root=ROOT)
    if width and height:
        eng.resolution = f"{width}x{height}"
    if fps:
        eng.fps = int(fps)
    if frames:
        eng.num_frames = int(frames)
    return eng


def film_duration(path: str) -> float:
    """探测视频时长（秒）。imageio_ffmpeg 无 ffprobe，故解析 ffmpeg -i 的 Duration。"""
    import re
    try:
        r = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True, timeout=60)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      (r.stderr or "") + (r.stdout or ""))
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def _ok(p: str) -> bool:
    return os.path.exists(p) and os.path.getsize(p) > 0


def last_frame(video: str, out_png: str) -> str | None:
    """抽取视频最后一帧（供下一镜 I2V 续写）。

    坑：-sseof -0.04 回退过短，ffmpeg 会报 "Output file is empty, nothing was
    encoded" 而抽不到帧（此时下一镜会静默退化成 T2V，续写链断掉）。
    故依次回退 1s / 0.5s / 0.2s 重试，最后用「探时长 + -ss」兜底。
    """
    if _ok(out_png):
        return out_png
    for off in ("-1", "-0.5", "-0.2"):
        subprocess.run([FFMPEG, "-y", "-sseof", off, "-i", video,
                        "-frames:v", "1", "-q:v", "2", out_png],
                       capture_output=True, text=True, timeout=180)
        if _ok(out_png):
            return out_png
    d = film_duration(video)
    if d > 0:
        subprocess.run([FFMPEG, "-y", "-ss", str(max(d - 0.08, 0)), "-i", video,
                        "-frames:v", "1", "-q:v", "2", out_png],
                       capture_output=True, text=True, timeout=180)
        if _ok(out_png):
            return out_png
    return None


def probe_audio(path: str) -> bool:
    r = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True, timeout=60)
    return "Audio:" in ((r.stderr or "") + (r.stdout or ""))


def concat_shots(eng, srcs: list, out: str) -> bool:
    """xfade 视频 + acrossfade 音频拼接（LTX-2.5 每镜自带音轨）。"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    T = eng.num_frames / eng.fps
    has_audio = all(probe_audio(p) for p in srcs)
    print(f"[concat] {len(srcs)} 镜, 单镜 {T:.3f}s, 音轨={'有' if has_audio else '无'}")

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
        print(f"[OK] 成片 {out}  {os.path.getsize(out) / 2 ** 20:.2f}MB  约 {total:.1f}s")
        return True
    print("[err] ffmpeg 拼接失败:", (r.stderr or "")[-1800:])
    return False


def _is_char_shot(prompt: str) -> bool:
    """该镜是否出现主角 Mira。

    分镜里主角统一用 'the woman' 指代，故以此判定。**只给含她的镜做锚定**——
    否则会把 Mira 硬塞进「店铺外景 / 药瓶特写 / 老板 / 草地」这些本没有她的镜头，
    重演 LTX 上「I2V 起始图权重过高 → 各镜画面趋同、与旁白脱节」的坑。
    """
    p = prompt.lower()
    if "woman" not in p:
        return False
    # 手部特写要排除：拿脸部锚定图去起手，会把脸硬塞进只该有手的画面
    # （ep1 的 shot8/15/18 都是 "close-up of the woman's hand ..."）。
    if "hand" in p and "close-up" in p:
        return False
    return True


def resolve_anchor(value: str, anchor_name: str = "mira") -> tuple:
    """解析 --anchor：'auto' 自动找角色卡；空=不锚定；否则当作路径。

    查找顺序：config.series.character_anchor → outputs/anchor/*anchor*.png
              → outputs/anchor/<anchor_name>_*.png
    返回 (路径, 说明)；找不到时路径为空、说明里写原因，由调用方决定怎么处理。
    """
    if not value:
        return "", ""
    if value.lower() != "auto":
        p = value if os.path.isabs(value) else os.path.join(ROOT, value)
        return (p, "指定路径") if os.path.exists(p) else ("", f"锚定图不存在: {p}")

    try:
        cfg_anchor = ((load_config().get("series") or {}).get("character_anchor") or "").strip()
    except Exception:                 # noqa: BLE001 - 读不到配置就跳过这级
        cfg_anchor = ""
    if cfg_anchor:
        p = cfg_anchor if os.path.isabs(cfg_anchor) else os.path.join(ROOT, cfg_anchor)
        if os.path.exists(p):
            return p, "config.series.character_anchor"

    anc_dir = os.path.join(ROOT, "outputs", "anchor")
    if os.path.isdir(anc_dir):
        files = sorted(f for f in os.listdir(anc_dir) if f.lower().endswith(".png"))
        hit = next((f for f in files if "anchor" in f.lower()), None)
        if hit:
            return os.path.join(anc_dir, hit), f"outputs/anchor/{hit}"
        hit2 = next((f for f in files
                     if f.lower().startswith(anchor_name.lower() + "_")), None)
        if hit2:
            return os.path.join(anc_dir, hit2), f"outputs/anchor/{hit2}"
    return "", "auto 但未在 outputs/anchor/ 找到角色卡（可先跑 gen_anchor_assets.py）"


def _write_qa_report(entries: list) -> None:
    """把逐镜质检结果落盘（含分布汇总，便于回头校准阈值）。"""
    if not entries:
        return
    path = os.path.join(WORK, "qa_report.json")
    summary = {}
    try:
        from agent import qa as qa_mod
        summary = qa_mod.summarize(entries)
    except Exception:                 # noqa: BLE001 - 报告只是附属产物，失败不影响出片
        pass
    failed = [e for e in entries if not e.get("ok")]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "total": len(entries), "failed": len(failed),
                       "summary": summary, "shots": entries},
                      f, ensure_ascii=False, indent=2)
        print(f"[qa] 报告 {path}（{len(entries)} 次生成，{len(failed)} 次未达标）")
    except Exception as e:            # noqa: BLE001
        print(f"[qa] 写报告失败: {e}")


def run_episode(eng, ep: int, prompts: list, style_anchor: str,
                prev_frame: str | None, out_dir: str, use_i2v: bool,
                anchor: str = "", anchor_mode: str = "first",
                only: set | None = None, force: bool = False,
                anchor_map: dict | None = None,
                ref_images: list | None = None,
                qa_policy: dict | None = None) -> str | None:
    """出一集。

    默认（use_i2v=False）每镜 **T2V**：prompt 语义主导，画面精确匹配该段旁白描写的场景。
    仅当 --i2v 时才用上一镜尾帧续写——注意 LTX 的 I2V 起始图权重过高，会忽略新
    prompt 的场景变化，导致 18 镜画面趋同、与旁白脱节（2026-09-07 实测教训）。

    --anchor：给「含 Mira 的镜」喂同一张标准像作 I2V 起始帧，用来锁住身份。
      first（默认）每个 Mira 镜都从标准像起 → 一致性最强；
      chain  首镜用标准像、后续接上一个 Mira 镜的尾帧 → 兼顾连贯与自然演变。
    """
    from agent import qa as qa_mod
    qa_policy = qa_policy or {}
    qa_entries: list = []
    man = load_manifest()
    shot_files = []
    mira_prev = None          # chain 模式：上一个 Mira 镜的尾帧

    for i, base in enumerate(prompts):
        idx = i + 1
        key = f"ep{ep}_shot{idx}"
        if only and idx not in only:
            continue
        shot = None if (force or only) else man.get(key)
        if not (shot and os.path.exists(shot)):
            prompt = f"{base}, {style_anchor}" if style_anchor else base
            out_path = os.path.join(WORK, f"{key}.mp4")
            # 起始图优先级：本镜锚定图（--anchor-map，逐镜精确）> 角色锚定（锁脸）
            #              > 上一镜尾帧续写 > 纯 T2V
            img, tag = None, "T2V"
            amap = anchor_map or {}
            p = amap.get(str(idx)) or amap.get(idx)
            if p:
                pth = p if os.path.isabs(p) else os.path.join(ROOT, p)
                if os.path.exists(pth):
                    img, tag = pth, "I2V/锚定图"
                else:
                    print(f"[{key}] 锚定图不存在，回退: {pth}")
            if img is None and anchor and _is_char_shot(base):
                img = anchor if anchor_mode == "first" else (mira_prev or anchor)
                tag = "I2V/角色锚定"
            elif img is None and use_i2v and prev_frame:
                img, tag = prev_frame, "I2V"
            # 质检 + 自动重 roll：不达标就换 seed 重出（限次），避免人工盯 54 镜。
            # 判定只在 config.qa 阈值明确越界时触发（默认很保守，见 agent/qa.py）。
            qa_on = bool((qa_policy or {}).get("enabled", True))
            rolls = 1 + (max(0, int(qa_policy.get("max_rerolls") or 0)) if qa_on else 0)
            is_char = _is_char_shot(base)
            base_seed = BASE_SEED + ep * 1000 + idx
            shot = None
            for attempt in range(rolls):
                seed = base_seed + attempt * 7919      # 确定性换 seed，便于复现失败样本
                print(f"[{key}] {tag} generate "
                      f"{eng.resolution} {eng.num_frames}帧@{eng.fps}fps "
                      f"seed={seed}"
                      + (f"（重 roll {attempt}/{rolls - 1}）" if attempt else ""))
                try:
                    produced = eng.generate(prompt, out_path, seed=seed,
                                            image=img,
                                            ref_images=ref_images or None)
                except Exception as e:
                    print(f"[{key}] 生成失败: {e}")
                    return None
                if not produced or not os.path.exists(produced):
                    print(f"[{key}] 未产出文件")
                    return None
                if not qa_on:
                    shot = produced
                    break
                sc = qa_mod.score_video(produced, qa_policy,
                                        is_char_shot=is_char, anchor=anchor)
                ok, reasons = qa_mod.evaluate(sc, qa_policy, is_char_shot=is_char)
                entry = dict(sc)
                entry.update({"key": key, "attempt": attempt, "seed": seed,
                              "ok": ok, "reasons": reasons})
                qa_entries.append(entry)
                if ok:
                    shot = produced
                    break
                if attempt + 1 < rolls:
                    print(f"[{key}] 质检未过：{'；'.join(reasons)} → 换 seed 重 roll")
                else:
                    print(f"[{key}] 质检仍未过（{'；'.join(reasons)}），采用本次结果继续")
                    shot = produced
            if not shot:
                print(f"[{key}] 无可用产出")
                return None
            man = load_manifest()
            man[key] = shot
            save_manifest(man)
        else:
            print(f"[{key}] 已存在，跳过 -> {shot}")
        shot_files.append(shot)
        if use_i2v:
            prev_frame = last_frame(shot, os.path.join(WORK, f"{key}_last.png"))
        if anchor and _is_char_shot(base):
            mira_prev = last_frame(shot, os.path.join(WORK, f"{key}_last.png"))

    _write_qa_report(qa_entries)

    if only:
        print(f"[only] 已重出镜号 {sorted(only)}，跳过拼接")
        return None
    film = os.path.join(out_dir, f"ep{ep}_series_film{FILM_SUFFIX}.mp4")
    if not concat_shots(eng, shot_files, film):
        return None
    if use_i2v:
        return last_frame(film, os.path.join(WORK, f"ep{ep}_film_last.png"))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, default=0, help="只重出第 N 集；0=全部三集")
    ap.add_argument("--concat", type=int, default=0, help="只做第 N 集拼接（镜头已生成完）")
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    ap.add_argument("--frames", type=int, default=0, help="覆盖单镜帧数")
    ap.add_argument("--fps", type=int, default=0)
    ap.add_argument("--i2v", action="store_true",
                    help="启用镜间 I2V 尾帧续写（连贯优先）。警告：LTX 的 I2V 起始图权重过高，"
                         "会忽略新 prompt 的场景变化，导致各镜画面趋同、与旁白脱节。"
                         "默认关闭=每镜 T2V，画面精确匹配旁白场景")
    ap.add_argument("--engine", default="mmh3", choices=["ltx", "mmh3"],
                    help="视频引擎：ltx=LTX-2.5，mmh3=MiniMax H3(Turbo 4 步, 自带立体声)")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "outputs"))
    ap.add_argument("--anchor", default="",
                    help="角色锚定图：给含 'woman' 的镜头作 I2V 起始帧（锁住同一张脸）。"
                         "传 auto 自动查找：config.series.character_anchor"
                         " → outputs/anchor/*anchor*.png → mira_*.png；留空=不锚定")
    ap.add_argument("--anchor-map", default="",
                    help="JSON 文件：{镜号: 锚定图路径}。逐镜指定锚定图（场景图 / 人物三视图），"
                         "优先级高于 --anchor")
    ap.add_argument("--anchor-mode", default="first", choices=["first", "chain"],
                    help="first=每个 Mira 镜都从锚定图起（一致性最强，默认）；"
                         "chain=首镜用锚定图、后续接上一 Mira 镜尾帧（更连贯）")
    ap.add_argument("--only", default="",
                    help="只重出指定镜号（逗号分隔，如 14,15,16），强制重生成并跳过拼接")
    ap.add_argument("--force", action="store_true",
                    help="强制重生成所有镜头（用于跑锚定图升级等场景，仍会拼接）")
    ap.add_argument("--ref-images", default="auto",
                    help="身份参考图：'auto' 自动收集 outputs/anchor/mira_*.png（最多9张）；"
                         "或逗号分隔的显式路径；'none' 关闭。配合首帧锚定走 Hybrid 增强人物一致性（不需白模）")
    ap.add_argument("--qa", dest="qa", action="store_true", default=None,
                    help="强制开启逐镜质检（默认取 config.qa.enabled）")
    ap.add_argument("--no-qa", dest="qa", action="store_false",
                    help="关闭逐镜质检（不看质检、不重 roll）")
    ap.add_argument("--qa-rerolls", type=int, default=None,
                    help="质检不达标时自动换 seed 重出的次数上限（覆盖 config.qa.max_rerolls）")
    a = ap.parse_args()
    only = None
    if a.only.strip():
        only = {int(x) for x in a.only.replace("，", ",").split(",") if x.strip().isdigit()}

    # 必须在任何 WORK/MANIFEST 相关操作之前切换工作区，否则会命中其它引擎的镜头缓存
    set_workspace(a.engine)
    eng = build_engine(a.width or None, a.height or None,
                       a.frames or None, a.fps or None, a.engine)

    data = load_json(SHOTS_FILE)
    # 角色卡：--anchor auto 自动查 config / outputs/anchor/，省得每次手填路径
    anchor, anchor_note = resolve_anchor(a.anchor)
    if a.anchor and not anchor:
        print(f"[anchor] {anchor_note}，改用无锚定出片")
    elif anchor:
        print(f"[anchor] 角色锚定图: {anchor}（{anchor_note}）")
    else:
        anc_dir = os.path.join(ROOT, "outputs", "anchor")
        n = len([f for f in os.listdir(anc_dir)
                 if f.lower().endswith(".png")]) if os.path.isdir(anc_dir) else 0
        if n:
            print(f"[anchor] 检测到 outputs/anchor/ 有 {n} 张角色卡；需要锁脸可加 "
                  f"--anchor auto（注意：LTX 的 I2V 起始图权重偏高，可能让各镜画面趋同）")

    anchor_map = {}
    if a.anchor_map:
        _p = a.anchor_map if os.path.isabs(a.anchor_map) else os.path.join(ROOT, a.anchor_map)
        anchor_map = load_json(_p)
        print(f"[anchor-map] 载入 {len(anchor_map)} 条逐镜锚定")
    try:
        style_anchor = load_json(BIBLE_FILE).get("style_anchor", "")
    except Exception:
        style_anchor = ""
    # 身份参考图（增强人物一致性，配合首帧锚定走 Hybrid，不需白模）
    ref_images = []
    if a.ref_images and a.ref_images.lower() != "none":
        if a.ref_images.lower() == "auto":
            anc_dir = os.path.join(ROOT, "outputs", "anchor")
            if os.path.isdir(anc_dir):
                ref_images = sorted([
                    os.path.join(anc_dir, f) for f in os.listdir(anc_dir)
                    if f.lower().startswith("mira_") and f.lower().endswith(".png")
                ])[:9]
        else:
            ref_images = [p.strip() for p in a.ref_images.split(",") if p.strip()]
    print(f"[cfg] {eng.resolution} {eng.num_frames}帧@{eng.fps}fps  style_anchor={'有' if style_anchor else '无'}  ref_images={len(ref_images)}")

    # 逐镜质检策略（config.qa，可被 --qa / --no-qa / --qa-rerolls 覆盖）
    from agent import qa as qa_mod
    qa_policy = qa_mod.load_policy(load_config())
    if a.qa is not None:
        qa_policy["enabled"] = a.qa
    if a.qa_rerolls is not None:
        qa_policy["max_rerolls"] = max(0, a.qa_rerolls)
    if qa_policy.get("enabled"):
        print(f"[qa] 逐镜质检开启 max_rerolls={qa_policy.get('max_rerolls')} "
              f"min_sharpness={qa_policy.get('min_sharpness')} "
              f"min_motion={qa_policy.get('min_motion')} "
              f"face_check={bool(qa_policy.get('face_check'))}")
    else:
        print("[qa] 逐镜质检已关闭")

    if a.concat:
        ep = a.concat
        man = load_manifest()
        shots = [man[f"ep{ep}_shot{i}"] for i in range(1, len(data[f"ep{ep}"]) + 1)
                 if f"ep{ep}_shot{i}" in man and os.path.exists(man[f"ep{ep}_shot{i}"])]
        film = os.path.join(a.out_dir, f"ep{ep}_series_film{FILM_SUFFIX}.mp4")
        return 0 if concat_shots(eng, shots, film) else 1

    episodes = [a.ep] if a.ep else [1, 2, 3]
    prev_frame = None          # 仅 --i2v 模式下用于续写链
    for ep in episodes:
        # 仅 I2V 模式才需要接上一集尾帧；T2V 模式每镜独立，靠角色/风格锚定保持一致。
        if a.i2v and prev_frame is None and ep > 1:
            prev_film = os.path.join(a.out_dir, f"ep{ep - 1}_series_film{FILM_SUFFIX}.mp4")
            if os.path.exists(prev_film):
                prev_frame = last_frame(
                    prev_film, os.path.join(WORK, f"ep{ep - 1}_film_last.png"))
                print(f"[chain] 接上一集 ep{ep - 1} 尾帧: {'成功' if prev_frame else '失败'}")
        prompts = data.get(f"ep{ep}")
        if not prompts:
            print(f"[fatal] series_shots.json 缺少 ep{ep}")
            return 1
        if anchor:
            mode = f"角色锚定({a.anchor_mode})"
        else:
            mode = "I2V 续写" if a.i2v else "T2V 精确场景"
        print(f"\n===== 第 {ep} 集（{len(prompts)} 镜, {mode}）=====")
        film = os.path.join(a.out_dir, f"ep{ep}_series_film{FILM_SUFFIX}.mp4")
        prev_frame = run_episode(eng, ep, prompts, style_anchor,
                                 prev_frame, a.out_dir, a.i2v,
                                 anchor=anchor, anchor_mode=a.anchor_mode,
                                 only=only, force=a.force, anchor_map=anchor_map,
                                 ref_images=ref_images, qa_policy=qa_policy)
        # T2V 模式下 run_episode 成功也返回 None，故用成片是否落盘判定成败
        if not (os.path.exists(film) and os.path.getsize(film) > 0):
            print(f"[abort] 第 {ep} 集失败")
            return 1
        if ep != episodes[-1]:
            time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
