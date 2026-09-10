"""旁白 / 字幕强制对齐（forced alignment）。

现状问题：`make_narration.py` 拿 **wav 文件时长**当作"这段话念了多久"，
但 TTS 产出的 wav **首尾带静音**，于是：
  · 字幕比真实语音晚开始，也在语音说完后**继续挂着一截空白**才消失；
  · 段与段之间看起来"对不上嘴"，尤其短句非常明显。

本模块用 faster-whisper 对每段旁白做 ASR，拿到**真实的首/尾语音时间戳**，
据此生成贴合语音的字幕区间（并可导出 SRT 外挂字幕）。

faster-whisper 是**可选依赖**（`pip install faster-whisper`，首次会下载模型），
没装时 `is_available()` 返回 False、`speech_spans()` 返回 None，
调用方沿用原来的估算时间轴 —— 与项目其它能力的降级策略保持一致。

补偿 atempo：`make_narration` 对超长旁白会做 `atempo=t` 变速，音频里原本
第 x 秒的内容会出现在 x/t 秒，故语音时间戳要 **除以 tempo** 才是成片坐标。
"""
from __future__ import annotations

import os

try:                                  # 允许 `python -m agent.align` 与直接运行两种方式
    from .llmutil import log
except ImportError:                   # pragma: no cover - 仅直接运行脚本时触发
    def log(msg: str) -> None:
        print(msg)


_MODELS: dict = {}


def is_available() -> bool:
    """faster-whisper 是否可用（没装就走估算时间轴，不报错）。"""
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:          # noqa: BLE001
        return False


def get_model(size: str = "small", device: str = "cpu",
              compute_type: str = "int8"):
    """按参数缓存模型实例（同一进程里多段旁白只加载一次）。"""
    from faster_whisper import WhisperModel
    key = (size, device, compute_type)
    if key not in _MODELS:
        _MODELS[key] = WhisperModel(size, device=device,
                                    compute_type=compute_type)
    return _MODELS[key]


def speech_span(path: str, *, model_size: str = "small",
                language: str | None = None, device: str = "cpu",
                compute_type: str = "int8"):
    """返回该音频真实语音的 {"start","end","duration","text"}；无语音 / 不可用 → None。"""
    if not is_available() or not (path and os.path.exists(path)):
        return None
    try:
        model = get_model(model_size, device, compute_type)
        segments, _info = model.transcribe(path, language=language,
                                           word_timestamps=True, vad_filter=True)
        start = end = None
        texts: list = []
        for seg in segments:
            words = getattr(seg, "words", None) or []
            wstarts = [w.start for w in words if getattr(w, "start", None) is not None]
            wends = [w.end for w in words if getattr(w, "end", None) is not None]
            # 优先用词级时间戳（比段级准），没有就退回段级
            seg_s = min(wstarts) if wstarts else getattr(seg, "start", None)
            seg_e = max(wends) if wends else getattr(seg, "end", None)
            if seg_s is None or seg_e is None:
                continue
            start = seg_s if start is None else min(start, seg_s)
            end = seg_e if end is None else max(end, seg_e)
            texts.append((getattr(seg, "text", "") or "").strip())
        if start is None or end is None or end <= start:
            return None
        return {"start": float(start), "end": float(end),
                "duration": float(end - start),
                "text": " ".join(t for t in texts if t).strip()}
    except Exception as e:             # noqa: BLE001 - ASR 失败只降级，不能中断出片
        log(f"  [align] ASR 失败（{os.path.basename(path)}）: {e}")
        return None


def speech_spans(paths: list, **kw):
    """批量取语音时间戳；不可用返回 None（调用方据此回退估算）。"""
    if not is_available():
        return None
    return [speech_span(p, **kw) for p in paths]


# ---------- 字幕区间 ----------
def cue(s: float, e: float, *, pad_end: float = 0.10,
        min_dur: float = 0.6) -> tuple:
    """收紧成字幕区间：尾部留 pad_end，至少显示 min_dur 秒。"""
    s = max(0.0, float(s))
    e = max(s, float(e)) + pad_end
    if e - s < min_dur:
        e = s + min_dur
    return (round(s, 3), round(e, 3))


def cues_for_lines(spans, starts: list, *, tempo=None, delay: float = 0.0,
                   total: float | None = None, pad_end: float = 0.10,
                   min_dur: float = 0.6, gap: float = 0.05) -> list:
    """按真实语音时间戳生成每段字幕的 (start, end)。

    spans  每段语音的 {"start","end"}，None 表示该段回退到整段槽位
    starts 每段在成片里的起点（SEG_STARTS）
    tempo  各段的 atempo 倍速（>1 表示被压缩，语音时间戳需除以它）
    gap    与下一段之间保留的间隔，避免字幕叠在一起
    """
    n = len(starts)
    out: list = []
    for i in range(n):
        s0 = float(starts[i])
        sp = spans[i] if (spans and i < len(spans)) else None
        t = 1.0
        if tempo and i < len(tempo):
            try:
                t = float(tempo[i]) or 1.0
            except (TypeError, ValueError):
                t = 1.0
        if t <= 0:
            t = 1.0
        if sp:
            s = s0 + delay + float(sp["start"]) / t
            e = s0 + delay + float(sp["end"]) / t
        else:
            # 回退：占满这一段的槽位
            if i + 1 < n:
                slot = float(starts[i + 1]) - s0
            elif total is not None:
                slot = float(total) - s0
            else:
                slot = min_dur + gap
            s = s0 + delay
            e = s + max(slot - gap, min_dur)
        c = cue(s, e, pad_end=pad_end, min_dur=min_dur)
        # 不侵入下一段 / 不超出成片
        if i + 1 < n:
            c = (c[0], min(c[1], float(starts[i + 1]) - gap))
        elif total is not None:
            c = (c[0], min(c[1], float(total) - 0.05))
        out.append((round(c[0], 3), round(c[1], 3)))
    return out


def _fmt_ts(sec: float) -> str:
    """秒 → SRT 时间戳 00:00:12,345"""
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def write_srt(cues: list, texts: list, path: str) -> str | None:
    """写 SRT 外挂字幕（便于外部播放器 / 二次剪辑）；返回路径或 None。"""
    if not cues:
        return None
    try:
        with open(path, "w", encoding="utf-8") as f:
            for i, (s, e) in enumerate(cues, 1):
                t = (texts[i - 1] if i - 1 < len(texts) else "")
                f.write(f"{i}\n{_fmt_ts(s)} --> {_fmt_ts(e)}\n"
                        f"{(t or '').strip()}\n\n")
        return path
    except Exception as e:             # noqa: BLE001
        log(f"  [align] 写 SRT 失败: {e}")
        return None


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="查看音频的真实语音时间戳（强制对齐用）")
    ap.add_argument("audios", nargs="+")
    ap.add_argument("--model", default="small")
    ap.add_argument("--lang", default=None)
    a = ap.parse_args()
    if not is_available():
        print("faster-whisper 不可用：pip install faster-whisper")
        return 1
    for i, p in enumerate(speech_spans(a.audios, model_size=a.model,
                                        language=a.lang) or [], 1):
        if p:
            print(f"{i:02d} {os.path.basename(a.audios[i-1])}: "
                  f"{p['start']:.3f} -> {p['end']:.3f} ({p['duration']:.3f}s)  {p['text'][:60]}")
        else:
            print(f"{i:02d} {os.path.basename(a.audios[i-1])}: 未检出语音")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
