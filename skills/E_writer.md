# E 阶段 · 剧本创作 — Skill 方法论

## 职责
基于 C 的概念企划，逐镜生成剧本/分镜（beat），供 G 视频导演消费。

## 方法论（参考 ShortGPT 脚本流水线）
- 以世界观为恒定上下文，逐镜推进，保持角色/设定一致。
- 每镜输出 title / description / shot / camera / mood。

## 工具
`agent/writer.py :: Writer.next_beat(bible, history)`
- 复用 C 产出的 `state.bible` 作为世界观。
- 支持本地 LLM（Ollama）或模板兜底。

## 兜底
无 LLM → 基于世界观的模板分镜（景别轮转 + 随机情绪）。
