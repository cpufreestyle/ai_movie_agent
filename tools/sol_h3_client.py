"""Sol-H3-Spark HTTP 客户端（本机 agent 调远程 DGX Spark 视频服务）。

对应 deploy/sol_h3_spark/sol_h3_server.py：本机 agent 通过 HTTP 调用 DGX Spark 上
常驻的 Sol-H3 服务（服务内部 subprocess 调 NVlabs/Sana 的 infer.py 出视频）。

与 ComfyUIClient 同风格：超时抛明确异常 + 有限重试退避。DGX Spark 走内网，
必须绕开 win32 的 127.0.0.1:7897 代理（trust_env=False），否则内网请求会被错误
发往代理而 5xx/超时。

请求用 JSON + base64 传输入（首帧/参考图/参考视频），响应用 base64 回传 mp4，
避免 multipart 解析依赖，兼容各 Python 版本。
"""
from __future__ import annotations

import base64
import os
import time

import requests

# 内网直连，绕开系统代理（同 comfyui_client 的处理）
_SESSION = requests.Session()
_SESSION.trust_env = False

# 轮询/提交遇这些 HTTP 状态视为瞬时故障，退避重试
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_BACKOFF = (1.0, 2.0, 4.0)


class SolH3Error(RuntimeError):
    """Sol-H3 服务交互失败（继承 RuntimeError，兼容既有 except RuntimeError）。"""


def _post(url: str, payload: dict, timeout: int, retries: int = 2):
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = _SESSION.post(url, json=payload, timeout=timeout)
            if r.status_code in _RETRY_STATUS and attempt < retries:
                last = SolH3Error(f"HTTP {r.status_code}")
                time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            if attempt >= retries:
                raise SolH3Error(f"请求 {url} 失败: {e}") from e
            time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
    raise SolH3Error(f"请求 {url} 多次重试仍失败: {last}")


class SolH3Client:
    def __init__(self, api: str, timeout: int = 1800):
        """api: Sol-H3 服务地址，如 http://192.168.x.x:8000。"""
        self.api = (api or "").rstrip("/")
        self.timeout = int(timeout)

    # ---------- 就绪探测 ----------
    def is_ready(self) -> bool:
        if not self.api:
            return False
        for _ in range(2):
            try:
                if _SESSION.get(self.api + "/", timeout=5).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False

    def health(self) -> bool:
        try:
            return _SESSION.get(self.api + "/health", timeout=5).status_code == 200
        except Exception:
            return False

    # ---------- 生成 ----------
    def generate(self, prompt: str, seed: int = 0,
                 first_frame: str | None = None,
                 references: list | None = None,
                 task: str = "auto") -> bytes:
        """提交一次生成，返回 mp4 字节。

        first_frame: 首帧图片路径（FL2VA 条件）。
        references : list[(type, path)]，type ∈ image/video/audio，如
                     [("image", png), ("video", mp4)]（Ref2VA 条件）。
        task       : "auto" 由服务端按输入判定（fl2va/ref2va/t2va）。
        失败抛 SolH3Error。
        """
        payload: dict = {"prompt": prompt, "seed": int(seed), "task": task}
        if first_frame and os.path.exists(first_frame):
            with open(first_frame, "rb") as f:
                payload["first_frame"] = base64.b64encode(f.read()).decode()
        refs = []
        for typ, path in (references or []):
            if path and os.path.exists(path):
                with open(path, "rb") as f:
                    refs.append({"type": typ,
                                 "data": base64.b64encode(f.read()).decode()})
        if refs:
            payload["references"] = refs

        r = _post(self.api + "/generate", payload, timeout=self.timeout)
        try:
            body = r.json()
        except Exception as e:
            raise SolH3Error(f"Sol-H3 响应非 JSON: {e}") from e
        if not body.get("ok"):
            raise SolH3Error("Sol-H3 服务返回错误: " + str(body.get("error", "")))
        vb = body.get("video_b64")
        if not vb:
            raise SolH3Error("Sol-H3 未返回视频数据")
        return base64.b64decode(vb)
