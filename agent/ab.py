"""A/B 工作台：同镜不同参数并排对比（承接 #10 的 gen_params.json）。

用途：挑种子 / 调参时，把"同一镜的多次生成"或"两套配置的各镜"摆在一起看——
哪次 seed/steps/resolution 更好、哪个 QA 指标更高。

两种用法（CLI: `python cli.py ab ...`）：
  (a) 单目录：列出该 work 目录所有镜的参数 + QA 概览；`--key X` 聚焦一镜，
      列出其变体视频（man 只留最新一版，故变体视频用文件名前缀匹配，建议命名
      含 seed，如 `ep1_shot1_s77.mp4`）并各自打分。
  (b) 双目录：两次生成（不同 seed / 配置）按镜对齐，逐镜并排参数 + QA，标出更优者。

QA 复用 agent.qa 的**风格无关指标**（仅 opencv + numpy，无额外依赖）；
视频读不到时该镜 QA 留空（不报错）。联系表(contact sheet)用 cv2 抽帧拼接。
"""
from __future__ import annotations

import glob
import json
import os
import re

import cv2
import numpy as np

from . import record
from .qa import DEFAULTS, evaluate, score_video

_MANIFEST_NAME = "series_manifest.json"
_PARAM_FIELDS = ("engine", "resolution", "num_frames", "fps", "steps", "lora",
                 "negative", "two_pass", "block_cache", "seed", "attempt",
                 "image", "ref_images", "style_anchor")


# ---------- IO ----------
def load_manifest(work_dir: str) -> dict:
    """读 series_manifest.json（man[key]=视频路径）；文件不存在/损坏返回 {}。"""
    p = os.path.join(work_dir, _MANIFEST_NAME)
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8")) or {}
    except Exception:             # noqa: BLE001
        return {}


def shot_keys(params: dict, manifest: dict) -> list:
    return sorted(set(params) | set(manifest))


def _variant_files(work_dir: str, key: str) -> list:
    """同镜多次生成的变体视频：文件名以 key 开头，后接 '_' 或 '.' 或直接命中。"""
    out = []
    for f in sorted(glob.glob(os.path.join(work_dir, "*.mp4"))):
        stem = os.path.splitext(os.path.basename(f))[0]
        if stem == key or stem.startswith(key + "_") or stem.startswith(key + "."):
            out.append(f)
    return out


def _video_for(work_dir: str, key: str, manifest: dict):
    v = manifest.get(key)
    if v and os.path.exists(v):
        return v
    cand = _variant_files(work_dir, key)
    return cand[0] if cand else None


def _safe_score(path, policy):
    if not path:
        return None
    try:
        sc = score_video(path, policy or {})
        ok, reasons = evaluate(sc, policy or DEFAULTS)
        return {**sc, "ok": ok, "reasons": reasons}
    except Exception as e:            # noqa: BLE001 - 视频读不到也别崩
        return {"path": path, "error": str(e)}


# ---------- 单目录 ----------
def build_rows(work_dir: str, policy: dict | None = None) -> list:
    """该 work 目录每镜一行：参数 + 主视频 QA + 变体列表。"""
    params = record.load(work_dir)
    manifest = load_manifest(work_dir)
    rows = []
    for key in shot_keys(params, manifest):
        p = params.get(key, {})
        v = _video_for(work_dir, key, manifest)
        qa = _safe_score(v, policy)
        variants = []
        for vf in _variant_files(work_dir, key):
            if v and os.path.abspath(vf) == os.path.abspath(v):
                continue
            variants.append({"path": vf, "qa": _safe_score(vf, policy)})
        rows.append({"key": key, "params": p, "video": v,
                     "qa": qa, "variants": variants})
    return rows


# ---------- 双目录（两次生成逐镜对比） ----------
def compare_runs(dir_a: str, dir_b: str, policy: dict | None = None) -> list:
    pa, pb = record.load(dir_a), record.load(dir_b)
    ma, mb = load_manifest(dir_a), load_manifest(dir_b)
    keys = set(shot_keys(pa, ma)) | set(shot_keys(pb, mb))
    rows = []
    for key in sorted(keys):
        a_v = _video_for(dir_a, key, ma)
        b_v = _video_for(dir_b, key, mb)
        rows.append({
            "key": key,
            "a": {"params": pa.get(key, {}), "video": a_v,
                  "qa": _safe_score(a_v, policy)},
            "b": {"params": pb.get(key, {}), "video": b_v,
                  "qa": _safe_score(b_v, policy)},
        })
    return rows


def param_diff(pa: dict, pb: dict) -> list:
    """返回两次生成在某镜上的参数差异 [(field, val_a, val_b), ...]。"""
    out = []
    for f in _PARAM_FIELDS:
        va, vb = pa.get(f), pb.get(f)
        if va != vb:
            out.append((f, va, vb))
    return out


