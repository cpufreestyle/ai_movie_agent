"""C 阶段 · 概念企划。

方法论固化自 [MetaGPT](https://github.com/geekan/MetaGPT) 的"多角色协作"：
把创作拆成 编剧 / 导演 / 制片人 等角色，各自产出后汇总为统一企划(JSON)。
无 LLM 时降级为模板概念。

Skill 方法论见 skills/C_planner.md。
"""
from __future__ import annotations

import json

from .llmutil import make_client, chat, extract_json


class Planner:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("planner", {}) or {}
        self.client = make_client(config)
        llm = config.get("llm", {})
        self.model = llm.get("model", "qwen2.5:14b")
        self.temperature = float(llm.get("temperature", 0.85))
        # 检索回流参数：主题级(kb_topk) + 每镜级(beat_topk)
        self.kb_topk = int(self.cfg.get("kb_topk", 8))
        self.beat_topk = int(self.cfg.get("beat_topk", 3))

    def plan(self, topic: str, material: list[dict], kb) -> dict:
        # 主题级检索：把 RAGFlow/本地知识库的检索结果作为全局参考
        global_refs = self._retrieve(kb, topic, self.kb_topk)
        roles = self.cfg.get("roles", ["编剧", "导演", "制片人"])
        system = (
            f"你是多角色创作小组（{', '.join(roles)}），协同产出一部短片的概念企划。"
            "用 JSON 输出：logline(一句话梗概), setting(场景), protagonist(主角), "
            "theme(主题), outline(分镜大纲数组，每项是英文短句画面描述)。只输出 JSON。"
        )
        user = f"主题：{topic}\n参考素材：\n{chr(10).join(global_refs)[:3000]}"
        out = chat(self.client, system, user, max_tokens=800,
                   temperature=self.temperature, model=self.model) if self.client else None
        if out:
            try:
                concept = json.loads(extract_json(out))
            except Exception:
                print("  [C] LLM 返回非 JSON，降级模板")
                concept = _template_concept(topic)
        else:
            concept = _template_concept(topic)

        # 检索回流：把检索片段作为 world bible 的参考依据，并逐镜再检索
        concept["world_refs"] = global_refs
        outline = concept.get("outline", [])
        if isinstance(outline, list) and kb is not None:
            refs = {}
            for i, beat in enumerate(outline):
                q = beat if isinstance(beat, str) else str(beat)
                r = self._retrieve(kb, q, self.beat_topk)
                if r:
                    refs[str(i)] = r
            if refs:
                concept["outline_refs"] = refs
        return concept

    def enrich(self, concept: dict, topic: str, kb=None) -> dict:
        """C+ 阶段：在已有企划上补充更具体、可落地的设定。

        产出 characters(人物小传) / visual_style(视觉风格) / three_act(三幕)，
        喂回 bible，使创意/规划 demo 读真实数据而非派生占位。无 LLM 时降级模板。
        """
        system = (
            "你是短片概念企划的资深编剧。基于已有企划，补充更具象、可落地的设定。"
            "用 JSON 输出："
            "characters(数组，每项 {name, role, personality, motivation, arc})，"
            "visual_style(美术风格 / 色彩 / 光影 一句话)，"
            "three_act(数组3项：起 / 承 / 转 各一句)。只输出 JSON。"
        )
        user = f"主题：{topic}\n已有企划：\n{json.dumps(concept, ensure_ascii=False)}"
        out = (chat(self.client, system, user, max_tokens=900,
                    temperature=self.temperature, model=self.model)
               if self.client else None)
        extra = None
        if out:
            try:
                extra = json.loads(extract_json(out))
            except Exception:
                extra = None
        if not isinstance(extra, dict):
            extra = _template_extra(topic, concept)
        for k in ("characters", "visual_style", "three_act"):
            if k in extra and extra[k]:
                concept[k] = extra[k]
        return concept

    @staticmethod
    def _retrieve(kb, query: str, k: int) -> list[str]:
        if kb is None:
            return []
        try:
            return kb.retrieve(query, k=k) or []
        except Exception as e:
            print(f"  [C] 检索失败（忽略）: {e}")
            return []


def _template_concept(topic: str) -> dict:
    return {
        "logline": topic,
        "setting": "未命名场景",
        "protagonist": "主角",
        "theme": topic,
        "outline": [
            "an establishing shot of the world",
            "a character in motion",
            "a tense confrontation",
            "a quiet resolution",
        ],
    }


def _template_extra(topic: str, concept: dict) -> dict:
    """enrich() 的无 LLM 降级：给出合理的默认人物/风格/三幕。"""
    prot = concept.get("protagonist", "主角")
    return {
        "characters": [{
            "name": prot,
            "role": "主角",
            "personality": "坚韧而内心犹疑",
            "motivation": "找回失去之物 / 弄清真相",
            "arc": "从被动承受走向主动抉择",
        }],
        "visual_style": concept.get("visual_motif") or "冷调霓虹 + 手持纪实质感",
        "three_act": [
            "起：日常被打破，主角被迫卷入事件",
            "承：追逐与代价，关系与世界层层展开",
            "转：真相揭晓，主角做出关键抉择",
        ],
    }
