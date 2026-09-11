"""本地 TTS 稳定音色：用本地 TTS 引擎合成旁白，固定音色保证系列一致性。

后端可插拔：piper(本地最快) / edge-tts(联网,经代理) / coqui。
无可用后端时优雅返回错误，不崩；音色偏好持久化到 outputs/tts_voice.json。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

VOICE_CONFIG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "outputs", "tts_voice.json")

_BIN = {"piper": "piper", "coqui": "tts"}


def list_backends():
    return ["piper", "edge-tts", "coqui"]


def _bin_for(backend):
    return _BIN.get(backend, backend)


def is_ready(backend="piper"):
    if backend == "edge-tts":
        return shutil.which("edge-tts") is not None
    return shutil.which(_bin_for(backend)) is not None


def _backend_cmd(backend, text, out_wav, voice):
    if backend == "piper":
        return ["piper", "--model", voice or "default",
                "--output_file", out_wav], text.encode("utf-8")
    if backend == "edge-tts":
        return ["edge-tts", "--voice", voice or "zh-CN-XiaoxiaoNeural",
                "--text", text, "--write-media", out_wav], None
    if backend == "coqui":
        return ["tts", "--text", text, "--out_path", out_wav,
                "--speaker_idx", voice or "p225"], None
    raise KeyError(backend)


def tts(text, out_wav, voice=None, backend=None, speed=1.0):
    """合成旁白。backend 省略时按可用顺序自动选(piper > edge-tts > coqui)。"""
    if backend is None:
        for b in list_backends():
            if is_ready(b):
                backend = b
                break
    if backend is None:
        return {"ok": False,
                "error": "无可用 TTS 后端(piper/edge-tts/coqui 均未安装)"}
    try:
        cmd, stdin = _backend_cmd(backend, text, out_wav, voice)
    except KeyError as e:
        return {"ok": False, "error": f"未知后端: {e}"}
    if not is_ready(backend):
        return {"ok": False, "error": f"TTS 后端未就绪: {backend}"}
    os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
    try:
        proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                              timeout=600)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[-400:]
        return {"ok": False, "error": f"{backend} 失败: {msg}"}
    return {"ok": True, "out": out_wav, "backend": backend, "voice": voice}


def save_voice_config(voice, backend, path=VOICE_CONFIG):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"voice": voice, "backend": backend},
                  f, ensure_ascii=False, indent=2)


def load_voice_config(path=VOICE_CONFIG):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
