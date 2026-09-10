"""逐镜自动质检（QA）：出片后打分，不达标自动换 seed 重 roll。

为什么需要：连续剧最大的观感杀手不是"不够美"，而是**个别镜头崩坏**
（全黑 / 卡成静帧 / 糊成一团），以及**人物身份漂移**。人工盯 54 镜不现实，
故把质检接进出片循环：每镜生成后打分 → 不达标就重出（限次）→ 结果落
`qa_report.json`，既自动修，也留下可用于调阈值的分布数据。

指标分两类：

1. **风格无关**（默认启用；只用 opencv + numpy，无额外依赖）
   - `sharpness`  拉普拉斯方差，偏低 = 糊 / 涂抹
   - `motion`     相邻采样帧的平均绝对差，过低 = 静帧 / 卡死，过高 = 抖动闪烁
   - `brightness` 灰度均值，用于抓全黑 / 全白

2. **人脸相关**（**需显式开启** `qa.face_check: true`）
   - `face_ratio`     检出人脸的采样帧占比
   - `identity_sim`   与锚定图的 arcface 余弦相似度（>0.6 同一人，<0.35 基本换人）
   注意：opencv 自带 Haar 只认真实人脸，**对动漫脸基本失效**；本项目默认已是动漫风格，
   故人脸类检查默认关闭（否则会大量误判、白白重 roll GPU）。装了 `insightface`
   并用真实人脸锚定图时再开启。

阈值默认取"只抓明确崩坏"的保守值（如 sharpness<5、静帧判定、全黑/全白），
避免误判把 GPU 时间浪费在无意义的重 roll 上。想知道阈值该设多少，先看报告分布：

    python -m agent.qa outputs/series_shots_mmh3/ep1_shot1.mp4
    python -m agent.qa --dir outputs/series_shots_mmh3 --json   # 全目录分布，用于校准
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

try:                                  # 允许 `python -m agent.qa` 与直接运行两种方式
    from .llmutil import log
except ImportError:                   # pragma: no cover - 仅直接运行脚本时触发
    def log(msg: str) -> None:
        print(msg)


# 默认策略：保守到只抓"明确崩坏"，把误判（白重 roll 一次要几分钟 GPU）压到最低
DEFAULTS: dict = {
    "enabled": True,
    "sample_frames": 9,
    "max_rerolls": 1,
    # 风格无关
    "min_sharpness": 5.0,      # 0 = 不检查
    "min_motion": 0.5,         # 0 = 不检查；正常视频的帧间差远高于此
    "max_motion": 0.0,         # 0 = 不检查（抖动阈值难普适，默认只报告不判死）
    "min_brightness": 8.0,     # 0 = 不检查
    "max_brightness": 247.0,
    # 人脸（需 insightface + 锚定图，且对动漫脸不可靠 → 默认关）
    "face_check": False,
    "min_face_ratio": 0.34,
    "min_identity": 0.35,
}

# 报告里按该顺序打印的指标
_METRIC_KEYS = ("sharpness", "motion", "brightness", "face_ratio", "identity_sim")

_FACE_APP = None
_FACE_TRIED = False
_ANCHOR_CACHE: dict = {}


# ---------- 帧采样 / 指标 ----------
def sample_frames(path: str, n: int) -> list:
    """在整段视频里均匀抽 n 帧（BGR）。读不到则返回已读到的部分。"""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames = []
        if total > 0:
            for k in range(n):
                pos = int(total * (k + 1) / (n + 1))
                cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
                ok, f = cap.read()
                if ok:
                    frames.append(f)
        else:                          # 拿不到总帧数就顺序读
            while len(frames) < n:
                ok, f = cap.read()
                if not ok:
                    break
                frames.append(f)
        return frames
    finally:
        cap.release()


def metrics_from_frames(frames: list) -> dict:
    """把采样帧折算成风格无关指标。"""
    if not frames:
        return {"frames": 0, "sharpness": 0.0, "motion": 0.0, "brightness": 0.0}
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in frames]
    sharp = [float(cv2.Laplacian(g, cv2.CV_64F).var()) for g in grays]
    # 缩到 64x36 再比帧间差：既快又只看整体变化，不会被压缩噪声带偏
    small = [cv2.resize(g, (64, 36)).astype("float32") for g in grays]
    diffs = [float(np.mean(np.abs(small[i + 1] - small[i])))
             for i in range(len(small) - 1)]
    return {
        "frames": len(frames),
        "sharpness": round(float(np.mean(sharp)), 3),
        "motion": round(float(np.mean(diffs)), 3) if diffs else 0.0,
        "brightness": round(float(np.mean([float(g.mean()) for g in grays])), 3),
    }


# ---------- 人脸（可选，依赖 insightface） ----------
def _face_app():
    """惰性初始化 insightface；不可用返回 None（整套人脸检查自动降级）。"""
    global _FACE_APP, _FACE_TRIED
    if _FACE_TRIED:
        return _FACE_APP
    _FACE_TRIED = True
    try:
        from insightface.app import FaceAnalysis
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=0, det_size=(640, 640))
        _FACE_APP = app
    except Exception as e:            # noqa: BLE001 - 缺依赖/模型都当作不可用
        _FACE_APP = None
        log(f"  [qa] 身份检测不可用（insightface: {e}），本次跳过人脸类指标")
    return _FACE_APP


def _cos(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def _anchor_embedding(app, anchor: str):
    if not anchor or not os.path.exists(anchor):
        return None
    if anchor in _ANCHOR_CACHE:
        return _ANCHOR_CACHE[anchor]
    emb = None
    img = cv2.imread(anchor)
    if img is not None:
        faces = app.get(img)
        if faces:
            emb = faces[0].normed_embedding
    _ANCHOR_CACHE[anchor] = emb
    return emb


def _face_metrics(frames: list, anchor: str) -> dict:
    app = _face_app()
    if app is None:
        return {}
    ref = _anchor_embedding(app, anchor)
    hits, sims = 0, []
    for f in frames:
        faces = app.get(f)
        if faces:
            hits += 1
            if ref is not None:
                best = max(faces, key=lambda x: _cos(x.normed_embedding, ref))
                sims.append(_cos(best.normed_embedding, ref))
    out: dict = {}
    if frames:
        out["face_ratio"] = round(hits / len(frames), 3)
    if sims:
        out["identity_sim"] = round(float(np.mean(sims)), 3)
    return out


# ---------- 打分 / 判定 ----------
def load_policy(config: dict | None) -> dict:
    """从 config.qa 读策略并补默认值。"""
    pol = dict(DEFAULTS)
    pol.update((config or {}).get("qa") or {})
    return pol


def score_video(path: str, policy: dict | None = None, *,
                is_char_shot: bool = False, anchor: str = "") -> dict:
    """给单个镜头视频打分，返回指标字典（含 path）。"""
    pol = {**DEFAULTS, **(policy or {})}
    frames = sample_frames(path, int(pol.get("sample_frames") or 9))
    out = {"path": path, **metrics_from_frames(frames)}
    if pol.get("face_check") and is_char_shot and frames:
        out.update(_face_metrics(frames, anchor))
    return out


def evaluate(score: dict, policy: dict | None = None,
             is_char_shot: bool = False) -> tuple:
    """按策略判定是否达标，返回 (ok, reasons)。"""
    pol = {**DEFAULTS, **(policy or {})}
    reasons: list = []

    def num(key, default=0.0):
        try:
            return float(score.get(key) if score.get(key) is not None else default)
        except (TypeError, ValueError):
            return default

    sh, mo, br = num("sharpness"), num("motion"), num("brightness")
    if pol.get("min_sharpness") and sh < float(pol["min_sharpness"]):
        reasons.append(f"画面偏糊(sharpness={sh:.1f}<{pol['min_sharpness']})")
    if pol.get("min_motion") and mo < float(pol["min_motion"]):
        reasons.append(f"近乎静帧/卡死(motion={mo:.2f}<{pol['min_motion']})")
    if pol.get("max_motion") and mo > float(pol["max_motion"]):
        reasons.append(f"帧间抖动过大(motion={mo:.1f}>{pol['max_motion']})")
    if pol.get("min_brightness") and br < float(pol["min_brightness"]):
        reasons.append(f"画面过暗/全黑(brightness={br:.1f})")
    if pol.get("max_brightness") and br > float(pol["max_brightness"]):
        reasons.append(f"画面过曝/全白(brightness={br:.1f})")
    if pol.get("face_check") and is_char_shot:
        if score.get("face_ratio") is not None and \
                float(score["face_ratio"]) < float(pol.get("min_face_ratio") or 0):
            reasons.append(f"人脸检出率低(face_ratio={score['face_ratio']})")
        if score.get("identity_sim") is not None and \
                float(score["identity_sim"]) < float(pol.get("min_identity") or 0):
            reasons.append(f"身份相似度低(identity_sim={score['identity_sim']})")
    return (not reasons), reasons


def summarize(scores: list) -> dict:
    """给一批打分结果算各指标的 min/中位/max/均值，用于校准阈值。"""
    out: dict = {"count": len(scores)}
    for key in _METRIC_KEYS:
        vals = [float(s[key]) for s in scores
                if isinstance(s, dict) and s.get(key) is not None]
        if vals:
            arr = np.array(vals, dtype="float64")
            out[key] = {"min": round(float(arr.min()), 3),
                        "median": round(float(np.median(arr)), 3),
                        "max": round(float(arr.max()), 3),
                        "mean": round(float(arr.mean()), 3)}
    return out


# ---------- CLI ----------
def _fmt(score: dict) -> str:
    parts = [f"{k}={score[k]}" for k in _METRIC_KEYS if score.get(k) is not None]
    return "  ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="逐镜质检：给镜头视频打分（可选整目录分布）")
    ap.add_argument("videos", nargs="*", help="视频路径（可多个）")
    ap.add_argument("--dir", default="", help="扫描该目录下所有 mp4")
    ap.add_argument("--anchor", default="", help="锚定图（配合 --char 出身份相似度，需 insightface）")
    ap.add_argument("--char", action="store_true", help="把输入当作含主角的镜头（启用人脸类指标）")
    ap.add_argument("--face-check", action="store_true", help="强制启用人脸类指标")
    ap.add_argument("--json", action="store_true", help="输出 JSON（便于脚本处理）")
    a = ap.parse_args()

    paths = list(a.videos)
    if a.dir:
        paths += sorted(glob.glob(os.path.join(a.dir, "*.mp4")))
    if not paths:
        ap.error("请给出视频路径，或用 --dir 指定目录")

    policy = dict(DEFAULTS)
    if a.face_check:
        policy["face_check"] = True
    scores = []
    for p in paths:
        sc = score_video(p, policy, is_char_shot=a.char, anchor=a.anchor)
        ok, reasons = evaluate(sc, policy, is_char_shot=a.char)
        sc["ok"] = ok
        sc["reasons"] = reasons
        scores.append(sc)
        if not a.json:
            flag = "OK  " if ok else "FAIL"
            print(f"[{flag}] {os.path.basename(p)}  {_fmt(sc)}")
            for r in reasons:
                print(f"        - {r}")

    if a.json:
        payload = {"scores": scores, "summary": summarize(scores)}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        print("\n== 汇总（用于校准阈值）==")
        print(json.dumps(summarize(scores), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
