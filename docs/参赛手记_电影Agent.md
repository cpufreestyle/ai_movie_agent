# 用 AI，提前看见未来：把一个剧组写进一套可运行的电影 Agent

> 参赛类别：**进阶创作｜电影 Agent**（AI+∞ 开发者创作大赛 · 第二期 · AI+影视流）
> 项目：**[ai_movie_agent](https://github.com/cpufreestyle/ai_movie_agent)** —— 本地持续创作的 AI 电影生成 Agent
> 技术栈：Qoder（本项目的代码与 Agent 编排即在 Qoder 中迭代）· 魔搭社区模型与 AIGC 工具链 · 本地 GPU（NVIDIA / AMD，含 DGX Spark 远程算力）

## 一、为什么是"电影 Agent"，而不是"一段视频"

赛题的第二类要求很明确：**把制作一部电影的方法，变成一套持续创作的系统**。这正是 `ai_movie_agent` 的出发点。

传统 AIGC 短片工作流是"人串工具"：你先写脚本，再手动出图，再一个个丢进视频模型，最后自己剪。难点不在某一步，而在**把一整套剧组协作法则固化成可复用、可无限续写的代码**。

我们做的，是把"编剧 / 导演 / 制片 / 剪辑"的职责，落成一组**有方法论、有工具的 Agent**，由统一编排器串成流水线：

```
cli.py ──> agent/agent.py (MovieAgent, 统一编排 A→H)
  素材层:  ├── collector.py     A 资料采集   (Crawl4AI / requests)
           ├── knowledge.py     B 知识沉淀   (RAGFlow / 本地检索)
  创意层:  ├── planner.py       C 概念企划   (MetaGPT 多角色)
           ├── image_prompt.py  D 图像提示词 (ComfyUI)
           ├── keyframe.py      D 关键帧出图 (ComfyUI -> I2V 首帧)
           ├── writer.py        E 剧本/分镜  (LLM + 模板兜底)
           ├── polisher.py      F 去AI味润色 (qu-ai-wei 方法论)
           ├── engine.py        G 视频导演   (SkyReels-V2 DF 续写 / MiniMax H3 / LTX-2.5 / Sol-H3)
           └── publisher.py     H 自动发布   (biliup-rs, B 站)
```

每个阶段在 `skills/` 下有一份**方法论文档**（固化自对应开源项目的方法论），在 `agent/` 下有一个**专用工具**（封装该开源项目，未安装时自动降级）。这套"方法论 + 工具"的分离，就是"为整个剧组写下运行法则"。

## 二、它真正能持续创作

- **无限时长 / 持续创作**：基于 SkyReels-V2 的 Diffusion Forcing 续写，每次在影片末尾追加新镜头，影片无缝生长；`python cli.py run --continuous` 可一直创作到手动停止。
- **自动剧本**：本地 LLM（默认 Ollama `qwen2.5:3b`，OpenAI 兼容接口）生成世界观与逐镜分镜；无 LLM 时自动降级为模板，保证流程可跑通。
- **电影感提示词**：导演模块把分镜压缩成视频引擎友好的英文提示词（含运镜、风格）。
- **逐镜自动质检**：每镜出片后自动打分（糊 / 静帧 / 全黑 / 可选身份漂移），不达标换 seed 自动重出（限次），落 `qa_report.json`。
- **Blender 预演**：白模动画的 depth/normal/line 控制图作为参考图喂给视频引擎，锁构图与走位（已 A/B 实测取舍）。
- **状态持久化**：每一镜的剧本、提示词、片段都落盘，可随时 `status` 查看进度，断点续作。

## 三、多引擎、跨平台：让"法则"不被单张卡绑架

视频引擎通过 `config.yaml` 的 `engine.backend` 切换，应用与算力解耦：

| 引擎 | 说明 | 量化 / 精度 |
|------|------|-------------|
| `comfyui_mmH3` | MiniMax H3（默认，音视频同出） | int4_convrot |
| `comfyui_ltx` | LTX-2.5（NVIDIA NVFP4 蒸馏） | fp4 / GGUF |
| `skyreels` | SkyReels-V2（无限时长 DF 续写） | 1.3B~14B |
| `sol_h3` | Sol-H3-Spark（远程 DGX Spark 常驻） | 1344x768 / 121 帧 |

部署支持 **Docker / 原生脚本，NVIDIA + AMD 均可**（AMD 走 ROCm，权重自动切 bf16/INT8）。跨平台一键部署见 `DEPLOY.md`；配置驱动的统一入口 `deploy.py` 会按"配置 × 环境（OS / Docker / GPU）"自动选择方案。

## 四、30 秒跑起来

```bash
cd ai_movie_agent
bash setup_wsl.sh                 # 克隆 SkyReels + 建 venv + 装依赖
source .venv/bin/activate

# 持续创作（Ctrl-C 停止，已生成的影片保留）
python cli.py run --continuous

# 完整 A–H 流水线（采集→沉淀→企划→关键帧→剧本→润色→生成→发 B 站）
python cli.py pipeline --topic "近未来赛博都市，记忆与身份" --max-scenes 8
```

真正生成电影（G 阶段）需要 GPU + ComfyUI；**创意策划 / 发布**两条链路不依赖 GPU，本机即可跑通（concept demo 生成、BGM 混入、投稿 B 站均实测成功）。

## 五、作品与合规

- 本作示例片名《看见未来之前》，为**完全原创的近未来赛博都市科幻设定**；所有画面由本地模型生成，**不含任何第三方影视的角色、台词、造型、剧照与片名**，符合赛事合规要求。
- 灵感可来自科幻类型，但画面 100% 属于本项目自身生成。

## 六、为什么它符合"电影 Agent"的精神

你既是总导演，也是为整个剧组写下运行法则的人。`ai_movie_agent` 把"如何拍一部电影"变成了：一套可配置的方法论（skills/）+ 一组可替换的工具（agent/）+ 一个统一编排器（MovieAgent）+ 一份驱动一切的配置（config.yaml）。换主题、换引擎、换显卡，只改配置，不碰代码——这就是"持续创作的系统"。

---

*如果想直接体验：本项目已打包为魔搭创空间应用（见仓库 `space/`），部署后即可在浏览器里输入一句话创意，由 Agent 自动企划并生成镜头。*
