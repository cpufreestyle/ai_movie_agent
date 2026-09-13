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
        self.last_prompt_id = None   # run_workflow 提交的任务 id，供 cancel() 使用

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

    def _post(self, path: str, *, json=None, retries: int = 2,
              timeout: int = 30) -> requests.Response:
        """POST 带有限重试退避（与 _get 同策略），供 cancel 等控制类请求复用。"""
        url = self.api + path
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = _SESSION.post(url, json=json, timeout=timeout)
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
        # 两次探测 + 短退避：避免 ComfyUI 瞬时繁忙/刚启动时的假"未就绪"
        # （否则上层 is_ready() 直接 False，引擎误报"ComfyUI 未就绪"退出）。
        for _ in range(2):
            try:
                if _SESSION.get(self.api + "/", timeout=5).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(0.3)
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

    def _queue_phase(self, prompt_id: str) -> tuple[str, int]:
        """返回 (phase, queue_pos)：phase ∈ {queued, running, done}。

        done 表示已不在队列（可能已完成或已被取消）；真实完成由 wait() 的
        /history 判定。这里仅用于进度回调告知"还在排队 / 执行中"。
        """
        try:
            q = self._get("/queue", timeout=15).json()
        except Exception:
            return ("running", 0)
        running = [x[1] if isinstance(x, (list, tuple)) and len(x) > 1 else x
                   for x in (q.get("queue_running") or [])]
        if prompt_id in running:
            return ("running", 0)
        for i, x in enumerate(q.get("queue_pending") or []):
            pid = x[1] if isinstance(x, (list, tuple)) and len(x) > 1 else x
            if pid == prompt_id:
                return ("queued", i + 1)
        return ("done", 0)

    def cancel(self, prompt_id: str | None = None) -> bool:
        """取消一个任务（best-effort，不抛异常，返回是否成功发起取消）。

        - 还在排队/已不在队列：POST /queue {"delete":[pid]} 移出队列（已完成的删除无害）；
        - 正在执行：POST /interrupt 中断"当前正在执行的"任务（ComfyUI 全局，
          无法只断某个 pid，故仅当本任务确实在跑时才调用，避免误伤其它任务）。
        prompt_id 缺省时用 self.last_prompt_id（run_workflow 提交的任务）。
        """
        pid = prompt_id or self.last_prompt_id
        if not pid:
            return False
        ok = True
        try:
            # 无论排队中还是已完成，尝试从队列删除都安全
            self._post("/queue", json={"delete": [pid]}, retries=1, timeout=15)
        except Exception:
            ok = False
        if self._queue_phase(pid)[0] == "running":
            try:
                self._post("/interrupt", retries=1, timeout=15)
            except Exception:
                ok = False
        return ok

    def wait(self, prompt_id: str, timeout: int | None = None,
             *, raise_on_timeout: bool = False, poll: float = 2.0,
             on_progress=None) -> dict | None:
        """轮询直到该 prompt 产出 / 报错 / 超时。

        返回 history 中该 prompt 的记录；超时按 raise_on_timeout 决定是
        抛 ComfyUITimeout 还是返回 None（默认 None，保持既有调用方行为）。
        执行报错时抛 ComfyUIError，消息里带节点号与异常类型。

        on_progress: 可选回调，签名 (info: dict)，在阶段切换时收到
            {"prompt_id", "phase": "queued"|"running"|"done", "queue_pos": int}，
            用于上层打印进度（如 G 阶段 UI 进度条）。无实时百分比时至少能区分
            "排队中 / 执行中 / 完成"。
        """
        timeout = timeout or self.timeout
        deadline = time.time() + timeout
        last_phase: str | None = None
        while time.time() < deadline:
            h = self.get_history(prompt_id)
            if h.get("status", {}).get("status_str") == "error":
                raise ComfyUIError(_fmt_status_error(h))
            if h.get("outputs"):
                if on_progress is not None:
                    on_progress({"prompt_id": prompt_id, "phase": "done", "queue_pos": 0})
                return h
            if on_progress is not None:
                phase, pos = self._queue_phase(prompt_id)
                if phase != last_phase:
                    on_progress({"prompt_id": prompt_id, "phase": phase, "queue_pos": pos})
                    last_phase = phase
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
        """根据输出元数据下载单个文件到 dest_dir，返回本地路径。

        流式分块写入（1MB/块）：视频文件较大，整段读进内存既占 RAM 又易在
        弱网下因一次性接收超时被截断；分块写更省内存也更稳。
        """
        params = {
            "filename": meta.get("filename") or meta.get("name"),
            "subfolder": meta.get("subfolder", ""),
            "type": meta.get("type", "output"),
        }
        if not params["filename"]:
            return None
        r = self._get("/view", timeout=120, params=params, stream=True)
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, params["filename"])
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
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
                     timeout: int | None = None,
                     on_progress=None) -> list[str]:
        """提交 workflow 并等待下载全部输出，返回本地文件路径列表。

        超时抛 ComfyUITimeout（而不是返回空列表让上层误报"未产出视频"）。
        on_progress: 可选进度回调，透传给 wait()（见其文档）。
        提交的任务 id 记在 self.last_prompt_id，便于事后 cancel()。
        """
        pid = self.queue_prompt(workflow)
        self.last_prompt_id = pid
        if not pid:
            return []
        item = self.wait(pid, timeout, raise_on_timeout=True, on_progress=on_progress)
        if not item:
            return []
        return self.download_outputs(item, dest_dir)
