"""剪辑：用 ffmpeg 把分镜片段合成最终影片、查看进度等。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):([\d.]+)")


def _ffmpeg() -> str:
    """定位 ffmpeg：优先 PATH，其次 imageio-ffmpeg 自带二进制。

    本机 ffmpeg **不在 PATH**（只有 `imageio-ffmpeg` 里带一份），只查 PATH 会让
    依赖它的功能静默失效（如白模灰模动画合成 → ref_video 永远不可用）。
    """
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:              # noqa: BLE001 - 没装则退回裸命令，由调用方报错
        return "ffmpeg"


class Editor:
    def __init__(self, fps: int = 24):
        self.fps = fps

    def probe_duration(self, path: str) -> float:
        """探测时长（秒）。ffprobe 优先，缺失时退回解析 `ffmpeg -i` 的 stderr。

        本机通常没有 ffprobe，若不退回解析则时长恒为 0.0，
        会让他依赖时长的判断（如 ref_video 的 2s 预检）失效。
        """
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "json", path],
                text=True,
            )
            return float(json.loads(out)["format"]["duration"])
        except Exception:
            pass
        try:
            r = subprocess.run([_ffmpeg(), "-i", path], capture_output=True,
                               text=True, errors="replace", timeout=30)
            m = _DUR_RE.search(r.stderr or "")
            if m:
                return round(int(m.group(1)) * 3600 + int(m.group(2)) * 60
                             + float(m.group(3)), 3)
        except Exception:
            pass
        return 0.0

    def concat(self, clip_paths: list[str], out_path: str) -> str:
        """把多个 mp4 按列表顺序拼接（流式拷贝，不重编码）。"""
        if not clip_paths:
            raise ValueError("没有可合成的片段")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        list_file = out_path + ".list.txt"
        with open(list_file, "w") as f:
            for c in clip_paths:
                f.write(f"file '{os.path.abspath(c)}'\n")
        subprocess.run(
            [_ffmpeg(), "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", out_path],
            check=True,
        )
        os.remove(list_file)
        return out_path

    def finalize(self, film_path: str, out_path: str) -> str:
        """对持续创作产出的单一长片做最终封装（重编码确保兼容性）。"""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        subprocess.run(
            [_ffmpeg(), "-y", "-i", film_path, "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path],
            check=True,
        )
        return out_path
