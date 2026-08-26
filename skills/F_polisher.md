# F 阶段 · 去 AI 味润色 — Skill 方法论

## 职责
把 E 产出的分镜描述/文案润色得更像人写，去除 AI 套话。

## 方法论（固化自 qu-ai-wei 中文去味）
- 识别并剔除 AI 高频辞令："在当今世界""值得注意的是""综上所述""首先…其次…"。
- 改用具体、口语化、有节奏的中文，保留原意与信息量。

## 工具
`agent/polisher.py :: Polisher.polish(text)`
- 配置：`config.polisher.{enabled, method}`
- 主路径用 LLM 提示词拟人化（`method: llm`，开箱即用）；
  若本地装了 qu-ai-wei Skill/CLI，可在 `method: qu-ai-wei` 下接其调用。

## 兜底
未启用 / 无 LLM → 原样返回，不影响后续。
