"""通用 ComfyUI HTTP 客户端（视频 / 图像生成共用）。

封装 ComfyUI 官方 API：
  - GET  /           探测服务是否就绪
  - POST /prompt     提交 workflow（API Format JSON），返回 prompt_id
  - GET  /history/{id}  轮询执行状态与输出
  - POST /upload/image  上传参考图（I2V 起始帧等）
  - GET  /view       下载输出文件（图片 / 视频）

被 agent/keyframe.py（D 阶段关键帧）与 agent/ltx_engine.py（G 阶段 LTX-2.5 视频）
复用，避免重复实现 HTTP 轮询逻辑。
"""
from __future__ import annotations

import os
import time

import requests

# ComfyUI 只跑在本机 127.0.0.1，必须绕过系统代理（HTTP_PROXY/HTTPS_PROXY 指向 7897），
# 否则 localhost 请求会被错误地发往代理而 5xx/超时。run_ltx25_multishot.py 之所以能跑，
# 是因为它对 requests.Session 设了 trust_env=False；这里统一在底层客户端处理，
# 让所有依赖 ComfyUIClient 的引擎（LTX-2.5 等）都不受代理影响。
_SESSION = requests.Session()
_SESSION.trust_env = False


class ComfyUIClient:
    def __init__(self, api: str, timeout: int = 1800):
        """api: ComfyUI 服务地址，如 http://127.0.0.1:8188。"""
        self.api = (api or "").rstrip("/")
        self.timeout = int(timeout)

    # ---------- 就绪探测 ----------
    def is_ready(self) -> bool:
        if not self.api:
            return False
        try:
            return _SESSION.get(self.api + "/", timeout=5).status_code == 200
        except Exception:
            return False

    # ---------- 提交 / 轮询 ----------
    def queue_prompt(self, workflow: dict) -> str | None:
        """提交 workflow，返回 prompt_id；失败抛异常。"""
        r = _SESSION.post(self.api + "/prompt", json={"prompt": workflow}, timeout=60)
        r.raise_for_status()
        return r.json().get("prompt_id")

    def get_history(self, prompt_id: str) -> dict:
        r = _SESSION.get(self.api + "/history/" + prompt_id, timeout=30)
        r.raise_for_status()
        return r.json().get(prompt_id, {})

    def wait(self, prompt_id: str, timeout: int | None = None) -> dict | None:
        """轮询直到该 prompt 产出 / 报错 / 超时。返回 history 中该 prompt 的记录。"""
        timeout = timeout or self.timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = self.get_history(prompt_id)
            if h.get("status", {}).get("status_str") == "error":
                raise RuntimeError(f"ComfyUI 执行错误: {h.get('status')}")
            if h.get("outputs"):
                return h
            time.sleep(2)
        return None

    # ---------- 上传 / 下载 ----------
    def upload_image(self, path: str) -> dict | None:
        """上传参考图，返回 ComfyUI 图像元数据 {name, subfolder, type}。"""
        with open(path, "rb") as f:
            r = _SESSION.post(self.api + "/upload/image",
                              files={"image": (os.path.basename(path), f, "image/png")},
                              timeout=60)
        r.raise_for_status()
        return r.json()

    def download_file(self, meta: dict, dest_dir: str) -> str | None:
        """根据输出元数据下载单个文件到 dest_dir，返回本地路径。"""
        params = {
            "filename": meta.get("filename") or meta.get("name"),
            "subfolder": meta.get("subfolder", ""),
            "type": meta.get("type", "output"),
        }
        r = _SESSION.get(self.api + "/view", params=params, timeout=120)
        r.raise_for_status()
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, params["filename"])
        with open(path, "wb") as f:
            f.write(r.content)
        return path

    def download_outputs(self, history_item: dict, dest_dir: str) -> list[str]:
        """下载该 prompt 的全部输出（图片 / gif / 视频），返回本地路径列表。"""
        paths: list[str] = []
        for node_out in (history_item.get("outputs") or {}).values():
            if not isinstance(node_out, dict):
                continue
            for kind in ("images", "gifs", "videos"):
                items = node_out.get(kind)
                if items:
                    for it in items:
                        p = self.download_file(it, dest_dir)
                        if p:
                            paths.append(p)
        return paths

    # ---------- 一站式 ----------
    def run_workflow(self, workflow: dict, dest_dir: str,
                     timeout: int | None = None) -> list[str]:
        """提交 workflow 并等待下载全部输出，返回本地文件路径列表。"""
        pid = self.queue_prompt(workflow)
        if not pid:
            return []
        item = self.wait(pid, timeout)
        if not item:
            return []
        return self.download_outputs(item, dest_dir)
