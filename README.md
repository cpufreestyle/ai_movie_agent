# AI 电影 Agent（本地持续创作）

一个在 **WSL / 本地** 持续创作的 AI 电影生成 Agent。参考开源项目
[SkyReels-V2](https://github.com/SkyworkAI/SkyReels-V2)（昆仑万维开源的
**无限时长电影生成模型**，基于 Diffusion Forcing）作为视频引擎，并在其上叠加一个
"编剧 → 导演 → 引擎" 的 Agent 编排层，实现自动化、可无限续写的电影创作。

## 它能做什么
- **持续创作 / 无限时长**：用 SkyReels 的 Diffusion Forcing 续写能力，每次在当前影片
  末尾追加一段新镜头，影片无缝变长，Agent 可一直创作到手动停止。
- **自动剧本**：用本地 LLM（默认 Ollama，OpenAI 兼容接口）生成世界观设定与逐镜分镜；
  无 LLM 时自动降级为模板生成，保证流程可跑通。
- **电影感提示词**：导演模块把分镜压缩成 SkyReels 友好的英文提示词（含运镜、风格）。
- **状态持久化**：每一镜的剧本、提示词、片段都落盘，可随时 `status` 查看进度。

## 架构
```
cli.py ──> agent/agent.py (MovieAgent, 统一编排 A→H)
  素材层:  ├── collector.py    A 资料采集   (Crawl4AI / requests)
           ├── knowledge.py    B 知识沉淀   (RAGFlow / 本地检索)
  创意层:  ├── planner.py      C 概念企划   (MetaGPT 多角色)
           ├── image_prompt.py D 图像提示词 (ComfyUI)
           ├── keyframe.py     D 关键帧出图 (ComfyUI -> SkyReels I2V)
           ├── writer.py       E 剧本/分镜  (LLM + 模板兜底)
           ├── polisher.py     F 去AI味润色 (qu-ai-wei 方法论)
           ├── engine.py       G 视频导演   (SkyReels-V2 DF 续写, 子进程)
           ├── director.py     分镜 -> SkyReels 提示词
           └── publisher.py    H 自动发布   (biliup-rs, B 站)

每个阶段的"方法论"固化在 skills/ 目录（A_collector.md … H_publisher.md），
"工具"即上面的 agent/<工具>.py。
```
输出在 `outputs/`：`film.mp4`（持续增长的长片）、`state.json`、`script.jsonl`、
`scenes/`（每镜片段与备份）。

## 环境要求
- WSL2 + Ubuntu，NVIDIA 显卡 + CUDA 驱动（WSL 内 `nvidia-smi` 可见）。
- 本项目实测环境：RTX 5070 Ti 16GB → 跑 `SkyReels-V2-DF-1.3B-540P`（约 14.7GB 显存）。
- 想要 14B 画质需 ≥48GB 显存，改 `config.yaml` 的 `model_id` 即可。
- 本地 LLM 可选：装 [Ollama](https://ollama.com) 并 `ollama pull qwen2.5:14b`。
  不装也能跑（模板模式）。

## 快速开始（WSL）
```bash
cd ai_movie_agent
bash setup_wsl.sh                 # 克隆 SkyReels + 建 venv + 装依赖
source .venv/bin/activate

# 持续创作（Ctrl-C 停止，已生成的影片会保留）
python cli.py run --continuous

# 只生成 5 镜
python cli.py run --max-scenes 5

# 交互式逐镜确认
python cli.py run --no-continuous --interactive

# 查看进度
python cli.py status

# 跑完整 A–H 流水线（A 采集 → B 沉淀 → C 企划 → D 关键帧
#   → E 剧本 → F 润色 → G 生成 → H 发 B 站）
python cli.py pipeline --topic "你设定的主题" --max-scenes 8
# 跳过素材/企划阶段，仅用已有世界观做创意+发布
python cli.py pipeline --no-research --max-scenes 5
```

首次 `run` 会从 HuggingFace 自动下载模型权重（1.3B 约 5~10GB）。
若 WSL 需要代理，先 `export HTTPS_PROXY=... HTTP_PROXY=...` 再运行。

## WebUI（本地浏览器可视化操作）

不想敲命令行？内置一个**零构建的本地 WebUI**（Flask + 原生前端），把上面这些能力搬到浏览器里：

- **概览配置**：实时影片状态（分镜数 / 时长 / BGM / 封面 / 登录态）+ 在线编辑 `config.yaml`
- **运行监控**：一键启动 A–H 流水线 / 续写，实时日志流 + 进度（需 GPU / SkyReels）
- **创意策划**：`enrich-bible` 充实世界观（人物小传 / 视觉风格 / 三幕）并重渲染 concept demo，预览播放，一键投稿到 B 站（带 BGM / 转场）
- **发布**：biliup 登录态引导 + 投稿正式成片

启动：
```bash
pip install flask                 # 首次需要（已写入 requirements.txt）
python cli.py webui               # 默认 http://127.0.0.1:8000
python cli.py webui --port 9000   # 自定义端口
```
打开浏览器访问提示的地址即可。所有耗时操作在后台线程执行，日志页每秒自动刷新。

> 提示：真正生成电影（G 阶段）需要 GPU + SkyReels；**创意策划 / 发布** 两条链路不依赖 GPU，本机即可跑通（已实测：concept demo 生成、BGM 混入、投稿 B 站均成功）。biliup 扫码登录仍须在**真实终端**执行（`biliup login`），Web 端会给出引导命令。

## 配置说明（config.yaml）
| 项 | 说明 |
|----|------|
| `project.theme` / `style` | 世界观主题与视觉风格 |
| `engine.model_id` | 模型，1.3B-540P / 14B-540P / 14B-720P |
| `engine.offload` | 显存不足时卸载到 CPU |
| `engine.scene_frames` | 每镜帧数（97≈4s @24fps） |
| `llm.*` | Ollama / 任意 OpenAI 兼容端点；`disabled: true` 强制模板 |

## 全链路 A–H 与 Skills

流水线：`A 资料采集 → B 知识沉淀 → C 概念企划 → D 图像提示词 → E 剧本创作
→ F 去AI味润色 → G 视频导演 → H 自动发布`。每个阶段在 `skills/` 下有一份
**方法论文档**（固化自对应开源项目的方法论），在 `agent/` 下有一个**专用工具**
（封装该开源项目，未安装时自动降级）。统一编排器 `MovieAgent.run(do_research=True)`
即 `cli.py pipeline`，按序串起全部阶段。

| 阶段 | 工具 | 方法论(Skill) | 开源项目 |
|------|------|---------------|----------|
| A 资料采集 | Collector | skills/A_collector.md | Crawl4AI |
| B 知识沉淀 | Knowledge | skills/B_knowledge.md | RAGFlow |
| C 概念企划 | Planner | skills/C_planner.md | MetaGPT |
| D 图像提示词/关键帧 | ImagePrompt+KeyframeGenerator | skills/D_image_prompt.md | ComfyUI |
| E 剧本创作 | Writer | skills/E_writer.md | ShortGPT(参考) |
| F 去AI味润色 | Polisher | skills/F_polisher.md | qu-ai-wei |
| G 视频导演 | Engine+Director | skills/G_director.md | SkyReels-V2 |
| H 自动发布 | Publisher | skills/H_publisher.md | biliup-rs |

## 发布到 B 站（H 阶段，自动投稿）
视频生成后可用开源工具 [biliup-rs](https://github.com/biliup/biliup-rs) 自动投稿。

1. 安装 biliup（见 `setup_wsl.sh` 已含步骤），并**手动登录一次**：
   ```bash
   biliup login        # 按提示扫码/密码，登录态写入 cookies.json（约 1~3 个月有效）
   ```
2. 在 `config.yaml` 的 `publish:` 段填写投稿模板（标题/标签/分区 tid 等），
   并把 `enabled: true` 打开（生成结束即自动投稿），或单独执行：
   ```bash
   python cli.py publish                       # 投稿 outputs/movie_final.mp4
   python cli.py publish --video 某片段.mp4     # 投稿指定影片
   ```
   > 注意：标签参数是单数 `--tag`（逗号分隔）；`--dtime` 为 10 位时间戳且需晚于
   > 当前时间 4 小时以上；分区 `tid` 请按 B 站实际分区表核对。

## 进阶
- **更长更连贯**：调大 `overlap_history`(17→37)、`addnoise_condition`(20)，或用异步
  `ar_step` + `causal_block_size`。
- **角色一致性**：后续可接入本地图像模型（SDXL/ComfyUI）生成关键帧，再用 SkyReels I2V
  (`--image`) 生成镜头。
- **配音**：用本地 TTS 生成旁白，editor 阶段合入音轨。
