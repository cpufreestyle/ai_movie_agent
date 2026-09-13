"""临时：对比两个视频的「真实人脸检出率」，判断是否仍有真人脸。

RetinaFace(buffalo_l) 只在真实照片上训练，对 2D 动漫脸基本检不出，
故检出率低 = 画面已非写实真人。
"""
import os
import sys

import cv2
import numpy as np
from insightface.app import FaceAnalysis


def rate(path, n=40):
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    hits = 0
    scores = []
    for k in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (k + 1) / (n + 1)))
        ok, f = cap.read()
        if not ok:
            continue
        fs = app.get(f)
        if fs:
            hits += 1
            scores.append(max(x.det_score for x in fs))
    cap.release()
    return hits, n, (float(np.mean(scores)) if scores else 0.0)


app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
app.prepare(ctx_id=0, det_size=(640, 640))

def scan(path, n=40):
    cap = cv2.VideoCapture(path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for k in range(n):
        pos = int(total * (k + 1) / (n + 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ok, f = cap.read()
        if not ok:
            out.append((pos, 0.0))
            continue
        fs = app.get(f)
        out.append((pos, max(x.det_score for x in fs) if fs else 0.0))
    cap.release()
    return out


if os.environ.get("PERFRAME") == "1":
    a, b = sys.argv[1], sys.argv[2]
    sa, sb = scan(a), scan(b)
    print(f"{'frame':>7}{'src':>8}{'cartoon':>9}")
    for (p, x), (_, y) in zip(sa, sb):
        flag = "  <== 仍检出" if y > 0.5 else ""
        print(f"{p:>7}{x:>8.3f}{y:>9.3f}{flag}")
    sys.exit(0)

for p in sys.argv[1:]:
    h, n, s = rate(p)
    print(f"{p}\n  检出真实人脸帧 {h}/{n}  ({h / n * 100:.0f}%)  平均检测置信 {s:.3f}")
