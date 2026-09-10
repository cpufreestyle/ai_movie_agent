"""通用 ComfyUI HTTP 客户端（视频 / 图像生成共用）。

封装 ComfyUI 官方 API：
  - GET  /               探测服务是否就绪
  - POST /prompt         提交 workflow（API Format JSON），返回 prompt_id
  - GET  /history/{id}   轮询执行状态与输出
  - POST /upload/image   上传参考图（I2V 起始帧等）
  - GET  /view           下载输出文件（图片 / 视频）

被 agent/keyframe.py（D 阶段关键帧）与 agent/ltx_engine.py（G 阶段 LTX-2.5 视频）
复用，避免重复实现 HTTP 轮询逻辑。

排障友好性（本模块的设计重点）：
  - 超时不再"静默变成没产出"：run_workflow() 会抛 ComfyUITimeout，带上
    prompt_id 与已等待秒数。此前超时被上层误报成"未产出视频，请检查节点接线"，
    把排障引向了完全错误的方向。
  - 执行报错会把 ComfyUI 的 node_errors / execution_error 解析成
    "节点 70 (KSampler): RuntimeError: xxx" 这样能直接读的句子，
    不用再自己去 ComfyUI 日志里翻。
  - 轮询 GET 对瞬时 5xx / 连接抖动做有限重试退避；POST（提交任务）不重试，
    避免重复排队。
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

# 轮询 GET 遇这些 HTTP 状态视为瞬时故障，退避重试
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_BACKOFF = (1.0, 2.0, 4.0)


class ComfyUIError(RuntimeError):
    """ComfyUI 交互失败（继承 RuntimeError，保持既有 `except RuntimeError` 兼容）。"""


class ComfyUITimeout(ComfyUIError):
    """等待工作流产出超时。"""


def _fmt_node_errors(node_errors: dict | None) -> str:
    """把 /prompt 返回的 node_errors 拼成 "节点 70: xxx; 节点 3: yyy"。"""
    if not isinstance(node_errors, dict) or not node_errors:
        return ""
    parts: list[str] = []
    for node_id, info in node_errors.items():
        msgs: list[str] = []
        if isinstance(info, dict):
            for err in (info.get("errors") or []):
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("details") or ""
                    typ = err.get("type") or ""
                    msgs.append(f"{typ + ': ' if typ else ''}{msg}".strip(": "))
                elif err:
                    msgs.append(str(err))
            msgs = [m for m in msgs if m] or [str(info.get("message") or info)]
        else:
            msgs = [str(info)]
        parts.append(f"节点 {node_id}: {'; '.join(m for m in msgs if m)}")
    return " | ".join(parts)


def _fmt_status_error(history_item: dict) -> str:
    """从 history 记录的 status.messages 里取出 execution_error 详情。"""
    status = history_item.get("status") or {}
    tail = f" (prompt_id={history_item.get('prompt_id') or '?'})"
    for evt in (status.get("messages") or []):
        # ComfyUI: messages = [[event_name, {data}], ...]
        if not (isinstance(evt, (list, tuple)) and len(evt) == 2):
            continue
        name, data = evt
        if name not in ("execution_error", "execution_interrupted"):
            continue
        if not isinstance(data, dict):
            continue
        node = data.get("node_id") or "?"
        ntype = data.get("node_type") or ""
        exc_t = data.get("exception_type") or ""
        exc_m = data.get("exception_message") or ""
        head = f"节点 {node}{f' ({ntype})' if ntype else ''}"
        detail = f"{exc_t}: {exc_m}".strip(": ") or "执行失败"
        return f"{head}: {detail}{tail}"
    return f"ComfyUI 执行错误: {status}{tail}"


class ComfyUIClient:
    def __init__(self, api: str, timeout: int = 1800):
        """api: ComfyUI 服务地址，如 http://127.0.0.1:8188。"""
        self.api = (api or "").rstrip("/")
        self.timeout = int(timeout)

    # ---------- HTTP 基础（GET 带有限重试退避）----------
    def _get(self, path: str, *, timeout: int, retries: int = 2, **kw) -> requests.Response:
        url = self.api + path
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = _SESSION.get(url, timeout=timeout, **kw)
                if r.status_code in _RETRY_STATUS and attempt < retries:
                    last = ComfyUIError(f"HTTP {r.status_code}")
                else:
                    r.raise_for_status()
                    return r
            except requests.RequestException as e:
                last = e
                if attempt >= retries:
                    raise ComfyUIError(f"请求 {url} 失败: {e}") from e
            time.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
        raise ComfyUIError(f"请求 {url} 多次重试仍失败: {last}")

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
        """提交 workflow，返回 prompt_id。

        POST 不重试（避免同一个任务被重复排队）。提交被拒时解析 ComfyUI 返回的
        error / node_errors，抛出人能直接读的异常。
        """
        r = _SESSION.post(self.api + "/prompt", json={"prompt": workflow}, timeout=60)
        if r.status_code >= 400:
            detail = ""
            try:
                body = r.json() or {}
            except Exception:
                body = {}
            node_err = _fmt_node_errors(body.get("node_errors"))
            err = body.get("error") or {}
            msg = err.get("message") or err.get("details") or ""
            detail = " | ".join(x for x in (msg, node_err) if x)
            raise ComfyUIError(
                f"提交工作流被 ComfyUI 拒绝（HTTP {r.status_code}）: {detail or r.text[:500]}")
        return r.json().get("prompt_id")

    def get_history(self, prompt_id: str) -> dict:
        r = self._get("/history/" + prompt_id, timeout=30)
        return r.json().get(prompt_id, {})

    def wait(self, prompt_id: str, timeout: int | None = None,
             *, raise_on_timeout: bool = False, poll: float = 2.0) -> dict | None:
        """轮询直到该 prompt 产出 / 报错 / 超时。

        返回 history 中该 prompt 的记录；超时按 raise_on_timeout 决定是
        抛 ComfyUITimeout 还是返回 None（默认 None，保持既有调用方行为）。
        执行报错时抛 ComfyUIError，消息里带节点号与异常类型。
        """
        timeout = timeout or self.timeout
        deadline = time.time() + timeout
        while time.time() < deadline:
            h = self.get_history(prompt_id)
            if h.get("status", {}).get("status_str") == "error":
                raise ComfyUIError(_fmt_status_error(h))
            if h.get("outputs"):
                return h
            time.sleep(poll)
        if raise_on_timeout:
            raise ComfyUITimeout(
                f"等待 ComfyUI 产出超时（{timeout}s，prompt_id={prompt_id}）。"
                f"可提高 config 的 timeout，或到 ComfyUI 控制台确认是否仍在执行/已卡死。")
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
        r = self._get("/view", timeout=120, params=params)
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
        """提交 workflow 并等待下载全部输出，返回本地文件路径列表。

        超时抛 ComfyUITimeout（而不是返回空列表让上层误报"未产出视频"）。
        """
        pid = self.queue_prompt(workflow)
        if not pid:
            return []
        item = self.wait(pid, timeout, raise_on_timeout=True)
        if not item:
            return []
        return self.download_outputs(item, dest_dir)
