"""剪辑：用 ffmpeg 把分镜片段合成最终影片、查看进度等。"""
from __future__ import annotations

import json
import os
import subprocess


class Editor:
    def __init__(self, fps: int = 24):
        self.fps = fps

    def probe_duration(self, path: str) -> float:
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries",
                 "format=duration", "-of", "json", path],
                text=True,
            )
            return float(json.loads(out)["format"]["duration"])
        except Exception:
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
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", out_path],
            check=True,
        )
        os.remove(list_file)
        return out_path

    def finalize(self, film_path: str, out_path: str) -> str:
        """对持续创作产出的单一长片做最终封装（重编码确保兼容性）。"""
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", film_path, "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path],
            check=True,
        )
        return out_path
