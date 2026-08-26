"""D 阶段 · 图像提示词（关键帧）。

为企划里每个分镜生成图像生成提示词（供 ComfyUI / SDXL 出关键帧，
再交给 SkyReels I2V 保证角色一致）。可选对接 ComfyUI API 直接出图（占位）。
无 LLM 时降级为模板提示词。

Skill 方法论见 skills/D_image_prompt.md。
"""
from __future__ import annotations

from .llmutil import make_client, chat


class ImagePrompt:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("image_prompt", {}) or {}
        self.client = make_client(config)
        self.model = config.get("llm", {}).get("model", "qwen2.5:14b")

    def generate(self, concept: dict) -> list[str]:
        outline = concept.get("outline", [])
        refs = concept.get("outline_refs", {}) or {}
        return [self._one(beat, concept, refs.get(str(i))) for i, beat in enumerate(outline)]

    def _one(self, beat: str, concept: dict, ref: list[str] | None = None) -> str:
        if self.client:
            extra = ""
            if ref:
                # 回流：把该镜对应的 RAGFlow 检索片段作为一致性约束
                extra = "\n参考资料(来自知识库，用于保持角色/场景一致):\n" + "\n".join(ref[:2])
            out = chat(
                self.client,
                "把分镜写成一条用于图像生成(ComfyUI/SDXL)的英文提示词，含主体、风格、构图，不解释。",
                f"分镜: {beat}\n风格: {concept.get('theme', 'cinematic')}{extra}",
                max_tokens=120, model=self.model,
            )
            if out:
                return out.strip().strip('"')
        return f"cinematic keyframe of {beat}, {concept.get('theme', 'cinematic')}"
