#!/usr/bin/env python
"""为《看见未来之前》加英语配音 + 中英双语字幕。

基于 make_narration.py 的骨架，差异：
  1. 配音 = 英文神经语音（en-US-AndrewMultilingualNeural，noir 质感男声），
     文本 = 内置 9 段中文独白的文学化英译（与中文解说逐段对应）。
  2. 字幕双行烧录：上行英文（配音原文，Arial），下行中文（原独白，simhei）。
  3. 其余（时间轴/混音/loudnorm/环境音压低）与中文版完全一致。

产物: outputs/ep2_vo.mp4（第二集；英文配音+中英双语字幕）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
NAR = os.path.join(ROOT, "outputs", "nar_en")
os.makedirs(NAR, exist_ok=True)

FILM = os.path.join(ROOT, "outputs", "ltx23_film.mp4")
OUT = os.path.join(ROOT, "outputs", "ep2_vo.mp4")
FONT_ZH = r"C\:/Windows/Fonts/simhei.ttf"    # ffmpeg 滤镜里需转义冒号
FONT_EN = r"C\:/Windows/Fonts/arial.ttf"
AMBIENT_VOL = 0.18
NARR_VOL = 2.6
LUFS = "I=-16:TP=-1.5:LRA=11"

TTS_ENGINE = "edge"
VOICE = "en-US-AndrewMultilingualNeural"   # noir 质感男声
TTS_RATE = "-4%"

T = 3.88
X = 0.5
N_SHOTS = 18
NARR_DELAY = 0.25
PAD = 0.6
LUFS_STR = LUFS

# ---- 9 段英文旁白（与中文独白逐段对应）----
LINES_EN = [
    "The rain never stops. In this city, they sell memories by the gram.",
    "This handful of light in my hand — the last part of me no one has taken.",
    "I lie down in that chair. Blue light sweeps my face, and my whole life unfolds.",
    "This fragment buys you three years of peace. His hand was steady when he handed it over.",
    "I stare at the chip — and suddenly, I can't remember who I am.",
    "Long ago, there was a meadow, and an entire afternoon of sunlight.",
    "I push the door and run into the rain. The whole city wakes. Red light falls.",
    "I crush it. The shattered light falls down, like a tiny rain.",
    "That door lights up again. This time, I remember who I am.",
]

# ---- 9 段中文原独白（字幕下行用；与 make_narration.py 的 LINES 一致）----
LINES_ZH = [
    "雨还没停。这座城市里，记忆论克卖。",
    "我手里这捧光，是仅剩的、还没被拿走的部分。",
    "躺上那张椅子，蓝光扫过脸，就能读完我的一生。",
    "这一片，换你三年的安宁。他递过来时，手很稳。",
    "我盯着那枚芯片，忽然想不起自己是谁。",
    "很久以前，有一片草地，和一整个下午的阳光。",
    "我推开门跑进雨里。整座城市醒了，红光落下。",
    "我把它捏碎了。碎光落下来，像一场很小的雨。",
    "那扇门又亮了。这一次，我记得自己是谁。",
]

SB_PATH = os.path.join(ROOT, "outputs", "storyboard.json")


def _zh_ratio(s: str) -> float:
    if not s:
        return 0.0
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff") / len(s)


def _apply_storyboard() -> None:
    """storyboard.json 的中文解说若被 WebUI 修改，字幕中文行同步覆盖。"""
    global LINES_ZH, N_SHOTS
    if not os.path.exists(SB_PATH):
        return
    try:
        with open(SB_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    narr = data.get("narration")
    if isinstance(narr, list) and narr:
        zh = [x.strip() for x in narr if isinstance(x, str) and x.strip()
              and _zh_ratio(x) >= 0.3]
        if zh:
            LINES_ZH = zh
            print(f"[storyboard] 中文字幕行同步: {len(LINES_ZH)} 段")


_apply_storyboard()


def seg_start(k: int) -> float:
    return (k - 1) * SEG_DUR


def synth_edge() -> None:
    import edge_tts

    async def gen(i: int, text: str) -> None:
        mp3 = os.path.join(NAR, f"nar_{i:02d}.mp3")
        wav = os.path.join(NAR, f"nar_{i:02d}.wav")
        ff = ffmpeg_exe()
        comm = edge_tts.Communicate(text, VOICE, rate=TTS_RATE)
        await comm.save(mp3)
        subprocess.run([ff, "-y", "-i", mp3, "-ar", "48000", "-ac", "2",
                        "-c:a", "pcm_s16le", wav],
                       capture_output=True, text=True, timeout=120)

    async def all_lines():
        await asyncio.gather(*(gen(i, t) for i, t in enumerate(LINES_EN, 1)))

    print(f"[tts/edge] 英文神经语音合成 {len(LINES_EN)} 段（{VOICE}）...")
    asyncio.run(all_lines())
    missing = [i for i in range(1, len(LINES_EN) + 1)
               if not os.path.exists(os.path.join(NAR, f"nar_{i:02d}.wav"))]
    if missing:
        raise RuntimeError(f"Edge TTS 未生成分段 {missing}")
    print("[tts/edge] 完成")


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def film_duration(path: str) -> float:
    try:
        r = subprocess.run([ffmpeg_exe(), "-i", path],
                           capture_output=True, text=True, timeout=60)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                      (r.stderr or "") + (r.stdout or ""))
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
        return 0.0
    except Exception:
        return 0.0


def compute_timing() -> None:
    global TOTAL, SEG_DUR, AVAIL
    d = film_duration(FILM)
    if d > 0:
        TOTAL = d
        print(f"[timing] 成片时长 {TOTAL:.2f}s")
    else:
        TOTAL = N_SHOTS * T - (N_SHOTS - 1) * X
    SEG_DUR = TOTAL / max(len(LINES_EN), 1)
    AVAIL = SEG_DUR - PAD


def build_and_render() -> None:
    ff = ffmpeg_exe()
    durs, tempos = [], []
    print("--- 各段时长（可用 %.2fs）---" % AVAIL)
    for i in range(1, len(LINES_EN) + 1):
        p = os.path.join(NAR, f"nar_{i:02d}.wav")
        d = wav_duration(p)
        tempo = 1.0
        if d > AVAIL:
            tempo = min(d / AVAIL, 1.15)
            d = d / tempo
        durs.append(d)
        tempos.append(tempo)
        print(f"  {i:02d} {d:5.2f}s tempo={tempo:.2f}  {LINES_EN[i-1][:24]}...")

    inputs = ["-i", FILM]
    for i in range(1, len(LINES_EN) + 1):
        inputs += ["-i", os.path.join(NAR, f"nar_{i:02d}.wav")]

    parts, labels = [], []
    for i in range(1, len(LINES_EN) + 1):
        ms = int((seg_start(i) + NARR_DELAY) * 1000)
        chain = f"[{i}:a]"
        if tempos[i - 1] > 1.0:
            chain += f"atempo={tempos[i-1]:.4f},"
        chain += (f"adelay={ms}|{ms},"
                  f"aformat=sample_rates=48000:channel_layouts=stereo[n{i}]")
        parts.append(chain)
        labels.append(f"[n{i}]")
    parts.append(f'{"".join(labels)}amix=inputs={len(LINES_EN)}:normalize=0,'
                 f'volume={NARR_VOL}[narr]')
    parts.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                 f"volume={AMBIENT_VOL}[amb]")
    parts.append(f"[amb][narr]amix=inputs=2:normalize=0,loudnorm={LUFS}[aout]")

    # ---- 视频：双行字幕（上行英文 Arial，下行中文 simhei），文本走 textfile ----
    for i in range(1, len(LINES_EN) + 1):
        with open(os.path.join(NAR, f"line_en_{i:02d}.txt"), "w",
                  encoding="utf-8") as f:
            f.write(LINES_EN[i - 1])
        with open(os.path.join(NAR, f"line_zh_{i:02d}.txt"), "w",
                  encoding="utf-8") as f:
            f.write(LINES_ZH[i - 1])
    for i in range(1, len(LINES_EN) + 1):
        s = seg_start(i) + NARR_DELAY
        e = min(s + durs[i - 1] + 0.35, seg_start(i) + SEG_DUR)
        vcur = f"v{i}"
        parts.append(
            f"[{vprev if i > 1 else '0:v'}]"
            f"drawtext=fontfile='{FONT_EN}':textfile=line_en_{i:02d}.txt:"
            f"x=(w-tw)/2:y=h-th-66:fontsize=20:fontcolor=white:"
            f"borderw=2:bordercolor=black@0.9:"
            f"enable='between(t,{s:.3f},{e:.3f})',"
            f"drawtext=fontfile='{FONT_ZH}':textfile=line_zh_{i:02d}.txt:"
            f"x=(w-tw)/2:y=h-th-38:fontsize=23:fontcolor=white:"
            f"borderw=2:bordercolor=black@0.9:"
            f"enable='between(t,{s:.3f},{e:.3f})'[{vcur}]"
        )
        vprev = vcur

    fc = ";".join(parts)
    cmd = [ff, "-y", *inputs, "-filter_complex", fc,
           "-map", f"[{vcur}]", "-map", "[aout]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", "25",
           "-c:a", "aac", "-b:a", "192k", "-shortest", OUT]

    print("[render] 合成中（英语配音+双语字幕+环境音）...")
    r = subprocess.run(cmd, cwd=NAR, capture_output=True, text=True, timeout=1800)
    if not (os.path.exists(OUT) and os.path.getsize(OUT) > 0):
        print("[err] ffmpeg 失败:\n", (r.stderr or "")[-2500:])
        return
    print(f"[OK] -> {OUT}  {os.path.getsize(OUT)/2**20:.2f}MB")


TOTAL = 0.0
SEG_DUR = 0.0
AVAIL = 0.0


def main():
    global FILM, OUT, AUTO_DUR
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", default=FILM)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--auto-dur", action="store_true",
                    help="探测成片真实时长作为时间轴（推荐）")
    a = ap.parse_args()

    AUTO_DUR = a.auto_dur
    FILM = a.film if os.path.isabs(a.film) else os.path.normpath(os.path.join(ROOT, a.film))
    OUT = a.out if os.path.isabs(a.out) else os.path.normpath(os.path.join(ROOT, a.out))

    synth_edge()
    compute_timing()
    build_and_render()


if __name__ == "__main__":
    main()
