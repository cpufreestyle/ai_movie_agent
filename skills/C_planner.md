# C 阶段 · 概念企划 — Skill 方法论

## 职责
基于素材与主题，产出**统一概念企划**：梗概、场景、主角、主题、分镜大纲。

## 方法论（固化自 MetaGPT 多角色协作）
- 把创作拆成角色：编剧（故事）、导演（视听）、制片人（可行性/节奏）。
- 一次性让多角色"协同"产出 JSON 结构，避免单角色视角片面。
- 角色列表可在 `config.planner.roles` 调整。

## 工具
`agent/planner.py :: Planner.plan(topic, material, kb)`
- 用 B 的 `retrieve(topic)` 取相关素材作为上下文。
- 输出写入 `state.bible`（作为 E 剧本的世界观）。

## 兜底
无 LLM → 模板概念（主题=梗概，含 4 句默认大纲）。
