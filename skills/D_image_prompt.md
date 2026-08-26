# D 阶段 · 图像提示词 / 关键帧 — Skill 方法论

## 职责
为企划里每个分镜生成**图像生成提示词**，用于出关键帧（保障后续视频角色一致）。

## 方法论（固化自 ComfyUI 提示词工程）
- 每个分镜 → 一条含"主体 + 风格 + 构图"的英文图像提示词。
- 关键帧经 ComfyUI(SDXL/WAN) 出图后，可交给 SkyReels **I2V** 生成该镜
  （避免 T2V 角色漂移）。当前把提示词存入 `state.image_prompts` 备用。

## 工具
- `agent/image_prompt.py :: ImagePrompt.generate(concept)` —— 产出每个分镜的英文图像提示词。
- `agent/keyframe.py :: KeyframeGenerator.generate(prompts)` —— 把提示词交给 ComfyUI
  出关键帧 PNG（`workdir/keyframes/keyframe_*.png`）。
- `agent/engine.py :: SkyReelsEngine.generate(..., image=keyframe)` —— DF 续写命令加
  `--image <keyframe>`，即以该图为起始帧做 I2V，**保证各镜角色一致**。

## 配置
`config.image_prompt.comfyui.{api, workflow}`：api 指向 ComfyUI 服务，workflow 为导出的
workflow JSON（含 "text" 字段的节点会被注入提示词）。

## 兜底
- 无 LLM → 模板提示词（`cinematic keyframe of <beat>`）。
- ComfyUI 未就绪 / 未配 workflow → 不出关键帧图，退化为纯 T2V/DF（不影响管线）。
