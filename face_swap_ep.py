#!/usr/bin/env python
"""ep 换脸后处理：把固定的 Mira 参考脸逐帧换进每一镜，锁住跨镜身份一致性。

v2（2026-09-09）修正「覆盖漏洞」：
  - v1 用 prompt 判定（_is_char_shot: 含 'woman'）决定换哪些镜。但 H3 存在 prompt
    依从性偏差——shot5/6/13 的 prompt 是店铺外景/药瓶/草地，画面里却生成了人物。
    这些「画面有 Mira、prompt 没写 woman」的镜被整镜漏掉，成了「还是不一致」的源头
    （诊断显示换脸前最低相似度 -0.040，即完全是另一个人）。
  - 故改为 **画面驱动**：逐帧检测人脸，取「与锚定图最相似」的那张脸，相似度
    >= --sim-thresh 才换（否则视为老板/路人，不换）。
  - 整镜没有 Mira 的（prescan 判定）直接复制原文件，不做重编码，避免无谓画质损失。

用法: python face_swap_ep.py                 # ep1 换脸 + 拼接
      python face_swap_ep.py --ep 2           # ep2
      python face_swap_ep.py --concat-only    # 换脸镜已就绪，只重拼
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import cv2
import numpy as np
import run_series as rs
from insightface.app import FaceAnalysis
from insightface.model_zoo.inswapper import INSwapper

ROOT = rs.ROOT
ANCHOR = os.path.join(ROOT, "outputs", "anchor", "mira_anchor.png")
SW_MODEL = os.path.join(ROOT, "outputs", "faceswap", "inswapper_128.onnx")
SIM_THRESH = 0.10   # 与锚定图的余弦相似度下限，低于此认为是老板/路人，不换
                    # （0.18→0.10：锚定驱动出片后画面里的 Mira 与锚定图 arcface 普遍
                    #  偏低，0.18 会把「有她」的镜误判成「无 Mira」而整镜漏换）
LOCAL_SIM = 0.05   # 局部跟踪换脸的相似度下限：姿态剧变导致 arcface 偏低时仍换 Mira（避免露原脸）


def cos(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def _is_char_shot(p: str) -> bool:
    """该镜 prompt 是否以主角 Mira 为主体（含 woman 且非手部特写）。"""
    pl = p.lower()
    if "woman" not in pl:
        return False
    if "hand" in pl and "close-up" in pl:
        return False
    return True


def prescan(app, path: str, src_emb, thresh: float, sample: int = 12,
            prompt: str = "") -> int:
    """抽样判断该镜画面里是否出现 Mira（避免对无她的镜做无谓重编码）。

    判据取「prompt 明确写了主角」或「画面检出她」的**并集**：只看画面相似度会把
    「有她、但小脸/侧脸导致 arcface 偏低」的镜整镜漏掉（实测 shot3/7/11 因此被判
    「无 Mira」原样保留，露出不一致的原脸）。
    """
    if prompt and _is_char_shot(prompt):
        return 1
    cap = cv2.VideoCapture(path)
    n = int(cap.get(7))
    hit = 0
    for k in range(sample):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * (k + 1) / (sample + 1)))
        ret, f = cap.read()
        if not ret:
            continue
        faces = app.get(f)
        if faces and max(cos(x.normed_embedding, src_emb) for x in faces) >= thresh:
            hit += 1
    cap.release()
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ep", type=int, default=1, help="处理第几集（默认 1）")
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=448)
    ap.add_argument("--frames", type=int, default=0,
                    help="单镜帧数；0=自动探测（ep1 实测 90 帧，按 56 拼会转场错位）")
    ap.add_argument("--fps", type=int, default=0, help="帧率；0=自动探测")
    ap.add_argument("--sim-thresh", type=float, default=SIM_THRESH,
                    help="与锚定图的相似度下限，低于此的脸视为非 Mira，不换")
    ap.add_argument("--concat-only", action="store_true",
                    help="只重拼（复用已换脸镜头）")
    ap.add_argument("--force", action="store_true",
                    help="重换已存在的镜（忽略已生成的 dst）")
    ap.add_argument("--only", default="",
                    help="只重出指定镜号（逗号分隔，如 3,7,11）；其余已换好的镜跳过，"
                         "最后仍按全部镜拼接")
    a = ap.parse_args()
    only = {int(x) for x in a.only.replace("，", ",").split(",")
            if x.strip().isdigit()} or None

    rs.set_workspace("mmh3")
    src0 = os.path.join(rs.WORK, f"ep{a.ep}_shot1.mp4")
    if not a.frames or not a.fps:
        n0 = f0 = 0
        if os.path.exists(src0):
            cap = cv2.VideoCapture(src0)
            n0, f0 = int(cap.get(7)), int(cap.get(5) or 0)
            cap.release()
        a.frames = a.frames or n0 or 56
        a.fps = a.fps or f0 or 24
    print(f"[cfg] 单镜 {a.frames} 帧 @ {a.fps}fps = {a.frames / a.fps:.3f}s  sim>={a.sim_thresh}")
    ENG = rs.build_engine(a.width, a.height, a.frames, a.fps, "mmh3")

    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=0, det_size=(640, 640))
    sw = INSwapper(SW_MODEL)
    anchor = cv2.imread(ANCHOR)
    sf = app.get(anchor)
    assert sf, "锚定图未检出人脸"
    source = sf[0]
    src_emb = source.normed_embedding

    def swap_video(src: str, dst: str) -> int:
        """逐帧换脸 + 跨帧 Mira 跟踪，确保整镜脸持续锁定、不闪原脸。

        策略:
          1) 全局检测取「与锚定图最相似」的脸 = Mira（相似度 >= sim_thresh）即换；
          2) 若全局没命中，用上一帧 Mira 的 bbox 在周围扩大区域做局部检测
             （姿态剧变/侧脸导致全局漏检时，局部仍能抓到），相似度 >= LOCAL_SIM 即换；
          3) 两者都失败（极端漏检）则保留原帧（极少发生）。
        这样 Mira 的脸在整镜/整片持续出现，不再因检测抖动或姿态变化露出原脸。
        """
        cap = cv2.VideoCapture(src)
        fps = cap.get(cv2.CAP_PROP_FPS)
        w, h = int(cap.get(3)), int(cap.get(4))
        tmp = dst + ".v.mp4"
        vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        n = int(cap.get(7))
        i = swapped = tracked = 0
        prev_bbox = None  # 上一帧确认的 Mira 全局 bbox(numpy)
        while True:
            ret, f = cap.read()
            if not ret:
                break
            fout = f.copy()
            faces = app.get(f)
            mira = None
            if faces:
                best = max(faces, key=lambda x: cos(x.normed_embedding, src_emb))
                if cos(best.normed_embedding, src_emb) >= a.sim_thresh:
                    mira = best
            if mira is None and prev_bbox is not None:
                x1, y1, x2, y2 = prev_bbox
                padx = int((x2 - x1) * 0.6); pady = int((y2 - y1) * 0.6)
                ax1 = max(0, int(x1 - padx)); ay1 = max(0, int(y1 - pady))
                ax2 = min(w, int(x2 + padx)); ay2 = min(h, int(y2 + pady))
                crop = f[ay1:ay2, ax1:ax2]
                if crop.size:
                    cf = app.get(crop)
                    if cf:
                        cb = max(cf, key=lambda x: cos(x.normed_embedding, src_emb))
                        if cos(cb.normed_embedding, src_emb) >= LOCAL_SIM:
                            sc = sw.get(crop, cb, source)
                            fout[ay1:ay2, ax1:ax2] = sc
                            prev_bbox = np.array(cb.bbox) + np.array([ax1, ay1, ax1, ay1])
                            swapped += 1; tracked += 1
                            vw.write(fout); i += 1
                            if i % 15 == 0:
                                print(f"  帧 {i}/{n} 已换 {swapped} (track {tracked})")
                            continue
            if mira is not None:
                fout = sw.get(f, mira, source)
                prev_bbox = np.array(mira.bbox)
                swapped += 1
            vw.write(fout)
            i += 1
            if i % 15 == 0:
                print(f"  帧 {i}/{n} 已换 {swapped} (track {tracked})")
        cap.release()
        vw.release()
        if swapped == 0:
            print(f"  [warn] {os.path.basename(src)} 整镜未换脸（首帧即漏检？）")
        subprocess.run([rs.FFMPEG, "-y", "-i", tmp, "-i", src,
                        "-c:v", "copy", "-c:a", "copy",
                        "-map", "0:v:0", "-map", "1:a:0", dst], check=True)
        os.remove(tmp)
        return swapped

    data = rs.load_json(rs.SHOTS_FILE)
    prompts = data[f"ep{a.ep}"]
    fs_dir = os.path.join(ROOT, "outputs", "series_shots_mmh3_fs")
    os.makedirs(fs_dir, exist_ok=True)
    files = []
    for i, p in enumerate(prompts):
        idx = i + 1
        key = f"ep{a.ep}_shot{idx}"
        src = os.path.join(rs.WORK, f"{key}.mp4")
        dst = os.path.join(fs_dir, f"{key}.mp4")
        if not os.path.exists(src):
            print("跳过(无文件)", src)
            continue
        if a.concat_only:
            if not os.path.exists(dst):
                shutil.copy(src, dst)
        elif (os.path.exists(dst) and not a.force
              and not (only and idx in only)):
            print(f"[SKIP] {key} 已换脸（续跑）")
        else:
            # dst 不存在，或 --force：执行换脸/复制（swap_video 内部会覆盖已存在的 dst）
            hit = prescan(app, src, src_emb, a.sim_thresh, prompt=p)
            if hit:
                print(f"[SWAP] {key}  预扫命中 {hit}/12  ({p[:50]}...)")
                n = swap_video(src, dst)
                print(f"  -> 实际换脸 {n} 帧")
            else:
                print(f"[COPY] {key}  无 Mira，原样保留")
                shutil.copy(src, dst)
        files.append(dst)

    film = os.path.join(ROOT, "outputs", f"ep{a.ep}_series_film_mmh3_fs.mp4")
    if not rs.concat_shots(ENG, files, film):
        print("[err] 拼接失败")
        sys.exit(1)
    print("DONE ->", film)


if __name__ == "__main__":
    main()
