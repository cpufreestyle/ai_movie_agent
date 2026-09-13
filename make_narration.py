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
import textwrap

ROOT = os.path.dirname(os.path.abspath(__file__))
NAR = os.path.join(ROOT, "outputs", "nar")
os.makedirs(NAR, exist_ok=True)

FILM = os.path.join(ROOT, "outputs", "ltx25_film.mp4")
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
VOICE_EN = "en-GB-SoniaNeural"               # 英式知性女声：成熟、有电影旁白质感，贴合剧情独白
VOICE_ZH = "zh-CN-XiaoxiaoNeural"             # 小晓神经语音，中文最自然之一
VOICE = VOICE_EN if LANG == "en" else VOICE_ZH
TTS_RATE = "-2%"                    # 略慢更旁白感，避免念稿腔
TTS_STYLE = "narration-relaxed"     # 松弛旁白风格，避免念稿腔（仅中文语音支持）

# ---- 分段情感曲线 (rate, pitch)：逐段起伏，贴合剧情 ----
# 实测 Edge 端点仅接受 rate/volume/pitch；任何额外 SSML（mstts:express-as / <break>）
# 都会导致 NoAudioReceived，故靠逐段 rate+pitch 变化制造抑扬顿挫，避免整片平铺直叙。
EMO = [
    ("-2%", "+0Hz"), ("-3%", "-1Hz"), ("-2%", "+2Hz"),
    ("-4%", "-2Hz"), ("-3%", "-1Hz"), ("-1%", "+1Hz"),
    ("-5%", "+1Hz"), ("-2%", "+0Hz"), ("-5%", "-2Hz"),
]

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


def _set_lines(zh: list, en: list, label: str) -> None:
    """写入台词，强制双语成对（中英文行数相等），避免「英讲 A、中讲 B」。"""
    global LINES, LINES_EN, TTS_LINES
    if not zh:
        raise RuntimeError(f"台词缺少 narration（中文行）: {label}")
    if len(en) != len(zh):
        raise RuntimeError(
            f"台词 narration({len(zh)}) 与 narration_en({len(en)}) 不等长，"
            f"双语会错位，已中止: {label}")
    LINES, LINES_EN = zh, en
    TTS_LINES = LINES_EN if LANG == "en" else LINES
    print(f"[lines] {label}（{len(zh)} 段）")


def _apply_lines_file(path: str) -> None:
    """从 JSON 文件加载「某一集」的专属台词，优先级高于 storyboard / 内置。

    各集台词不同（第二集=内置《看见未来之前》、第三集=storyboard 的 Mira），
    若都写进 storyboard.json 会互相覆盖，故每集单独存一个台词文件，用
    --lines-json 指定。文件格式：{"narration":[中文...], "narration_en":[英文...]}

    双语必须成对，这里强制校验等长，避免又出现「英讲 A、中讲 B」。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    zh = [x.strip() for x in (data.get("narration") or [])
          if isinstance(x, str) and x.strip()]
    en = [x.strip() for x in (data.get("narration_en") or [])
          if isinstance(x, str) and x.strip()]
    _set_lines(zh, en, f"使用指定台词文件：{os.path.basename(path)}")


def _apply_series_episode(path: str, ep: int) -> None:
    """从系列剧本 outputs/series_script.json 取第 N 集台词（三集连贯版）。

    文件结构：{"ep1": {"title":..., "narration":[...], "narration_en":[...]}, ...}
    三集共用同一份连贯剧本（跨集互文 + 钩子链），取代各自散落的 lines_*.json，
    是本脚本优先级最高的台词来源。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    node = data.get(f"ep{ep}") or data.get(str(ep))
    if not isinstance(node, dict):
        avail = [k for k, v in data.items() if isinstance(v, dict)]
        raise RuntimeError(f"系列剧本缺少第 {ep} 集（可用集: {avail}）: {path}")
    zh = [x.strip() for x in (node.get("narration") or [])
          if isinstance(x, str) and x.strip()]
    en = [x.strip() for x in (node.get("narration_en") or [])
          if isinstance(x, str) and x.strip()]
    _set_lines(zh, en,
               f"系列剧本 {os.path.basename(path)} 第{ep}集「{node.get('title', '')}」")


_apply_storyboard()

