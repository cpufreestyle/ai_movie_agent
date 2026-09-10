#!/usr/bin/env python
"""量化 ep 各 Mira 镜的「脸一致性」：抽帧 → 检测最大人脸 → 与锚定图算 arcface 余弦相似度。

对比换脸前(before)/换脸后(after)，定位哪些镜漏换、换错人、或换上去仍不像。
判读：>0.6 基本同一人；0.35~0.6 偏弱；<0.35 基本是另一个人；NO_FACE=该帧没检出脸(会漏换)。

用法: python diag_face_consistency.py [--ep 1] [--nframes 3]
"""
from __future__ import annotations
import argparse
import os
import cv2
import numpy as np
import run_series as rs
from insightface.app import FaceAnalysis

ROOT = rs.ROOT
ANCHOR = os.path.join(ROOT, "outputs", "anchor", "mira_anchor.png")


def area(f):
    return (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])


def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--nframes", type=int, default=3, help="每镜抽样帧数")
    a = ap.parse_args()

    rs.set_workspace("mmh3")
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    anchor = cv2.imread(ANCHOR)
    sf = app.get(anchor)
    assert sf, "锚定图未检出人脸"
    src = sf[0]
    src_emb = src.normed_embedding

    data = rs.load_json(rs.SHOTS_FILE)
    prompts = data[f"ep{a.ep}"]
    dirs = {"before": os.path.join(ROOT, "outputs", "series_shots_mmh3"),
            "after": os.path.join(ROOT, "outputs", "series_shots_mmh3_fs")}

    print(f"{'shot':<13}{'before':>9}{'after':>9}   漏检帧(b/a)")
    tot = {"before": [], "after": []}
    for i, p in enumerate(prompts):
        if not rs._is_char_shot(p):
            continue
        key = f"ep{a.ep}_shot{i + 1}"
        row = {}
        for tag, d in dirs.items():
            path = os.path.join(d, f"{key}.mp4")
            if not os.path.exists(path):
                row[tag] = (None, -1)
                continue
            cap = cv2.VideoCapture(path)
            n = int(cap.get(7))
            sims = []
            noface = 0
            for k in range(a.nframes):
                pos = int(n * (k + 1) / (a.nframes + 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ret, f = cap.read()
                if not ret:
                    continue
                faces = app.get(f)
                if not faces:
                    noface += 1
                    continue
                # 与换脸逻辑一致：取「与锚定图最相似」的脸（=Mira），而非最大脸
                best = max(faces, key=lambda x: cos(x.normed_embedding, src_emb))
                sims.append(cos(best.normed_embedding, src_emb))
            cap.release()
            row[tag] = (float(np.mean(sims)) if sims else None, noface)
            tot[tag].extend(sims)
        b, af = row["before"], row["after"]
        bs = f"{b[0]:.3f}" if b[0] is not None else "NA"
        as_ = f"{af[0]:.3f}" if af[0] is not None else "NA"
        print(f"{key:<13}{bs:>9}{as_:>9}   {b[1]}/{af[1]}")
    for tag in ("before", "after"):
        if tot[tag]:
            arr = np.array(tot[tag])
            print(f"[{tag}] 平均 {arr.mean():.3f}  最低 {arr.min():.3f}  (n={len(arr)})")


if __name__ == "__main__":
    main()
