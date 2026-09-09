# AI 电影 Agent · 部署指南

## 这套东西由什么组成
- **Agent 应用**（本项目，纯 Python + Flask，WebUI 端口 8000）：写脚本 / 分镜 / 配音 / 封装成片。本身**不带大模型**。
- **Ollama**（LLM，端口 11434）：跑 qwen2.5 等文本模型，负责所有文案 / 企划 / 分镜。**跨平台，必须装。**
- **ComfyUI**（视频生成，端口 8188）：跑 LTX-2.5 / MiniMax H3 出视频。**需要 NVIDIA 显卡。**

> 视频模型是 NVFP4 / int4 量化，依赖 CUDA，**macOS 无法本地跑视频**。macOS 上只能跑 A–F 的文案链路；视频阶段把 `COMFYUI_API` 指向一台远程有显卡的 ComfyUI 即可（应用与视频引擎解耦，天然支持）。

## 先看：选 Docker 还是原生脚本？

两种都能在 Linux / Windows / macOS 跑起来，**二选一**即可。Agent 与 LLM/Ollama 永远在本机或容器，视频走外部 ComfyUI（有显卡的机器，可远程）。

| 维度 | 方式一 · Docker Compose | 方式二 · 原生一键脚本 |
|---|---|---|
| 前置依赖 | 只需装 Docker Desktop | 需 Python 3.10+，并自行装 Ollama |
| 上手成本 | 最低，一条 `docker compose up` | 中，跑脚本建 venv + 装 Ollama |
| 改代码即时生效 | 否，改完要重新 `build` | 是，改完直接重跑 WebUI |
| 环境隔离 | 强，不污染本机 | 弱，依赖装进本机 `.venv` |
| 给别人交付 | 最省事（对方只要有 Docker） | 对方也要会装 Python/Ollama |
| 视频(ComfyUI) | 同 compose 可选启用，或指向远程 | 同左，指向远程或本机启动 |
| 适合谁 | 交付给别人 / 换机器 / 不想折腾环境 | 自己开发调试 / 想改源码 |

> 结论：给别人用、图省事 → **Docker**；自己开发、要频繁改代码 → **原生脚本**。两者视频出片能力完全一致。

## 方式一：Docker Compose（推荐，跨平台）
Linux / Windows(Docker Desktop) / macOS 都适用，一条命令起。

```bash
git clone <repo> && cd ai_movie_agent
cp .env.example .env                 # 按需改 COMFYUI_API / ENGINE_BACKEND
docker compose up -d                 # 起 agent + ollama
docker compose exec ollama ollama pull qwen2.5:3b
# 浏览器打开 http://localhost:8000
```

- Agent 与 Ollama 在容器内；视频走 ComfyUI（默认期望同网络 `http://comfyui:8188`）。
- **有 NVIDIA 显卡的机器**想让 ComfyUI 也进容器：在 `.env` 设 `COMFYUI_IMAGE=你们带 LTX-2.5 / MiniMax H3 节点的 ComfyUI 镜像`，然后 `docker compose --profile gpu up -d`（用 profile 启用，无需改文件）。容器把 `/ComfyUI/models` 挂成卷，权重用下方下载脚本放进对应子目录即可。
- **macOS / 无显卡机器**：不启用 gpu profile，`.env` 里 `COMFYUI_API=http://<远程显卡机IP>:8188`。

## 方式二：原生一键脚本（不装 Docker）
- **Windows**：`setup_windows.bat` → `start_webui_windows.bat`
- **Linux / macOS**：`bash setup_unix.sh` → `./start_webui.sh`

脚本自动建 `.venv` 并装 `requirements.txt`。仍需你另装 Ollama（`ollama pull qwen2.5:3b`）以及（视频）ComfyUI。

## 视频引擎与模型权重
- 默认引擎已设为 **MiniMax H3（`comfyui_mmH3`）**；改 LTX-2.5 设 `ENGINE_BACKEND=comfyui_ltx`（或在 WebUI「接口与模型设置」里选）。
- 权重需自行下载到 ComfyUI 的 `models/`：
  - LTX-2.5：transformer(nvfp4) + Gemma4-12B 文本编码器(int8-convrot)
  - MiniMax H3：unet + qwen3vl_32b 文本编码器 + video_vae + audio_vae + turbo LoRA
  - VAE 走 `pixel_space`，无需单独文件。
- 官方仓库 gated；可用免 token 社区镜像（经 `hf-mirror.com`）：
  - LTX-2.5：`python download_ltx_models.py`（NVFP4 transformer + int8 文本编码器 + 音视频 VAE，多源续传）。
  - **MiniMax H3（默认引擎）**：`python download_mmh3_models.py --models-dir <ComfyUI/models 路径>`（int4_convrot pruned unet + qwen3vl_32b 文本编码器 + 音视频 VAE + turbo LoRA，多源续传）。
  - 两条脚本都自动走本地代理 `127.0.0.1:7897`、断连自动重试，在「有 NVIDIA 显卡、已装好 ComfyUI」的机器上跑。

## 环境变量（Docker / 远程部署用，免改 config.yaml）
| 变量 | 作用 |
|---|---|
| `OLLAMA_URL` | LLM 地址（自动补 `/v1`） |
| `LLM_MODEL` | LLM 模型名 |
| `LLM_API_KEY` | LLM key（Ollama 默认 `ollama`） |
| `COMFYUI_API` | 视频服务地址 |
| `ENGINE_BACKEND` | `comfyui_mmH3` / `comfyui_ltx` |

## 给别人交付的最小清单
1. 整个项目目录（含 `config.yaml` / `workflows` / `requirements.txt` / `Dockerfile` / `docker-compose.yml` / 脚本）。
2. 一句说明：需要 Docker，或 Python 3.10+ + Ollama +（视频）ComfyUI。
3. 一份 `.env`（或口头告知 `COMFYUI_API` 指向哪台显卡机）。
4. 视频权重让对方在其显卡机上按上节下载（本项目已内置 LTX-2.5 的免 token 下载脚本）。