# 段长按"解说段数"均分全片 —— 这样增删分镜/解说都不会错位。
# TOTAL/SEG_DUR/AVAIL 在 main() 里根据 CLI 参数（--auto-dur / --t）动态计算，
# 不在此硬算，以便同一脚本服务 LTX-2.3 / LTX-2.5 等不同成片。
RATE = 0  # SAPI 语速 -10~10（仅离线兜底时使用，0=默认）
AUTO_DUR = False  # 为 True 时用 ffprobe 探测成片真实时长作为 TOTAL（模型无关，最稳）
FIT_FILM = False   # 旁白整体压缩+顺排以匹配成片时长（用于比旁白短的成片，如 33.5s 的 H3 版）
FPS = 25           # 输出帧率（H3 成片为 24fps，须传 --fps 24 否则 -shortest 会把音轨截短）
# FIT 模式专用全局：各段顺排起点、统一变速、压缩后时长
SEG_STARTS, GLOBAL_TEMPO, COMP_DUR, TEMPOS, BLOCK_SPAN = [], 1.0, [], [], 0.0
# 强制对齐（--align）：ASR 取真实语音时间戳后收紧字幕窗口；需可选依赖 faster-whisper
ALIGN = False
ALIGN_MODEL = "small"
ALIGN_LANG = None        # None = 自动检测语言
SPEECH_SPANS = None      # list[dict|None]，与 TTS_LINES 等长；None 表示尚未/无法对齐


def seg_start(k: int) -> float:
    """第 k 段(1起)的起始时间。FIT 模式用顺排起点，否则按段均分。"""
    if FIT_FILM and SEG_STARTS:
        return SEG_STARTS[k - 1]
    return (k - 1) * SEG_DUR


def _trim_silence() -> None:
    """FIT 模式：去掉每段旁白首尾静音，缩短总时长、降低所需压缩比（避免念稿腔）。"""
    ff = ffmpeg_exe()
    for i in range(1, len(TTS_LINES) + 1):
        src = os.path.join(NAR, f"nar_{i:02d}.wav")
        tmp = os.path.join(NAR, f"_trim_{i:02d}.wav")
        subprocess.run([ff, "-y", "-i", src, "-af",
                        "silenceremove=start_periods=1:start_duration=0.08:"
                        "start_threshold=-40dB:stop_periods=1:stop_duration=0.25:"
                        "stop_threshold=-40dB", tmp],
                       capture_output=True, text=True, timeout=120)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, src)
    print("[trim] 去首尾静音完成")


