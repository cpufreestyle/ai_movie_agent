"""画质增强接口：触发 / 查询 enhance_video.py 后台任务（POST /api/enhance）。

设计要点（与既有 anchor / metadata 视图一致）：
  - 触发非阻塞：subprocess.Popen 起 enhance_video.py，另起 watcher 线程等结束、写状态，
    请求立刻返回；前端轮询 /api/enhance/status 拿进度。增强可能跑几分钟~几十分钟（GPU），
    不能卡 HTTP 请求。
  - 路径安全：dst 默认锁定在 outputs/ 下，防任意路径写。
  - 状态落 outputs/enhance_status.json + 日志 outputs/enhance.log，进程死了也能查上次结果。
"""
from __future__ import annotations

import json as _json
import os
import subprocess
import sys
import threading

from flask import Blueprint, request

from ..state import json_resp

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "outputs")
STATUS_FILE = os.path.join(OUT_DIR, "enhance_status.json")
LOG_FILE = os.path.join(OUT_DIR, "enhance.log")

bp = Blueprint("enhance", __name__)


def _write_status(state, src, dst, profile, rc=None, size=0, error=""):
    data = {"state": state, "src": src, "dst": dst, "profile": profile,
            "rc": rc, "size_mb": round(size / 2 ** 20, 2), "error": error}
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return data


def _latest_film():
    cands = [os.path.join(OUT_DIR, fn) for fn in os.listdir(OUT_DIR)
             if "series_film" in fn and fn.endswith(".mp4")]
    return max(cands, key=os.path.getmtime) if cands else ""


def _wait(proc, src, dst, profile):
    rc = proc.wait()
    ok = rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0
    _write_status("done" if ok else "error", src, dst, profile, rc=rc,
                  size=(os.path.getsize(dst) if ok else 0),
                  error="" if ok else f"enhance 退出码 {rc}")


def _style_anime():
    keys = ("anime", "动漫", "cel-shaded", "二次元", "赛璐璐")
    for rel in ("config.yaml", "config.example.yaml"):
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        try:
            import yaml
            with open(p, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            return any(k in str(((cfg.get("project") or {}).get("style") or "")).lower()
                       for k in keys)
        except Exception:
            continue
    return False


@bp.route("/api/enhance/profiles")
def api_enhance_profiles():
    """前端下拉用：可用档位 + 超分模型推荐。"""
    from agent import encode as enc
    return json_resp({"profiles": enc.profiles(), "sr_models": enc.SR_MODELS,
                      "default": "anime" if _style_anime() else "standard"})


@bp.route("/api/enhance", methods=["POST"])
def api_enhance_run():
    body = request.get_json(silent=True) or {}
    src = (body.get("src") or "").strip() or _latest_film()
    if not src or not os.path.exists(src):
        return json_resp({"error": "找不到源视频（传 src 或先在 outputs 出片）"}, status=400)

    from agent import encode as enc
    profile = (body.get("profile") or "").strip().lower()
    if profile not in enc.profiles():
        profile = "anime" if _style_anime() else "standard"

    dst = (body.get("dst") or "").strip() or (os.path.splitext(src)[0] + "_enhanced.mp4")
    if not dst.endswith(".mp4"):
        dst += ".mp4"
    if not os.path.isabs(dst):
        dst = os.path.join(OUT_DIR, os.path.basename(dst))

    cmd = [sys.executable, os.path.join(ROOT, "enhance_video.py"), src, dst, "--profile", profile]
    if (body.get("kind") or "").strip().lower() in ("anime", "real"):
        cmd += ["--kind", body["kind"]]
    if body.get("no_rife"):
        cmd.append("--no-rife")
    if body.get("no_sr"):
        cmd.append("--no-sr")
    if body.get("denoise"):
        cmd.append("--denoise")

    _write_status("running", src, dst, profile)
    log = open(LOG_FILE, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
    threading.Thread(target=_wait, args=(proc, src, dst, profile), daemon=True).start()
    return json_resp({"state": "running", "src": src, "dst": dst,
                      "profile": profile, "pid": proc.pid})


@bp.route("/api/enhance/status")
def api_enhance_status():
    """返回最近一次增强任务状态 + 日志尾部。"""
    data = None
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                data = _json.load(f)
        except Exception:
            data = None
    tail = ""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
                tail = "\n".join(f.read().splitlines()[-40:])
        except OSError:
            pass
    if data:
        data = dict(data)
        data["log_tail"] = tail
    return json_resp(data or {"state": "idle", "log_tail": tail})
