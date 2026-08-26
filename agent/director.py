"""导演：把分镜(beat)翻译成 SkyReels 可用的视频提示词。

SkyReels 对英文提示词更友好，且偏好'画面 + 运镜 + 风格'的紧凑描述。
优先用 LLM 压缩成一条电影感提示词；否则用模板拼接。
"""
from __future__ import annotations

from .llmutil import make_client, chat


class Director:
    def __init__(self, config: dict):
        self.style = config.get("project", {}).get("style", "cinematic")
        llm = config.get("llm", {})
        self.model = llm.get("model", "qwen2.5:14b")
        self.temperature = float(llm.get("temperature", 0.85))
        self.disabled = bool(llm.get("disabled", False))
        self.client = make_client(config)

    def beat_to_prompt(self, beat: dict) -> str:
        desc = beat.get("description", "")
        shot = beat.get("shot", "")
        camera = beat.get("camera", "")
        mood = beat.get("mood", "")
        user = (
            "把下面这个电影分镜写成一句用于视频生成模型的英文提示词"
            "(不超过 200 字符)，包含画面、运镜和情绪，不要解释，只输出提示词：\n"
            f"description: {desc}\nshot: {shot}\ncamera: {camera}\nmood: {mood}\n"
            f"整体风格: {self.style}"
        )
        if not self.disabled and self.client is not None:
            out = chat(
                self.client,
                "你是资深影视分镜师，擅长把分镜写成精准的视频生成提示词。",
                user,
                max_tokens=120,
                model=self.model,
                temperature=self.temperature,
            )
            if out:
                return out.strip().strip('"')
        # 模板兜底
        parts = [str(desc)]
        if camera:
            parts.append(camera)
        if mood:
            parts.append(mood)
        parts.append(self.style)
        prompt = ", ".join(p for p in parts if p)
        return prompt[:200]