def _probe_wh(path):
    """探测视频宽高，字幕字号/排版据此自适应。"""
    try:
        import cv2
        v = cv2.VideoCapture(path)
        w = int(v.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(v.get(cv2.CAP_PROP_FRAME_HEIGHT))
        v.release()
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 1024, 576


def _wrap_zh(text, n):
    """中文按字符数硬换行（避免单行超出画面宽度）。"""
    text = (text or "").strip()
    if not text:
        return [""]
    return [text[k:k + n] for k in range(0, len(text), n)]


def _sub_sizes(w):
    """按视频宽度算自适应字号与每行最大字符数（英文字号偏小以保证多为一行）。"""
    en_fs = min(32, max(20, round(w * 0.022)))
    zh_fs = min(38, max(24, round(w * 0.030)))
    en_max = max(24, int(w * 0.95 / (en_fs * 0.55)))
    zh_max = max(12, int(w * 0.96 / (zh_fs * 1.02)))
    return en_fs, zh_fs, en_max, zh_max


def write_line_files() -> None:
    """写字幕文本（长行按画面宽度自动换行，避免超出屏幕）。"""
    w, _ = _probe_wh(FILM)
    _, _, EN_MAX, ZH_MAX = _sub_sizes(w)
    for i, t in enumerate(LINES, 1):
        zh = _wrap_zh(t, ZH_MAX)
        with open(os.path.join(NAR, f"line_zh_{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(zh))
        # 兼容旧引用：line_XX.txt 始终指向中文行
        with open(os.path.join(NAR, f"line_{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(zh))
    for i, t in enumerate(LINES_EN, 1):
        en = textwrap.wrap(t, EN_MAX) or [t]
        with open(os.path.join(NAR, f"line_en_{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(en))


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
        # 逐段情感曲线：rate/pitch 随剧情起伏（Edge 仅认这三项 prosody）。
        rate, pitch = EMO[(i - 1) % len(EMO)]
        comm = edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch)
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
    """按当前 FILM / T / X / N_SHOTS / AUTO_DUR 重算 TOTAL/SEG_DUR/AVAIL（供 seg_start 使用）。

    镜头块对齐（修复「旁白与画面不同步」）：成片由 N_SHOTS 个镜头经 xfade 拼接，
    旁白 n 段通常按「2 镜 1 段」对应。每段旁白应落在它覆盖的镜头块起点，并贴合
    该镜头块时长——超长则整体压缩到该块时长（避免与下一段重叠），不足则 1.0x
    原速（段间留自然空隙）。起点按 slot 整数倍排布，不再因顺序平铺累积漂移。
    """
    global TOTAL, SEG_DUR, AVAIL, SEG_STARTS, GLOBAL_TEMPO, COMP_DUR, TEMPOS, BLOCK_SPAN
    global SPEECH_SPANS
    n = len(TTS_LINES)
    # 强制对齐：只对每段旁白 wav 做一次 ASR，取真实语音首/尾时间戳（用于字幕窗口）
    if ALIGN and SPEECH_SPANS is None:
        try:
            from agent import align
            wavs = [os.path.join(NAR, f"nar_{i:02d}.wav") for i in range(1, n + 1)]
            SPEECH_SPANS = align.speech_spans(wavs, model_size=ALIGN_MODEL,
                                              language=ALIGN_LANG)
            if SPEECH_SPANS is None:
                print("[align] faster-whisper 不可用，字幕沿用估算时间轴"
                      "（pip install faster-whisper 可启用）")
            else:
                print(f"[align] ASR 完成：{sum(1 for s in SPEECH_SPANS if s)}/{n} "
                      f"段取到语音时间戳")
        except Exception as e:        # noqa: BLE001 - ASR 失败只降级，不能中断出片
            print(f"[align] 初始化失败，沿用估算时间轴: {e}")
            SPEECH_SPANS = None
    total = film_duration(FILM) if AUTO_DUR else (N_SHOTS * T - (N_SHOTS - 1) * X)
    TOTAL = total
    # 镜头块对齐：N_SHOTS 镜 / n 段，通常 2 镜对应 1 段
    if N_SHOTS and N_SHOTS >= n and (N_SHOTS % n == 0):
        sps = N_SHOTS // n                            # 每段覆盖镜头数（如 2）
        T_actual = (total + (N_SHOTS - 1) * X) / N_SHOTS  # 单镜真实时长
        slot = sps * (T_actual - X)                   # 每段旁白的可用时长槽（非重叠）
        starts = [i * slot for i in range(n)]
        comp, tempo = [], []
        for i in range(1, n + 1):
            nat = wav_duration(os.path.join(NAR, f"nar_{i:02d}.wav"))
            if nat > slot:
                # 限速：旁白最高加速到 1.15x，避免被压成"念稿腔"
                t = min(nat / slot, 1.15)
                tempo.append(t)
                comp.append(nat / t)
            else:
                tempo.append(1.0)
                comp.append(nat)
        SEG_STARTS, COMP_DUR, TEMPOS, BLOCK_SPAN = starts, comp, tempo, slot
        GLOBAL_TEMPO = 1.0
        SEG_DUR = slot
        AVAIL = slot - PAD
        print(f"[timing/align] {N_SHOTS}镜/{n}段, 每镜{T_actual:.3f}s, "
              f"每段覆盖{sps}镜≈{slot:.2f}s；旁白贴合镜头块（超则压缩/不足留隙）")
        return
    # 回退：均匀分段
    SEG_DUR = TOTAL / max(n, 1)
    AVAIL = SEG_DUR - PAD
    SEG_STARTS = [i * SEG_DUR for i in range(n)]
    BLOCK_SPAN = SEG_DUR
    comp, tempo = [], []
    for i in range(1, n + 1):
        nat = wav_duration(os.path.join(NAR, f"nar_{i:02d}.wav"))
        if nat > AVAIL:
            t = min(nat / AVAIL, 1.15)
            tempo.append(t); comp.append(nat / t)
        else:
            tempo.append(1.0); comp.append(nat)
    COMP_DUR, TEMPOS = comp, tempo
    GLOBAL_TEMPO = 1.0


def build_and_render() -> None:
    ff = ffmpeg_exe()
    durs, tempos = list(COMP_DUR), list(TEMPOS)
    n = len(TTS_LINES)
    print("--- 各段时长（可用 %.2fs）---" % AVAIL)
    for i in range(1, n + 1):
        d, tempo = durs[i - 1], tempos[i - 1]
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
    # 强制对齐：有真实语音时间戳就用它，否则沿用"段起点 + wav 时长"的估算窗口
    cues = None
    if ALIGN and SPEECH_SPANS:
        try:
            from agent import align
            cues = align.cues_for_lines(SPEECH_SPANS, SEG_STARTS, tempo=TEMPOS,
                                        delay=NARR_DELAY, total=TOTAL)
            print(f"[align] 字幕按语音时间戳对齐（{len(cues)} 段）")
        except Exception as e:        # noqa: BLE001
            print(f"[align] 对齐失败，沿用估算时间轴: {e}")
            cues = None

    vprev, vcur = "0:v", None
    Wv, Hv = _probe_wh(FILM)
    EN_FS, ZH_FS, _EN_MAX, _ZH_MAX = _sub_sizes(Wv)
    ZH_MARGIN = max(16, int(Hv * 0.05))       # 中文距底
    EN_MARGIN = max(64, int(Hv * 0.15))       # 英文距底：底部锁定，多行向上生长，永不压到中文
    for i in range(1, len(TTS_LINES) + 1):
        if cues:
            s, e = cues[i - 1]
        else:
            s = seg_start(i) + NARR_DELAY
            e = s + durs[i - 1] + 0.10
            if i < n:
                e = min(e, SEG_STARTS[i] - 0.05)   # 不侵入下一段起点
            else:
                e = min(e, TOTAL - 0.05)
        vcur = f"v{i}"
        filters = []
        if SUBS in ("bilingual", "en"):
            filters.append(
                f"drawtext=fontfile='{FONT_EN}':textfile=line_en_{i:02d}.txt:"
                f"x=(w-tw)/2:y=h-th-{EN_MARGIN}:fontsize={EN_FS}:fontcolor=white:"
                f"borderw=3:bordercolor=black@0.9:"
                f"enable='between(t,{s:.3f},{e:.3f})'")
        if SUBS in ("bilingual", "zh"):
            filters.append(
                f"drawtext=fontfile='{FONT}':textfile=line_zh_{i:02d}.txt:"
                f"x=(w-tw)/2:y=h-th-{ZH_MARGIN}:fontsize={ZH_FS}:fontcolor=white:"
                f"borderw=3:bordercolor=black@0.9:"
                f"enable='between(t,{s:.3f},{e:.3f})'")
        parts.append(f"[{vprev}]" + ",".join(filters) + f"[{vcur}]")
        vprev = vcur

    fc = ";".join(parts)
    cmd = [ff, "-y", *inputs, "-filter_complex", fc,
           "-map", f"[{vcur}]", "-map", "[aout]",
           "-c:v", "libx264", "-crf", "18", "-preset", "medium",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", OUT]

    print("[render] 合成中（字幕+解说+环境音）...")
    # cwd 设为字幕文本目录，滤镜里才能用相对文件名，规避 Windows 路径转义
    r = subprocess.run(cmd, cwd=NAR, capture_output=True, text=True, timeout=1800)
    if not (os.path.exists(OUT) and os.path.getsize(OUT) > 0):
        print("[err] ffmpeg 失败:\n", (r.stderr or "")[-2500:])
        return
    print(f"[OK] -> {OUT}  {os.path.getsize(OUT)/2**20:.2f}MB")
    if cues:
        from agent import align
        p = align.write_srt(cues, TTS_LINES, os.path.splitext(OUT)[0] + ".srt")
        if p:
            print(f"[align] SRT 外挂字幕 -> {p}")


def main():
    global FILM, OUT, T, X, N_SHOTS, AUTO_DUR, LANG, SUBS, VOICE, TTS_LINES, AMBIENT_VOL
    global FIT_FILM, FPS
    global ALIGN, ALIGN_MODEL, ALIGN_LANG, SPEECH_SPANS
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", default=FILM, help="输入成片（含原生音轨）")
    ap.add_argument("--out", default=OUT, help="输出带解说+字幕的成片")
    ap.add_argument("--t", type=float, default=T, help="单镜时长(秒)，用于时间轴估算")
    ap.add_argument("--xfade", type=float, default=X, help="转场时长(秒)")
    ap.add_argument("--shots", type=int, default=0, help="镜头数（覆盖 storyboard 推断）")
    ap.add_argument("--auto-dur", action="store_true",
                    help="用 ffprobe 探测成片真实时长作为时间轴（推荐，模型无关）")
    ap.add_argument("--fit-film", action="store_true",
                    help="旁白去静音后统一变速+顺排以填满成片时长（用于成片比旁白短的"
                         "情况，如 33.5s 的 H3 版）。隐含 --auto-dur。")
    ap.add_argument("--fps", type=int, default=FPS,
                    help="输出帧率（默认 25；H3 成片为 24fps，须传 24 否则音轨被截短）")
    ap.add_argument("--align", action="store_true",
                    help="用 ASR(faster-whisper) 取真实语音时间戳来收紧字幕窗口，"
                         "并导出同名 .srt 外挂字幕；未装该依赖时自动沿用估算时间轴")
    ap.add_argument("--align-model", default="small",
                    help="Whisper 模型尺寸（默认 small；越大越准越慢）")
    ap.add_argument("--align-lang", default="",
                    help="ASR 语言（如 en / zh）；留空=自动检测")
    ap.add_argument("--lang", choices=["en", "zh"], default=LANG,
                    help="配音语言（默认 en 英文）")
    ap.add_argument("--subs", choices=["bilingual", "en", "zh"], default=SUBS,
                    help="字幕模式（默认 bilingual 中英双语）")
    ap.add_argument("--lines-json", default="",
                    help="某一集的专属台词文件 JSON（narration + narration_en），"
                         "优先级高于 storyboard/内置，用于各集台词互不覆盖")
    ap.add_argument("--series-script", default="",
                    help="系列剧本 JSON（outputs/series_script.json），配合 --ep 取某一集台词；"
                         "三集共用同一连贯剧本，优先级最高")
    ap.add_argument("--ep", type=int, default=0,
                    help="集数（配合 --series-script 使用，如 1/2/3）")
    ap.add_argument("--orig-vol", type=float, default=AMBIENT_VOL,
                    help="原片音轨音量（默认 0.18 作背景垫底）；"
                         "设 0 = 完全去掉原音轨，只留解说")
    a = ap.parse_args()

    # build_and_render 会把 cwd 切到 outputs/nar 找字幕文本，
    # 故 FILM/OUT 必须基于 ROOT 绝对化，否则 ffmpeg 相对路径解析失败。
    FILM = a.film if os.path.isabs(a.film) else os.path.normpath(os.path.join(ROOT, a.film))
    OUT = a.out if os.path.isabs(a.out) else os.path.normpath(os.path.join(ROOT, a.out))
    T = a.t
    X = a.xfade
    if a.shots:
        N_SHOTS = a.shots
    AUTO_DUR = a.auto_dur or a.fit_film   # --fit-film 隐含 --auto-dur
    FIT_FILM = a.fit_film
    ALIGN = a.align
    ALIGN_MODEL = a.align_model
    ALIGN_LANG = a.align_lang.strip() or None
    SPEECH_SPANS = None
    if ALIGN:
        from agent import align
        if align.is_available():
            print(f"[align] 强制对齐开启（model={ALIGN_MODEL}, lang={ALIGN_LANG or 'auto'}）")
        else:
            print("[align] 未安装 faster-whisper，字幕沿用估算时间轴；"
                  "pip install faster-whisper 后重跑即可启用")
            ALIGN = False
    FPS = a.fps
    LANG = a.lang
    SUBS = a.subs
    VOICE = VOICE_EN if LANG == "en" else VOICE_ZH
    TTS_LINES = LINES_EN if LANG == "en" else LINES
    AMBIENT_VOL = a.orig_vol
    # LANG/SUBS 变了，分镜覆盖的分支判定也要跟着重跑一次
    _apply_storyboard()
    # 台词优先级：系列剧本(--series-script + --ep) > 单集台词文件 > storyboard > 内置
    if a.series_script:
        if not a.ep:
            raise SystemExit("[err] --series-script 必须配合 --ep <N>（如 --ep 1）")
        _apply_series_episode(a.series_script, a.ep)
    elif a.lines_json:
        _apply_lines_file(a.lines_json)
    print(f"[cfg] 配音={LANG}({VOICE}) 字幕={SUBS} 原音={AMBIENT_VOL} "
          f"fit={FIT_FILM} fps={FPS} -> {OUT}")

    # FIT 模式需要在算时间轴前先合成旁白以测得自然时长
    write_line_files()
    synth()
    compute_timing()
    build_and_render()


if __name__ == "__main__":
    main()
