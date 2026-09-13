#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sol-H3-Spark HTTP 服务（运行在 DGX Spark / GB10 上）。

把 NVlabs/Sana (sol-engine) 的 infer.py 封装成常驻 HTTP 服务，供远程 ai_movie_agent
（agent/sol_h3_engine.py + tools/sol_h3_client.py）调用出视频。

设计要点：
  - 初版用 subprocess 调 infer.py（每次请求一次进程）。Sol-H3 模型常驻但 infer.py 自身
    每次会重新加载；后续可升级为 import runtime.pipeline 常驻以复用模型、降延迟。
  - 任务自动判定：first_frame -> fl2va；references -> ref2va；两者皆有 ->
    fl2va + 附参考（best-effort，并记录日志）；都无 -> t2va。
    paths 文件随任务切换：t2va->paths.json / fl2va->paths-fl2va.json / ref2va->paths-ref2va.json
    （均由 prepare.py 在 DGX 上生成）。
  - 输入用 JSON + base64（避免 multipart 解析依赖），输出 mp4 用 base64 回传。
  - output-dir 每次新建（README 要求："The output directory must be new"）。

用法：
  python sol_h3_server.py --repo /path/to/Sol-H3-Spark --port 8000 --python python
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG_LOCK = threading.Lock()


def log(msg: str) -> None:
    with LOG_LOCK:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# 任务 -> paths 文件（须已由 prepare.py 在 DGX 上生成）
PATHS_BY_TASK = {
    "t2va": "paths.json",
    "fl2va": "paths-fl2va.json",
    "ref2va": "paths-ref2va.json",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 屏蔽默认访问日志，用自定义 log
        pass

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("", "/health"):
            self._send(200, {"status": "ok", "service": "sol_h3_spark"})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/generate":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            req = json.loads(raw or b"{}")
        except Exception as e:  # noqa: BLE001
            self._send(400, {"ok": False, "error": f"bad request: {e}"})
            return
        try:
            out = self.server.generate(req)  # type: ignore[attr-defined]
            self._send(200, out)
        except Exception as e:  # noqa: BLE001
            log(f"[generate] 异常: {e}")
            self._send(200, {"ok": False, "error": str(e)})


class Server(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, repo: str, python: str,
                 timeout: int, paths_dir: str | None):
        super().__init__((host, port), Handler)
        self.repo = repo
        self.python = python
        self.timeout = timeout
        self.paths_dir = paths_dir or repo

    # ---------- 工具 ----------
    @staticmethod
    def _save_b64(work: str, name: str, b64: str) -> str:
        with open(os.path.join(work, name), "wb") as f:
            f.write(base64.b64decode(b64))
        return os.path.join(work, name)

    def generate(self, req: dict) -> dict:
        prompt = (req.get("prompt") or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt 为空"}
        seed = int(req.get("seed", 0))
        task = (req.get("task") or "auto")
        ff_b64 = req.get("first_frame")
        refs = req.get("references") or []

        work = os.path.join(self.repo, "srv_work",
                            f"srv_{int(time.time() * 1000)}_{os.getpid()}")
        os.makedirs(work, exist_ok=True)
        try:
            ff_path = self._save_b64(work, "first_frame.png", ff_b64) if ff_b64 else None
            ref_paths: list = []
            for i, r in enumerate(refs):
                rp = self._save_b64(work, f"ref_{i}.bin", r.get("data", ""))
                ref_paths.append((r.get("type", "image"), rp))

            has_ff = ff_path is not None
            has_ref = len(ref_paths) > 0
            if task == "auto":
                task = "fl2va" if has_ff else ("ref2va" if has_ref else "t2va")
            if task not in PATHS_BY_TASK:
                task = "t2va"

            # 构建 JSONL（单行）；case_id 固定 srv，输出路径可预测
            row: dict = {"case_id": "srv", "prompt": prompt, "seed": seed}
            if task != "t2va":
                row["task"] = task
            if has_ff:
                row["first_frame"] = "first_frame.png"
            if has_ref:
                row["references"] = [{"type": t, "path": os.path.basename(p)}
                                     for t, p in ref_paths]
            jsonl = os.path.join(work, "req.jsonl")
            with open(jsonl, "w", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

            paths_file = os.path.join(self.paths_dir, PATHS_BY_TASK[task])
            if not os.path.exists(paths_file):
                return {"ok": False,
                        "error": f"paths 文件不存在: {paths_file}"
                                 f"（请先在 DGX 上跑 prepare.py 生成 paths.json / "
                                 f"paths-fl2va.json / paths-ref2va.json）"}
            out_dir = os.path.join(work, "out")  # 必须新建
            cmd = [self.python, "infer.py",
                   "--paths", paths_file,
                   "--prompts", jsonl,
                   "--output-dir", out_dir,
                   "--seed", str(seed)]
            log(f"[generate] task={task} seed={seed} -> {' '.join(cmd)}")
            t0 = time.time()
            r = subprocess.run(cmd, cwd=self.repo, capture_output=True, text=True,
                               timeout=self.timeout)
            dt = time.time() - t0
            if r.returncode != 0:
                return {"ok": False,
                        "error": f"infer.py 退出码 {r.returncode}\n"
                                 f"STDERR 尾:\n{(r.stderr or '')[-3000:]}"}
            # 查找 mp4：<out_dir>/srv/stage2/*.mp4
            stage2 = os.path.join(out_dir, "srv", "stage2")
            mp4s: list = []
            if os.path.isdir(stage2):
                for root, _, files in os.walk(stage2):
                    for fn in files:
                        if fn.lower().endswith(".mp4"):
                            mp4s.append(os.path.join(root, fn))
            if not mp4s:
                return {"ok": False,
                        "error": f"未产出 mp4（耗时 {dt:.1f}s）。out_dir={out_dir}\n"
                                 f"STDOUT 尾:\n{(r.stdout or '')[-1500:]}"}
            mp4 = max(mp4s, key=os.path.getmtime)
            with open(mp4, "rb") as f:
                data = f.read()
            log(f"[generate] 完成 task={task} 耗时 {dt:.1f}s -> "
                f"{mp4} ({len(data)} bytes)")
            return {"ok": True, "video_b64": base64.b64encode(data).decode()}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"infer.py 超时（>{self.timeout}s）"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"服务内部错误: {e}"}
        # 保留 work 便于排查；可定期清理 srv_work 目录


def main() -> None:
    ap = argparse.ArgumentParser(description="Sol-H3-Spark HTTP 服务")
    ap.add_argument("--repo", default=os.getcwd(),
                    help="Sol-H3-Spark 目录（含 infer.py / paths*.json）")
    ap.add_argument("--python", default=sys.executable,
                    help="运行 infer.py 的解释器（已配好 Sol-H3 环境的 python）")
    ap.add_argument("--host", default="0.0.0.0",
                    help="监听地址（0.0.0.0=内网可达；仅本机用 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--timeout", type=int, default=1800,
                    help="单次 infer.py 超时(秒)")
    ap.add_argument("--paths-dir", default=None,
                    help="paths*.json 所在目录（默认 --repo）")
    args = ap.parse_args()

    srv = Server(args.host, args.port, args.repo, args.python,
                 args.timeout, args.paths_dir)
    log(f"Sol-H3 服务启动: http://{args.host}:{args.port}")
    log(f"  repo={args.repo}")
    log(f"  python={args.python}")
    log(f"  paths 目录={args.paths_dir or args.repo}  (需 {', '.join(PATHS_BY_TASK.values())})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("已停止")


if __name__ == "__main__":
    main()
