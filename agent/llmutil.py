"""共享 LLM 工具：统一创建 OpenAI 兼容客户端并对话。

默认指向本地 Ollama；无 openai 依赖 / 禁用时返回 None，调用方据此降级为模板。
"""
from __future__ import annotations

import sys


def log(*args, **kwargs):
    """统一日志输出到 stderr，保持 stdout 干净（供 JSON / 管道解析使用）。"""
    print(*args, file=sys.stderr, **kwargs)


def make_client(config: dict):
    llm = config.get("llm", {}) or {}
    if llm.get("disabled"):
        return None
    try:
        from openai import OpenAI
        return OpenAI(
            base_url=llm.get("base_url", "http://localhost:11434/v1"),
            api_key=llm.get("api_key", "ollama"),
        )
    except Exception:
        return None


def chat(client, system: str, user: str, max_tokens: int = 400,
         temperature: float = 0.85, model: str = "qwen2.5:14b"):
    if client is None:
        return None
    try:
        r = client.chat.completions.create(
            model=model, temperature=temperature, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        log(f"  [llm] 调用失败: {e}")
        return None


def extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else text
