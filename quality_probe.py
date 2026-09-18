#!/usr/bin/env python
"""客观画质度量：VMAF / PSNR / SSIM（ffmpeg 自带，无需额外依赖）。

为什么要有这个：增强做完了「看起来更清楚」不算数 —— 超分可能只是锐化、插帧可能
引入果冻伪影、降噪可能吃掉细节。开源社区（Netflix VMAF、Video2X 的对照评测）
一律用客观指标判定。本脚本让「增强前后」有可复现的数字对比。

用法（ref=基准片，dist=待评片；两者分辨率/时长不一致会自动对齐）：
    python quality_probe.py <ref.mp4> <dist.mp4>                 # VMAF（默认）
    python quality_probe.py ref.mp4 dist.mp4 --mode psnr
    python quality_probe.py ref.mp4 dist.mp4 --mode ssim --json
    python quality_probe.py ref.mp4 dist.mp4 --max-sec 20         # 只比前 20 秒（快）

判读（VMAF 0~100，越高越好）：
    < 80  明显劣化   80~93 可接受   93~97 良好   > 97 接近无损
PSNR：> 40 dB 通常视为质量很好；SSIM：0~1，> 0.98 很好。
"""
from __future__ import annotations

import argparse
import json as _json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from agent import encode as enc  # noqa: E402

MODES = ("vmaf", "psnr", "ssim")


def probe_size(path: str, ff: str) -> tuple[int, int]:
    """读视频宽高；读不到返回 (0, 0)。"""
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path],
                           capture_output=True, text=True, errors="ignore")
        m = re.search(r"(\d{2,5})x(\d{2,5})", r.stderr or "")
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 0, 0


def _filter(mode: str, w: int, h: int, log_name: str = "") -> str:
    """构造滤镜链：两侧统一到 w×h + yuv420p，再做指标比对。"""
    prep = (f"[0:v]scale={w}:{h}:flags=bicubic,format=yuv420p,setpts=PTS-STARTPTS[r];"
            f"[1:v]scale={w}:{h}:flags=bicubic,format=yuv420p,setpts=PTS-STARTPTS[d];")
    if mode == "vmaf":
        return prep + f"[r][d]libvmaf=log_path={log_name}:log_fmt=json"
    if mode == "ssim":
        return prep + "[r][d]ssim"
    return prep + "[r][d]psnr"


_NUM = r"(inf|[0-9]+(?:\.[0-9]+)?)"


def _as_float(tok: str) -> float:
    """'inf' 表示两片**逐像素完全相同**（比对自身或无损副本时会出现）。"""
    return float("inf") if tok == "inf" else float(tok)


def _parse(mode: str, stderr: str, log_path: str = "") -> float | None:
    """从 ffmpeg 输出/日志里抠出分数。"""
    if mode == "vmaf":
        try:
            with open(log_path, encoding="utf-8") as f:
                d = _json.load(f)
            return float(d["pooled_metrics"]["vmaf"]["mean"])
        except Exception:
            return None
    if mode == "ssim":
        m = re.search(rf"All:{_NUM}", stderr or "")
        return _as_float(m.group(1)) if m else None
    m = re.search(rf"average:{_NUM}", stderr or "")
    return _as_float(m.group(1)) if m else None


def _cleanup_log(log_path: str) -> None:
    """删掉 VMAF 临时日志（不留在仓库根目录里堆垃圾）。"""
    if log_path and os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass


def measure(ref: str, dist: str, mode: str = "vmaf", ff: str = "",
            max_sec: float = 0.0, timeout: int = 3600) -> dict:
    """比对 ref 与 dist，返回 {mode, score, size, note}。score 为 None 表示测不出。"""
    ff = ff or enc.ffmpeg_exe()
    out = {"mode": mode, "score": None, "size": None, "note": ""}
    if not ff or not os.path.exists(ref) or not os.path.exists(dist):
        out["note"] = "ffmpeg 缺失或文件不存在"
        return out

    w, h = probe_size(ref, ff)
    dw, dh = probe_size(dist, ff)
    if not w or not h:
        out["note"] = "读不到基准片分辨率"
        return out
    out["size"] = {"ref": [w, h], "dist": [dw, dh], "compared_at": [w, h]}
    if (dw, dh) != (w, h):
        out["note"] = f"分辨率不同，已把待评片缩放到 {w}x{h} 再比（缩放本身会影响分数）"

    dur = (["-t", str(max_sec)] if max_sec > 0 else [])
    # VMAF 日志用**相对文件名 + cwd**，规避 Windows 路径里冒号/反斜杠的滤镜转义坑
    log_path = ""
    if mode == "vmaf":
        fd, log_path = tempfile.mkstemp(prefix="_vmaf_", suffix=".json", dir=HERE)
        os.close(fd)
        log_name = os.path.basename(log_path)
    else:
        log_name = ""

    cmd = [ff, "-y", "-i", ref, "-i", dist, *dur,
           "-lavfi", _filter(mode, w, h, log_name), "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="ignore",
                           timeout=timeout, cwd=HERE)
        stderr = r.stderr or ""
    except subprocess.TimeoutExpired:
        _cleanup_log(log_path)
        out["note"] = "超时"
        return out

    score = _parse(mode, stderr, log_path)
    _cleanup_log(log_path)          # 必须在 _parse 之后：日志是解析的数据源
    if score is None:
        low = (stderr or "").lower()
        if mode == "vmaf" and ("libvmaf" in low or "no such filter" in low):
            out["note"] = "该 ffmpeg 构建不含 libvmaf；改用 --mode psnr 或 --mode ssim"
        else:
            out["note"] = (stderr.strip().splitlines() or ["解析失败"])[-1][:200]
        return out
    out["score"] = score if score == float("inf") else round(score, 3)
    return out


# 判读阈值：从高到低，命中第一个即返回；都没命中用 _FALLBACK
_THRESHOLDS: dict[str, tuple] = {
    "vmaf": ((97, "接近无损"), (93, "良好"), (80, "可接受")),
    "psnr": ((40, "很好"), (35, "可接受")),
    "ssim": ((0.98, "很好"), (0.95, "可接受")),
}
_FALLBACK = {"vmaf": "明显劣化", "psnr": "偏差较大", "ssim": "偏差较大"}


def verdict(mode: str, score: float | None) -> str:
    """把分数翻译成人话。"""
    if score is None:
        return "测不出"
    if score == float("inf"):
        return "完全相同（无损）"
    for threshold, label in _THRESHOLDS.get(mode, ()):
        if score >= threshold:
            return label
    return _FALLBACK.get(mode, "—")


def main() -> int:
    ap = argparse.ArgumentParser(description="客观画质度量（VMAF / PSNR / SSIM）")
    ap.add_argument("ref", help="基准片（如增强前的原片）")
    ap.add_argument("dist", help="待评片（如增强后的片）")
    ap.add_argument("--mode", default="vmaf", choices=MODES)
    ap.add_argument("--max-sec", type=float, default=0.0, help="只比前 N 秒（0=全片）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    res = measure(a.ref, a.dist, a.mode, max_sec=a.max_sec)
    res["verdict"] = verdict(a.mode, res["score"])
    if a.json:
        print(_json.dumps(res, ensure_ascii=False, indent=2))
    else:
        s = res["score"]
        print(f"[{a.mode}] {s if s != float('inf') else 'inf'}  ({res['verdict']})")
        if res["note"]:
            print("  注：" + res["note"])
    return 0 if res["score"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
