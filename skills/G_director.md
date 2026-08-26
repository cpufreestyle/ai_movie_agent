# G 阶段 · 视频导演 — Skill 方法论

## 职责
把 F 润色后的分镜变成视频片段，并以 Diffusion Forcing **续写**实现持续/无限时长。

## 方法论（固化自 SkyReels-V2 Diffusion Forcing）
- 首镜 T2V；后续每镜把上一版影片作为 `--video_path` 在末尾续写，无缝变长。
- 关键帧（D 阶段）未来可经 I2V 注入，保障角色一致。

## 工具
`agent/engine.py :: SkyReelsEngine.generate(prompt, out, prev_clip)` +
`agent/director.py :: Director.beat_to_prompt(beat)`
- 配置：`config.engine.*`

## 兜底
无 GPU/未装 SkyReels → 仅产出提示词文本（`cli` 仍可验证流程）。
