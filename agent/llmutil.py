"""共享 LLM 工具：统一封装 Ollama 原生 /api/chat 并对话。

为什么不用 OpenAI 兼容客户端：
  - 本机 Ollama 在 127.0.0.1:11434，openai 客户端默认 trust_env=True 会把
    localhost 也经系统代理(7897)转发 -> 502；且 gemma4 等推理模型经 /v1/chat/completions
    返回的 message.content 恒为空（思考被单独剥离，content 取不到）。
  - 改用 Ollama 原生 /api/chat 协议：直连、不走代理、content 正常返回。
对外仍模拟 `client.chat.completions.create(...)` 返回 `.choices[0].message.content`，
因此 planner / storyboard 等所有调用方无需改动。
"""
from __future__ import annotations

import sys
import time
import requests


def log(*args, **kwargs):
    """统一日志输出到 stderr，保持 stdout 干净（供 JSON / 管道解析使用）。"""
    print(*args, file=sys.stderr, **kwargs)


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, client):
        self._client = client

    def create(self, model=None, messages=None, temperature=0.85,
               max_tokens=400, **kwargs):
        return self._client._create(model=model, messages=messages,
                                    temperature=temperature,
                                    max_tokens=max_tokens, **kwargs)


class _Chat:
    def __init__(self, client):
        self.completions = _Completions(client)


class _OllamaClient:
    def __init__(self, chat_url: str, api_key: str = "ollama"):
        self._url = chat_url
        self._api_key = api_key
        self.chat = _Chat(self)

    def _create(self, model, messages, temperature, max_tokens, **kwargs):
        # 直连 Ollama，禁用代理（否则 127.0.0.1 会被系统代理 7897 转发导致 502）
        s = requests.Session()
        s.trust_env = False
        s.headers.update({"Content-Type": "application/json"})
        payload = {
            "model": model,
            "messages": messages or [],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        last_err = ""
        for attempt in range(3):
            try:
                r = s.post(self._url, json=payload, timeout=600)
                if r.status_code == 200:
                    data = r.json()
                    msg = (data.get("message") or {})
                    content = (msg.get("content", "") or "").strip()
                    # 推理模型（如 gemma4）会把回答放进 thinking，content 可能为空：
                    # 退而取 thinking 作为正文，保证上层能解析到内容。
                    if not content and msg.get("thinking"):
                        content = (msg.get("thinking", "") or "").strip()
                        log("  [llm] content 为空，回退使用 thinking 字段")
                    return _Resp(content)
                last_err = f"HTTP {r.status_code}"
                log(f"  [llm] 调用失败: {last_err}（第 {attempt+1}/3 次，2s 后重试）")
            except Exception as e:
                last_err = str(e)
                log(f"  [llm] 调用失败: {last_err}（第 {attempt+1}/3 次，2s 后重试）")
            time.sleep(2)
        log(f"  [llm] 重试耗尽，最后一次错误: {last_err}")
        return _Resp("")


def make_client(config: dict):
    llm = config.get("llm", {}) or {}
    if llm.get("disabled"):
        return None
    base_url = llm.get("base_url", "http://localhost:11434/v1")
    # 把 openai 兼容 base_url(http://host:port/v1) 转为原生 /api/chat
    native = base_url.rstrip("/")
    native = native.removesuffix("/v1").rstrip("/") + "/api/chat"
    return _OllamaClient(native, api_key=llm.get("api_key", "ollama"))


def chat(client, system: str, user: str, max_tokens: int = 400,
         temperature: float = 0.85, model: str = "qwen2.5:14b", **kwargs):
    if client is None:
        return None
    r = client.chat.completions.create(
        model=model, temperature=temperature, max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **kwargs,  # 透传 extra_body 等（原生协议忽略，content 已包含最终回答）
    )
    if r is None or not getattr(r, "choices", None):
        return None
    return r.choices[0].message.content.strip()


def extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else text
