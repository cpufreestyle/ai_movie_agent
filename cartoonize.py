"""后期去真人脸：只对检测到的人脸区域做风格化，背景与画面其余部分保持原样。

用法:
  python cartoonize.py <src> <dst> [--full]      # --full = 全片整体卡通化（旧模式，画质损失大）
                                                 # 默认 = 仅人脸区域（画质无损）

原理:
  逐帧跑 insightface 人脸检测 → 以人脸为中心生成羽化椭圆掩码
  → 只在掩码内做风格化（cv2.stylization 油画/赛璐璐感，缺则退回 kmeans 平涂）
  → 与原帧按掩码加权混合。无脸帧完全不处理，背景零损失。
音频原样保留。
"""
import argparse
import subprocess

import cv2
import numpy as np
import imageio_ffmpeg


def flat_color(img, k=6):
    """kmeans 平涂 + 粗线稿（保底方案）。"""
    for _ in range(4):
        img = cv2.bilateralFilter(img, 9, 160, 160)
    z = img.reshape((-1, 3)).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, lab, cent = cv2.kmeans(z, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    quant = cent[lab.flatten()].reshape(img.shape).astype(np.uint8)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.adaptiveThreshold(cv2.medianBlur(gray, 5), 255,
                                  cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY, 13, 9)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    return cv2.bitwise_and(quant, cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR))


def paint(img, mode="flat", sigma_s=150, sigma_r=0.12, k=8):
    """区域风格化。flat = kmeans 平涂+线稿（去写实最彻底）；stylize = 油画感（更自然但偏弱）。"""
    if mode == "flat":
        return flat_color(img, k=k)
    try:
        return cv2.stylization(img, sigma_s=sigma_s, sigma_r=sigma_r)
    except AttributeError:
        return flat_color(img)


def face_mask(shape, faces):
    """实心矩形掩码：完全盖住人脸+发型，核心区必须为 1.0（否则残留原脸仍会被检出）。"""
    m = np.zeros(shape[:2], np.float32)
    for f in faces:
        x1, y1, x2, y2 = f.bbox.astype(int)
        w, h = x2 - x1, y2 - y1
        ex1 = max(int(x1 - 0.45 * w), 0)
        ex2 = min(int(x2 + 0.45 * w), shape[1] - 1)
        ey1 = max(int(y1 - 0.70 * h), 0)          # 上扩盖住头发
        ey2 = min(int(y2 + 0.55 * h), shape[0] - 1)
        cv2.rectangle(m, (ex1, ey1), (ex2, ey2), 1.0, -1)
    m = cv2.GaussianBlur(m, (31, 31), 0)          # 小羽化，核心仍保持 1.0
    return np.clip(m, 0, 1)[..., None]


def pixelate(img, bs=18):
    """马赛克：缩小再最近邻放大，彻底破坏五官高频结构 → RetinaFace 必失败。
    经验证对清晰写实大脸也能打到 0 检出（平涂反而让肤色均匀更易被检出）。"""
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // bs), max(1, h // bs)),
                       interpolation=cv2.INTER_LINEAR)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)


def face_paint(roi, mode="pixelate", k=6, blur=7, pixel=18):
    """人脸区域专用处理。pixelate=马赛克(推荐, 背景锐利且必杀检测); flat=平涂(对清晰大脸无效)。"""
    if mode == "pixelate":
        return pixelate(roi, pixel)
    if mode == "stylize":
        return cv2.stylization(roi, sigma_s=150, sigma_r=0.07)
    p = flat_color(roi, k=k)
    p = cv2.GaussianBlur(p, (blur, blur), 0)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--full", action="store_true", help="全片整体卡通化（画质损失大）")
    ap.add_argument("--mode", choices=["pixelate", "flat", "stylize"], default="pixelate",
                    help="人脸区域处理：pixelate=马赛克(推荐, 背景锐利且必杀检测, 默认)；"
                         "flat=平涂(对清晰大脸无效)；stylize=油画感")
    ap.add_argument("--det-thresh", type=float, default=0.30)
    ap.add_argument("--face-k", type=int, default=6, help="flat 模式平涂色块数")
    ap.add_argument("--face-blur", type=int, default=7, help="flat 模式高斯模糊核(奇数)")
    ap.add_argument("--face-pixel", type=int, default=18, help="pixelate 模式马赛克块大小(px)")
    a = ap.parse_args()

    from insightface.app import FaceAnalysis
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640), det_thresh=a.det_thresh)

    cap = cv2.VideoCapture(a.src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    tmp = a.dst + ".silent.mp4"
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    n = 0
    touched = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if a.full:
            out = flat_color(frame)
            touched += 1
        else:
            faces = app.get(frame)
            if faces:
                m = face_mask(frame.shape, faces)
                bs = [f.bbox for f in faces]
                # 只在人脸包围盒(扩边)内做风格化，避免整帧浪费算力
                x1 = max(int(min(b[0] for b in bs)) - 40, 0)
                y1 = max(int(min(b[1] for b in bs)) - 40, 0)
                x2 = min(int(max(b[2] for b in bs)) + 40, w)
                y2 = min(int(max(b[3] for b in bs)) + 40, h)
                roi = frame[y1:y2, x1:x2]
                mr = m[y1:y2, x1:x2]
                out = frame.copy()
                fp = face_paint(roi, mode=a.mode, k=a.face_k,
                                blur=a.face_blur, pixel=a.face_pixel)
                out[y1:y2, x1:x2] = (fp * mr + roi * (1 - mr)).astype(np.uint8)
                touched += 1
            else:
                out = frame                       # 无脸帧：原样直通，零画质损失
        vw.write(out)
        n += 1
        if n % 100 == 0:
            print(f"[info] {n} 帧, 已处理 {touched}", flush=True)
    cap.release()
    vw.release()
    print(f"[info] {n} 帧, 其中处理 {touched} 帧")

    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-i", tmp, "-i", a.src, "-map", "0:v:0",
                    "-map", "1:a:0?", "-c:v", "libx264", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", a.dst],
                   capture_output=True)
    print("done", a.dst)


if __name__ == "__main__":
    main()
