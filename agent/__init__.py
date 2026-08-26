"""AI 电影 Agent：在本地持续创作的 AI 电影生成器（A–H 全链路）。

视频引擎：SkyReels-V2 (Diffusion Forcing, 无限时长)
发布引擎：biliup-rs (B 站自动投稿)

阶段 ↔ 工具：
  A 资料采集   Collector   (Crawl4AI)
  B 知识沉淀   Knowledge   (RAGFlow / 本地)
  C 概念企划   Planner     (MetaGPT 角色法)
  D 图像提示词 ImagePrompt (ComfyUI)
  E 剧本创作   Writer
  F 去AI味润色 Polisher    (qu-ai-wei 方法论)
  G 视频导演   Engine+Director (SkyReels-V2)
  H 自动发布   Publisher   (biliup-rs)
各阶段的方法论文档见 skills/ 目录。
"""

from .agent import MovieAgent
from .writer import Writer
from .director import Director
from .engine import SkyReelsEngine
from .editor import Editor
from .publisher import Publisher
from .collector import Collector
from .knowledge import Knowledge
from .planner import Planner
from .image_prompt import ImagePrompt
from .polisher import Polisher
from .keyframe import KeyframeGenerator

__all__ = [
    "MovieAgent", "Writer", "Director", "SkyReelsEngine", "Editor", "Publisher",
    "Collector", "Knowledge", "Planner", "ImagePrompt", "Polisher", "KeyframeGenerator",
]
