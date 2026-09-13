---
title: AI 电影 Agent（本地持续创作）
app_file: app.py
app_entrypoint: app.py
sdk: gradio
python_version: "3.10"
disable_gpu: false
show_file: true
---

# AI 电影 Agent（本地持续创作）

把"编剧 → 导演 → 引擎"的剧组协作，固化成一套可运行、可无限续写的电影 Agent 系统，
对应 **AI+∞ 开发者创作大赛 · 第二期 · AI+影视流** 的「进阶创作｜电影 Agent」类别。

## 它能做什么
- 持续创作 / 无限时长：基于 SkyReels-V2 Diffusion Forcing 续写，影片可一直生长。
- 自动剧本：本地 LLM 生成世界观与逐镜分镜；无 LLM 时模板兜底。
- 电影感提示词、逐镜自动质检、Blender 预演、状态持久化。
- 多引擎可切换：MiniMax H3 / LTX-2.5 / SkyReels / 远程 DGX Spark(Sol-H3)。

## 本 Space 演示
- 「创意企划」标签：输入一句话主题，Agent 产出世界观 Bible + 逐镜分镜。
- 默认 CPU 环境走内置模板 Demo；配置 `COMFYUI_API` 等变量后即跑真实流水线并出片。

## 启用真实视频生成
创空间环境变量：`COMFYUI_API`、`ENGINE_BACKEND`、`LLM_MODEL`；并在 requirements 安装项目完整依赖。
详见仓库 `README.md` / `DEPLOY.md`。

源码：https://github.com/cpufreestyle/ai_movie_agent
