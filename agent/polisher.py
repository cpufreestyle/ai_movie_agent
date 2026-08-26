"""F 阶段 · 去AI味润色。

方法论参考开源 [qu-ai-wei](https://github.com/LifelongLazyLearner/qu-ai-wei)
（中文去 AI 味）。工具实现以 LLM 提示词拟人化为主（开箱即用），
若 method=qu-ai-wei 且本地装了该 Skill/CLI 可在此接其调用。
无 LLM / 未启用时原样返回（不影响管线）。

Skill 方法论见 skills/F_polisher.md。
"""
from __future__ import annotations

from .llmutil import make_client, chat


class Polisher:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("polisher", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.method = self.cfg.get("method", "llm")
        self.client = make_client(config)
        self.model = config.get("llm", {}).get("model", "qwen2.5:14b")

    def polish(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        if self.method == "llm" and self.client:
            out = chat(
                self.client,
                "你是资深中文编辑。把下面这段 AI 生成的文字润色得更像人写的："
                "去掉 AI 套话（如'在当今世界''值得注意的是''综上所述''首先…其次'），"
                "改用具体、口语化、有节奏的中文，保持原意。只输出润色后的文字。",
                text, max_tokens=400, model=self.model,
            )
            if out:
                return out.strip()
        return text