# ---------- 渲染 ----------
def _seed_from_name(stem: str):
    m = re.search(r"_s(\d+)$", stem)
    return int(m.group(1)) if m else None


def _fmt_qa(qa):
    if not qa or qa.get("error"):
        return "-", "-", "-"
    sh = qa.get("sharpness")
    mo = qa.get("motion")
    ok = qa.get("ok")
    return (f"{sh:.1f}" if sh is not None else "-",
            f"{mo:.2f}" if mo is not None else "-",
            "OK" if ok else ("FAIL" if ok is False else "-"))


def render_single(rows: list) -> str:
    head = (f"{'KEY':<14}{'ENGINE':<8}{'RES':<10}{'STEPS':<6}"
            f"{'SEED':<6}{'SHARP':<8}{'MOTION':<8}{'OK':<6}")
    lines = [head, "-" * len(head)]
    for r in rows:
        p = r["params"]
        sh, mo, ok = _fmt_qa(r["qa"])
        lines.append(
            f"{r['key']:<14}{str(p.get('engine', '-')):<8}"
            f"{str(p.get('resolution', '-')):<10}{str(p.get('steps', '-')):<6}"
            f"{str(p.get('seed', '-')):<6}{sh:<8}{mo:<8}{ok:<6}")
    return "\n".join(lines)


def render_markdown(rows: list) -> str:
    lines = ["| KEY | ENGINE | RES | STEPS | SEED | SHARP | MOTION | OK |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        p = r["params"]
        sh, mo, ok = _fmt_qa(r["qa"])
        lines.append(
            f"| {r['key']} | {p.get('engine', '-')} | {p.get('resolution', '-')} | "
            f"{p.get('steps', '-')} | {p.get('seed', '-')} | {sh} | {mo} | {ok} |")
    return "\n".join(lines)


def render_variants(key: str, variants: list) -> str:
    head = (f"{'VARIANT':<22}{'SEED':<6}{'STEPS':<6}"
            f"{'SHARP':<8}{'MOTION':<8}{'OK'}")
    lines = [head, "-" * len(head)]
    for v in variants:
        stem = os.path.splitext(os.path.basename(v["path"]))[0]
        seed = _seed_from_name(stem)
        if seed is None:
            seed = v.get("params", {}).get("seed", "-")
        sh, mo, ok = _fmt_qa(v["qa"])
        lines.append(
            f"{os.path.basename(v['path']):<22}{str(seed):<6}"
            f"{str(v.get('params', {}).get('steps', '-')):<6}{sh:<8}{mo:<8}{ok}")
    return "\n".join(lines)


def _seed_sharp(run: dict) -> str:
    p = run["params"]
    qa = run["qa"]
    seed = p.get("seed", "-")
    sh = qa.get("sharpness") if qa and not qa.get("error") else None
    return f"{seed}/{sh:.1f}" if sh is not None else f"{seed}/-"


def _better(a_qa, b_qa) -> str:
    sa = a_qa.get("sharpness") if a_qa and not a_qa.get("error") else None
    sb = b_qa.get("sharpness") if b_qa and not b_qa.get("error") else None
    if sa is None and sb is None:
        return "-"
    if sa is None:
        return "B"
    if sb is None:
        return "A"
    if abs(sa - sb) < 1e-6:
        return "-"
    return "A" if sa > sb else "B"


def render_runs(rows: list) -> str:
    head = (f"{'KEY':<14}{'A(seed/sharp)':<18}"
            f"{'B(seed/sharp)':<18}{'BETTER'}")
    lines = [head, "-" * len(head)]
    for r in rows:
        a_s = _seed_sharp(r["a"])
        b_s = _seed_sharp(r["b"])
        lines.append(f"{r['key']:<14}{a_s:<18}{b_s:<18}{_better(r['a']['qa'], r['b']['qa'])}")
    return "\n".join(lines)


# ---------- 联系表（需真实视频） ----------
def contact_sheet(paths: list, out_png: str, frame_idx: int = -1):
    """把多个视频的同一帧横向拼成一张图（便于肉眼 A/B）。读不到返回 None。"""
    frames = []
    for p in paths:
        if not p or not os.path.exists(p):
            continue
        cap = cv2.VideoCapture(p)
        if not cap.isOpened():
            continue
        try:
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if frame_idx >= 0:
                idx = frame_idx
            elif total > 0:
                idx = total // 2
            else:
                idx = 0
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, min(idx, total - 1)))
            ok, f = cap.read()
            if ok:
                frames.append(f)
        finally:
            cap.release()
    if not frames:
        return None
    h = max(f.shape[0] for f in frames)
    w = max(f.shape[1] for f in frames)
    grid = np.hstack([cv2.resize(f, (w, h)) for f in frames])
    cv2.imwrite(out_png, grid)
    return out_png
