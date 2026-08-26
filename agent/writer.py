"""剧本与分镜生成。

优先使用本地 LLM（OpenAI 兼容接口，默认 Ollama），
当 LLM 不可用 / 被禁用时，降级为基于世界观的模板生成，
保证 Agent 在没有模型的情况下也能跑通整个创作流程。
"""
from __future__ import annotations

import json
import random

from .llmutil import make_client, chat, extract_json


class Writer:
    def __init__(self, config: dict):
        llm = config.get("llm", {})
        self.model = llm.get("model", "qwen2.5:14b")
        self.temperature = float(llm.get("temperature", 0.85))
        self.disabled = bool(llm.get("disabled", False))
        self.theme = config.get("project", {}).get("theme", "")
        self.client = make_client(config)

    # ---------- 内部工具 ----------
    def _chat(self, system: str, user: str, max_tokens: int = 400) -> str | None:
        return chat(self.client, system, user, max_tokens=max_tokens,
                    temperature=self.temperature, model=self.model)

    # ---------- 公开接口 ----------
    def story_bible(self) -> dict:
        """生成/返回一个世界观设定（角色、场景、基调）。"""
        system = (
            "你是一个电影编剧。请用简洁的 JSON 输出一部短片的'世界观设定'，"
            "字段：title(片名), logline(一句话梗概), setting(场景), "
            "protagonist(主角设定), tone(基调), visual_motif(视觉母题)。"
            "只输出 JSON，不要多余文字。"
        )
        user = f"主题：{self.theme}"
        txt = self._chat(system, user)
        if txt:
            try:
                bible = json.loads(extract_json(txt))
                return bible
            except Exception:
                pass
        # 模板兜底
        return {
            "title": "未命名短片",
            "logline": self.theme,
            "setting": "近未来赛博都市的雨夜街道",
            "protagonist": "一名寻找丢失记忆的侦探",
            "tone": "冷峻、悬疑、诗意",
            "visual_motif": "霓虹倒影与雨水",
        }

    def next_beat(self, bible: dict, history: list[dict]) -> dict:
        """根据世界观与已有分镜，生成下一个分镜（beat）。"""
        ctx = "\n".join(
            f"第{i+1}镜: {b.get('title','')} — {b.get('description','')}"
            for i, b in enumerate(history)
        )
        # 检索回流：把 C 企划里的 RAGFlow 检索片段作为一致性约束
        kb_ctx = self._kb_context(bible, len(history))
        # 传给大模型的世界观去掉大段检索片段，避免重复塞满提示词
        slim = {k: v for k, v in (bible or {}).items()
                if k not in ("world_refs", "outline_refs")}
        system = (
            "你是一个电影导演助理。基于已有分镜，构思'下一个'电影分镜，"
            "保持叙事连贯与角色一致。用 JSON 输出，字段："
            "title(分镜名), description(画面描述, 英文, 可用短句), "
            "shot(景别如 wide/close-up/over-shoulder), camera(运镜如 pan/slow dolly), "
            "mood(情绪)。只输出 JSON。"
        )
        user = (
            f"世界观: {json.dumps(slim, ensure_ascii=False)}\n"
            f"已有分镜:\n{ctx}\n{kb_ctx}\n\n请构思下一个分镜。"
        )
        txt = self._chat(system, user)
        if txt:
            try:
                beat = json.loads(extract_json(txt))
                beat.setdefault("title", f"scene {len(history)+1}")
                beat.setdefault("description", self.theme)
                return beat
            except Exception:
                pass
        return _template_beat(bible, history)

    @staticmethod
    def _kb_context(bible: dict, next_index: int) -> str:
        """从企划的检索回流片段里抽取与'下一镜'相关的知识库参考。"""
        if not isinstance(bible, dict):
            return ""
        parts = []
        world = bible.get("world_refs") or []
        if world:
            parts.append("全局参考资料(来自知识库，用于保持角色/场景一致):\n"
                         + "\n".join(world[:3]))
        refs = bible.get("outline_refs") or {}
        nxt = refs.get(str(next_index))
        if nxt:
            parts.append(f"本镜参考资料(来自知识库):\n" + "\n".join(nxt[:2]))
        return "\n".join(parts)


_SHOT_BANK = [
    ("wide", "establishing shot of the city skyline at night"),
    ("close-up", "a face lit by flickering neon, eyes reflecting the city"),
    ("over-shoulder", "a figure walking through a crowded rain-soaked street"),
    ("tracking", "a slow tracking shot following the protagonist down an alley"),
    ("low-angle", "towering buildings overhead as rain falls"),
    ("insert", "a handheld device displaying a fragment of memory"),
]


def _template_beat(bible: dict, history: list[dict]) -> dict:
    setting = bible.get("setting", "a mysterious place")
    shot, desc = _SHOT_BANK[len(history) % len(_SHOT_BANK)]
    moods = ["tense", "melancholic", "hopeful", "suspenseful", "serene"]
    return {
        "title": f"scene {len(history) + 1}",
        "description": f"{desc} in {setting}",
        "shot": shot,
        "camera": random.choice(["slow dolly", "static", "gentle pan"]),
        "mood": random.choice(moods),
    }
