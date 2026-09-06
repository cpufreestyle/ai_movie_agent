#!/usr/bin/env python
"""为《看见未来之前》加解说配音 + 烧录字幕。

默认（2026-09-06 用户规则）：**英文配音 + 中英双语字幕**
  - 配音：Edge TTS 英文神经语音 en-US-AndrewMultilingualNeural（noir 男声）
  - 字幕：上行英文(Arial) + 下行中文(simhei)
  - 想回中文版：--lang zh --subs zh

设计要点：
  1. 配音用微软神经语音 Edge TTS，自然度远高于 Windows 自带 SAPI。
     需联网；断网时自动回退 SAPI(Microsoft Huihui) 离线合成。
  2. 解说一句跨两个镜头（每段 7.26s），比一句一镜更自然，台词也不用被压短到失真。
  3. 中文不经过 shell 命令行（GBK 会弄乱），台词写进 UTF-8 文件，ffmpeg 用 textfile 读取。
  4. 字幕字体直接指定字体文件路径，避免 libass/fontconfig 找不到字体变方框。
  5. 原 LTX 环境音压到 0.18 做背景垫底，解说在上层。
  6. 双语必须成对：中英文行逐段对应同一故事。storyboard.json 只存中文 narration，
     若它讲的是另一个故事而英文行没同步，硬覆盖会造成双语错位，故要求同时提供
     narration_en 才覆盖（详见 _apply_storyboard）。

产物: outputs/ep1_vo.mp4（默认第一集；第三集链路用 --out outputs/ep3_vo.mp4）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
NAR = os.path.join(ROOT, "outputs", "nar")
os.makedirs(NAR, exist_ok=True)

FILM = os.path.join(ROOT, "outputs", "ltx23_film.mp4")
OUT = os.path.join(ROOT, "outputs", "ep1_vo.mp4")
FONT = r"C\:/Windows/Fonts/simhei.ttf"   # ffmpeg 滤镜里需转义冒号
FONT_EN = r"C\:/Windows/Fonts/arial.ttf"
AMBIENT_VOL = 0.18                        # 原环境音压低，给解说让位
NARR_VOL = 2.6                            # 解说提升，确保压在环境音之上清晰可辨
# 响度目标：网络视频常用 -16 LUFS；用默认 -24 会把整片压得过轻（实测比原片还小声）
LUFS = "I=-16:TP=-1.5:LRA=11"

# ---- 配音引擎 ----
# edge = 微软神经语音(联网, 最自然)；断网时自动回退 sapi = Windows 离线(兜底)
TTS_ENGINE = "edge"
# 默认：英文配音 + 中英双语字幕（用户规则 2026-09-06：默认都用英文和双语字幕）
# 想回中文版：--lang zh --subs zh
LANG = "en"          # en=英文配音, zh=中文配音
SUBS = "bilingual"   # bilingual=中英双行, en=仅英文, zh=仅中文
VOICE_EN = "en-US-AndrewMultilingualNeural"   # noir 质感英文男声
VOICE_ZH = "zh-CN-XiaoxiaoNeural"             # 小晓神经语音，中文最自然之一
VOICE = VOICE_EN if LANG == "en" else VOICE_ZH
TTS_RATE = "-4%"                    # 略慢更旁白感；短句前提下仍落进段落预算，无需加速
TTS_STYLE = "narration-relaxed"     # 松弛旁白风格，避免念稿腔（仅中文语音支持）

# ---- 时间轴参数（18 镜 / 9 段解说，均可被 storyboard.json 覆盖）----
T = 3.88          # 单镜时长
X = 0.5           # 转场时长
N_SHOTS = 18
NARR_DELAY = 0.25         # 解说相对段首的起播偏移
PAD = 0.6                 # 段内留白，避免解说贴边

# ---- 9 段解说（第一人称内心独白）----
LINES = [
    "雨还没停。这座城市里,记忆论克卖。",
    "我手里这捧光,是仅剩的、还没被拿走的部分。",
    "躺上那张椅子,蓝光扫过脸,就能读完我的一生。",
    "这一片,换你三年的安宁。他递过来时,手很稳。",
    "我盯着那枚芯片,忽然想不起自己是谁。",
    "很久以前,有一片草地,和一整个下午的阳光。",
    "我推开门跑进雨里。整座城市醒了,红光落下。",
    "我把它捏碎了。碎光落下来,像一场很小的雨。",
    "那扇门又亮了。这一次,我记得自己是谁。",
]
LINES = [s.replace(",", "，") for s in LINES]   # 统一用全角逗号

# ---- 英文行：与上面 9 段中文逐段对应，作为英文配音文本 + 双行字幕上行 ----
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
# 实际送 TTS 的台词（LANG=en 时用英文行）
TTS_LINES = LINES_EN if LANG == "en" else LINES

# ---- WebUI 编辑覆盖：outputs/storyboard.json ----
SB_PATH = os.path.join(ROOT, "outputs", "storyboard.json")


def _zh_ratio(s: str) -> float:
    """字符串里中文字符的占比，用于识别「解说被写成了英文」。"""
    if not s:
        return 0.0
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff") / len(s)


def _apply_storyboard() -> None:
    """WebUI 改完分镜/解说会存到 outputs/storyboard.json，存在则覆盖内置值。

    双语模式下必须「成对覆盖」：中英文行要逐段对应同一个故事。
    storyboard.json 只存中文 narration，若它描述的是另一个故事（如现在的 Mira），
    而英文行还是内置的《看见未来之前》，硬覆盖就会出现
    「英文行讲 A 故事、中文行讲 B 故事」的双语错位 —— 比不覆盖更糟。
    因此：只有 storyboard 同时提供 narration_en（等长）时才成对覆盖；
    否则警告并保留内置成对台词。
    """
    global LINES, LINES_EN, TTS_LINES, N_SHOTS
    if not os.path.exists(SB_PATH):
        return
    try:
        with open(SB_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # 文件坏了就回退内置值，别把合成搞挂
        print(f"[warn] 读取分镜覆盖失败({e})，使用内置分镜")
        return
    shots = data.get("shots")
    if isinstance(shots, list) and shots:
        N_SHOTS = len(shots)
    narr = data.get("narration")
    narr_en = data.get("narration_en")
    if not (isinstance(narr, list) and narr and all(isinstance(x, str) for x in narr)):
        return
    cand = [x.strip() for x in narr if x.strip()]
    zh = [x for x in cand if _zh_ratio(x) >= 0.3]
    if not zh:
        print(f"[warn] storyboard.json 的解说全非中文，回退内置解说（{len(LINES)} 段）")
        return
    if len(zh) < len(cand):
        print(f"[warn] storyboard.json 中 {len(cand) - len(zh)} 段解说非中文，已忽略")
    # 双语：需要等长的英文行才成对覆盖
    if SUBS == "bilingual" or LANG == "en":
        en = [x.strip() for x in narr_en
              if isinstance(x, str) and x.strip()] if isinstance(narr_en, list) else []
        if len(en) == len(zh) and len(zh) == len(LINES_EN):
            LINES, LINES_EN = zh, en
            TTS_LINES = LINES_EN if LANG == "en" else LINES
            print(f"[storyboard] 成对覆盖中英解说：{N_SHOTS} 镜 / {len(LINES)} 段")
        else:
            print(f"[warn] storyboard.json 缺少等长英文解说(narration_en)，"
                  f"为避免双语错位，保留内置成对台词（{len(LINES_EN)} 段）")
            print("[hint] 想让 WebUI 改的解说生效，请在 storyboard.json 里同时写 narration_en")
        return
    # 纯中文模式：照旧覆盖
    LINES = zh
    TTS_LINES = LINES
    print(f"[storyboard] 使用 outputs/storyboard.json：{N_SHOTS} 镜 / {len(LINES)} 段解说")


def _apply_lines_file(path: str) -> None:
    """从 JSON 文件加载「某一集」的专属台词，优先级高于 storyboard / 内置。

    各集台词不同（第二集=内置《看见未来之前》、第三集=storyboard 的 Mira），
    若都写进 storyboard.json 会互相覆盖，故每集单独存一个台词文件，用
    --lines-json 指定。文件格式：{"narration":[中文...], "narration_en":[英文...]}

    双语必须成对，这里强制校验等长，避免又出现「英讲 A、中讲 B」。
    """
    global LINES, LINES_EN, TTS_LINES
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    zh = [x.strip() for x in (data.get("narration") or [])
          if isinstance(x, str) and x.strip()]
    en = [x.strip() for x in (data.get("narration_en") or [])
          if isinstance(x, str) and x.strip()]
    if not zh:
        raise RuntimeError(f"台词文件缺少 narration（中文行）: {path}")
    if len(en) != len(zh):
        raise RuntimeError(
            f"台词文件 narration({len(zh)}) 与 narration_en({len(en)}) 不等长，"
            f"双语会错位，已中止: {path}")
    LINES, LINES_EN = zh, en
    TTS_LINES = LINES_EN if LANG == "en" else LINES
    print(f"[lines] 使用指定台词文件：{os.path.basename(path)}（{len(zh)} 段）")


_apply_storyboard()

# 段长按"解说段数"均分全片 —— 这样增删分镜/解说都不会错位。
# TOTAL/SEG_DUR/AVAIL 在 main() 里根据 CLI 参数（--auto-dur / --t）动态计算，
# 不在此硬算，以便同一脚本服务 LTX-2.3 / LTX-2.5 等不同成片。
RATE = 0  # SAPI 语速 -10~10（仅离线兜底时使用，0=默认）
AUTO_DUR = False  # 为 True 时用 ffprobe 探测成片真实时长作为 TOTAL（模型无关，最稳）


def seg_start(k: int) -> float:
    """第 k 段(1起)的起始时间。"""
    return (k - 1) * SEG_DUR


def write_line_files() -> None:
    """写字幕文本。中文行 line_zh_XX.txt、英文行 line_en_XX.txt（ffmpeg textfile 读取）。"""
    for i, t in enumerate(LINES, 1):
        with open(os.path.join(NAR, f"line_zh_{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write(t)
        # 兼容旧引用：line_XX.txt 始终指向中文行
        with open(os.path.join(NAR, f"line_{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write(t)
    for i, t in enumerate(LINES_EN, 1):
        with open(os.path.join(NAR, f"line_en_{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write(t)


def write_ps1() -> str:
    body = ["Add-Type -AssemblyName System.Speech",
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
            '$s.SelectVoice("Microsoft Huihui Desktop")',
            f"$s.Rate = {RATE}",
            "$lines = @("]
    for t in TTS_LINES:
        body.append(f'    "{t}"')
    body += [")",
             "for ($i = 0; $i -lt $lines.Count; $i++) {",
             '    $idx = "{0:D2}" -f ($i + 1)',
             f'    $s.SetOutputToWaveFile("{NAR.replace(chr(92), "/")}/nar_$idx.wav")',
             "    $s.Speak($lines[$i])",
             "}",
             "$s.Dispose()",
             'Write-Output "TTS_DONE"']
    ps1 = os.path.join(NAR, "tts.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:   # BOM: 让 PowerShell 正确解中文
        f.write("\n".join(body))
    return ps1


def _esc_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def synth_sapi() -> None:
    """离线兜底：Windows SAPI(Microsoft Huihui) 合成。"""
    ps1 = write_ps1()
    print(f"[tts/sapi] 合成 {len(TTS_LINES)} 段解说...")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", ps1], capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    if "TTS_DONE" not in out:
        raise RuntimeError("TTS 失败: " + out[-1500:])
    print("[tts/sapi] 完成")


def synth_edge() -> None:
    """默认引擎：微软神经语音 Edge TTS，自然度远高于 SAPI，需联网。"""
    import asyncio
    try:
        import edge_tts
    except Exception as e:
        raise RuntimeError(f"未安装 edge_tts（pip install edge-tts）：{e}")
    ff = ffmpeg_exe()

    async def gen(i: int, text: str) -> None:
        mp3 = os.path.join(NAR, f"nar_{i:02d}.mp3")
        wav = os.path.join(NAR, f"nar_{i:02d}.wav")
        # 纯文本+语速：自然语速下每段解说约 4~6s，刚好落进段落预算，无需加速失真。
        # （narration-relaxed 等情绪风格会把语速拖到 ~10s/句，迫使 1.5x 加速反而更机械，故不用。）
        comm = edge_tts.Communicate(text, VOICE, rate=TTS_RATE)
        await comm.save(mp3)
        subprocess.run([ff, "-y", "-i", mp3, "-ar", "48000", "-ac", "2",
                        "-c:a", "pcm_s16le", wav],
                       capture_output=True, text=True, timeout=120)

    async def all_lines():
        await asyncio.gather(*(gen(i, t) for i, t in enumerate(TTS_LINES, 1)))

    print(f"[tts/edge] 神经语音合成 {len(TTS_LINES)} 段解说（{VOICE} / lang={LANG}）...")
    asyncio.run(all_lines())
    missing = [i for i in range(1, len(TTS_LINES) + 1)
               if not os.path.exists(os.path.join(NAR, f"nar_{i:02d}.wav"))]
    if missing:
        raise RuntimeError(f"Edge TTS 未生成分段 {missing}（可能断网或语音不可用）")
    print("[tts/edge] 完成")


def synth() -> None:
    if TTS_ENGINE == "edge":
        try:
            synth_edge()
            return
        except Exception as e:
            print(f"[warn] Edge TTS 失败（{e}），回退 Windows SAPI 离线语音")
    synth_sapi()


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def film_duration(path: str) -> float:
    """探测成片真实时长（秒），用于 --auto-dur 自动对齐解说时间轴。

    注意：imageio_ffmpeg 只提供 ffmpeg 可执行文件，不提供 ffprobe。
    故用 `ffmpeg -i` 解析其 stderr 里的 Duration 字段，而非调 ffprobe。
    """
    import re
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
    """按当前 FILM / T / X / N_SHOTS / AUTO_DUR 重算 TOTAL/SEG_DUR/AVAIL（供 seg_start 使用）。"""
    global TOTAL, SEG_DUR, AVAIL
    if AUTO_DUR:
        d = film_duration(FILM)
        if d > 0:
            TOTAL = d
            print(f"[timing] 探测成片时长 {TOTAL:.2f}s（--auto-dur）")
        else:
            print("[warn] --auto-dur 探测失败，回退按 N_SHOTS*T 估算")
            TOTAL = N_SHOTS * T - (N_SHOTS - 1) * X
    else:
        TOTAL = N_SHOTS * T - (N_SHOTS - 1) * X
    SEG_DUR = TOTAL / max(len(LINES), 1)
    AVAIL = SEG_DUR - PAD


def build_and_render() -> None:
    ff = ffmpeg_exe()
    durs, tempos = [], []
    print("--- 各段时长（可用 %.2fs）---" % AVAIL)
    for i in range(1, len(TTS_LINES) + 1):
        p = os.path.join(NAR, f"nar_{i:02d}.wav")
        d = wav_duration(p)
        tempo = 1.0
        if d > AVAIL:
            tempo = min(d / AVAIL, 1.15)   # 最多加速 1.15 倍，超过则宁可略微截断，避免明显机械感
            d = d / tempo
        durs.append(d)
        tempos.append(tempo)
        print(f"  {i:02d} {d:5.2f}s tempo={tempo:.2f}  {TTS_LINES[i-1][:16]}...")

    # ---- 音频：9 段延时后混合，再与原环境音混合 ----
    inputs = ["-i", FILM]
    for i in range(1, len(TTS_LINES) + 1):
        inputs += ["-i", os.path.join(NAR, f"nar_{i:02d}.wav")]

    parts, labels = [], []
    for i in range(1, len(TTS_LINES) + 1):
        ms = int((seg_start(i) + NARR_DELAY) * 1000)
        chain = f"[{i}:a]"
        if tempos[i - 1] > 1.0:
            chain += f"atempo={tempos[i-1]:.4f},"
        chain += (f"adelay={ms}|{ms},"
                  f"aformat=sample_rates=48000:channel_layouts=stereo[n{i}]")
        parts.append(chain)
        labels.append(f"[n{i}]")
    parts.append(f'{"".join(labels)}amix=inputs={len(TTS_LINES)}:normalize=0,'
                 f'volume={NARR_VOL}[narr]')
    parts.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                 f"volume={AMBIENT_VOL}[amb]")
    parts.append(f"[amb][narr]amix=inputs=2:normalize=0,loudnorm={LUFS}[aout]")

    # ---- 视频：逐段烧字幕（文本走 textfile，规避命令行中文编码）----
    # 默认双语：上行英文(Arial) + 下行中文(simhei)
    vprev, vcur = "0:v", None
    for i in range(1, len(TTS_LINES) + 1):
        s = seg_start(i) + NARR_DELAY
        e = min(s + durs[i - 1] + 0.35, seg_start(i) + SEG_DUR)
        vcur = f"v{i}"
        filters = []
        if SUBS in ("bilingual", "en"):
            filters.append(
                f"drawtext=fontfile='{FONT_EN}':textfile=line_en_{i:02d}.txt:"
                f"x=(w-tw)/2:y=h-th-66:fontsize=20:fontcolor=white:"
                f"borderw=2:bordercolor=black@0.9:"
                f"enable='between(t,{s:.3f},{e:.3f})'")
        if SUBS in ("bilingual", "zh"):
            filters.append(
                f"drawtext=fontfile='{FONT}':textfile=line_zh_{i:02d}.txt:"
                f"x=(w-tw)/2:y=h-th-38:fontsize=23:fontcolor=white:"
                f"borderw=2:bordercolor=black@0.9:"
                f"enable='between(t,{s:.3f},{e:.3f})'")
        parts.append(f"[{vprev}]" + ",".join(filters) + f"[{vcur}]")
        vprev = vcur

    fc = ";".join(parts)
    cmd = [ff, "-y", *inputs, "-filter_complex", fc,
           "-map", f"[{vcur}]", "-map", "[aout]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", "25",
           "-c:a", "aac", "-b:a", "192k", "-shortest", OUT]

    print("[render] 合成中（字幕+解说+环境音）...")
    # cwd 设为字幕文本目录，滤镜里才能用相对文件名，规避 Windows 路径转义
    r = subprocess.run(cmd, cwd=NAR, capture_output=True, text=True, timeout=1800)
    if not (os.path.exists(OUT) and os.path.getsize(OUT) > 0):
        print("[err] ffmpeg 失败:\n", (r.stderr or "")[-2500:])
        return
    print(f"[OK] -> {OUT}  {os.path.getsize(OUT)/2**20:.2f}MB")


def main():
    global FILM, OUT, T, X, N_SHOTS, AUTO_DUR, LANG, SUBS, VOICE, TTS_LINES
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", default=FILM, help="输入成片（含原生音轨）")
    ap.add_argument("--out", default=OUT, help="输出带解说+字幕的成片")
    ap.add_argument("--t", type=float, default=T, help="单镜时长(秒)，用于时间轴估算")
    ap.add_argument("--xfade", type=float, default=X, help="转场时长(秒)")
    ap.add_argument("--shots", type=int, default=0, help="镜头数（覆盖 storyboard 推断）")
    ap.add_argument("--auto-dur", action="store_true",
                    help="用 ffprobe 探测成片真实时长作为时间轴（推荐，模型无关）")
    ap.add_argument("--lang", choices=["en", "zh"], default=LANG,
                    help="配音语言（默认 en 英文）")
    ap.add_argument("--subs", choices=["bilingual", "en", "zh"], default=SUBS,
                    help="字幕模式（默认 bilingual 中英双语）")
    ap.add_argument("--lines-json", default="",
                    help="某一集的专属台词文件 JSON（narration + narration_en），"
                         "优先级高于 storyboard/内置，用于各集台词互不覆盖")
    a = ap.parse_args()

    # build_and_render 会把 cwd 切到 outputs/nar 找字幕文本，
    # 故 FILM/OUT 必须基于 ROOT 绝对化，否则 ffmpeg 相对路径解析失败。
    FILM = a.film if os.path.isabs(a.film) else os.path.normpath(os.path.join(ROOT, a.film))
    OUT = a.out if os.path.isabs(a.out) else os.path.normpath(os.path.join(ROOT, a.out))
    T = a.t
    X = a.xfade
    if a.shots:
        N_SHOTS = a.shots
    AUTO_DUR = a.auto_dur
    LANG = a.lang
    SUBS = a.subs
    VOICE = VOICE_EN if LANG == "en" else VOICE_ZH
    TTS_LINES = LINES_EN if LANG == "en" else LINES
    # LANG/SUBS 变了，分镜覆盖的分支判定也要跟着重跑一次
    _apply_storyboard()
    # 指定集的台词文件最后应用，覆盖 storyboard / 内置台词
    if a.lines_json:
        _apply_lines_file(a.lines_json)
    print(f"[cfg] 配音={LANG}({VOICE}) 字幕={SUBS} -> {OUT}")

    compute_timing()
    write_line_files()
    synth()
    build_and_render()


if __name__ == "__main__":
    main()
