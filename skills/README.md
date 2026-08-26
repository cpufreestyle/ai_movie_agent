# Skills 总览：A–H 阶段 ↔ 外部 Skill 方法论 ↔ 专用工具

本项目每个阶段 = 一个**外部 Skill（方法论）** + 一个**专用工具（Tool，封装开源项目）**。
方法论固化在本文档与各 `skills/<阶段>.md`；工具实现在 `agent/<工具>.py`，通过统一
编排器 `agent/agent.py (MovieAgent.run)` 串成 A→H 流水线。

| 阶段 | 职责 | 方法论来源(Skill) | 专用工具(Tool) | 封装的开源项目 |
|------|------|------------------|---------------|----------------|
| A 资料采集 | 搜索/抓取素材 | Crawl4AI 文档抓取 SOP | `Collector` | [Crawl4AI](https://github.com/unclecode/crawl4ai) |
| B 知识沉淀 | 素材→可检索知识 | RAGFlow 中文切片检索 | `Knowledge` | [RAGFlow](https://github.com/infiniflow/ragflow) |
| C 概念企划 | 选题/大纲 | MetaGPT 多角色协作 | `Planner` | [MetaGPT](https://github.com/geekan/MetaGPT) |
| D 图像提示词 | 关键帧提示词 | ComfyUI 提示词工程 | `ImagePrompt` | [ComfyUI](https://github.com/comfyanonymous/ComfyUI) |
| E 剧本创作 | 脚本/分镜 | 短剧脚本流水线 | `Writer` | [ShortGPT](https://github.com/RayVentura/ShortGPT)（参考） |
| F 去AI味润色 | 拟人化 | qu-ai-wei 中文去味 | `Polisher` | [qu-ai-wei](https://github.com/LifelongLazyLearner/qu-ai-wei) |
| G 视频导演 | 视频生成 | SkyReels Diffusion Forcing | `Engine`+`Director` | [SkyReels-V2](https://github.com/SkyworkAI/SkyReels-V2) |
| H 自动发布 | 发 B 站 | biliup 投稿 SOP | `Publisher` | [biliup-rs](https://github.com/biliup/biliup-rs) |

> 设计原则：每个工具的"主路径"调用对应开源项目，**未安装/未配置时自动降级**
> （模板或本地实现），保证整条流水线在没有全部依赖时也能跑通。
