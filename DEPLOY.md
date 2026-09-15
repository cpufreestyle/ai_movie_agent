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

## 方式零：统一部署入口 `deploy.py`（推荐，按配置自动选方案）

不想手动判断 Docker / 原生、NVIDIA / AMD、MiniMax H3 / LTX-2.5？用统一入口：它读 `config.yaml`
（`engine.backend`、`blender.enabled`、ComfyUI 地址等）+ 探测本机（OS / Docker / GPU），自动算出该用哪套方案。

```bash
python deploy.py                 # 只打印方案（不改动任何东西）
python deploy.py --apply         # 执行安全部分：建 venv、装依赖、写 .env、docker compose up
python deploy.py --apply --with-weights --models-dir D:/ComfyUI/models   # 额外授权下载视频权重
# 也可手动覆盖探测结果：
python deploy.py --method docker --gpu amd --engine comfyui_ltx
```

- 视频权重大下载需显式 `--with-weights`（用户授权）才执行；Blender 不自动安装（见下方白模节）。
- 跨平台：Windows / Linux / macOS 同一脚本；读配置不依赖 PyYAML（内置 mini 解析兜底，venv 前的系统 Python 也能跑）。
- 下方「方式一 / 方式二」仍可手动使用；`deploy.py` 本质上帮你选了其中一条，并补上 `.env` 与权重下载脚本。

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

- **想在 ComfyUI 界面里手动出片**：导入 `workflows/mmh3_turbo_4v8a_ui.json`（菜单 `Workflow → Open`，或直接拖到画布）。
  它由 `python make_mmh3_workflow.py` 生成：以官方 T8 示例为骨架，按 `config.yaml` 填入本机实际权重。
  > 手搭时最常见的错误是把文本编码器选成 LTX-2.5 的 `gemma4-12b…`(6144 维)，而 H3 的
  > `condition_proj` 期望 5120 维，会报 `mat1 and mat2 shapes cannot be multiplied (75x6144 and 5120x5376)`。
  > 正确搭配：`CLIPLoader` = `qwen3vl_32b_minimax_h3_int4_convrot.safetensors`、**type = `minimax`**；
  > VAE = `minimax_h3_video_vae_fp16` + `minimax_h3_audio_vae_fp32`；且不能用 `KSampler`/`CLIPTextEncode`
  > 那套通用 SD 节点，必须走 T8 节点链（AudioConditioning → MultiRate/DualClock Sampler →
  > SamplerCustomAdvanced → AVDecode → VHS_VideoCombine）。

## 视频引擎增强：性能 / 质量 节点与插件
- 提速：**SageAttention** 注意力后端（`pip install sageattention` 后 `python launch_comfy.py --sage-attention`，已在脚本内置开关），支持的显卡采样提速且更省显存。
- 提质：在「解码 → 保存」之间插入**超分 + 锐化**，仅增强图像、不动音频。开关在 `config.yaml` 的 `engine.comfyui_mmH3.post` / `engine.comfyui_ltx.post`：
  ```yaml
  post:
    upscale_model: ""      # ESRGAN 模型（放 ComfyUI/models/upscale_models/）；留空=不超分，填了才启用
    sharpen: 0.2           # 0~1，默认 0.2 轻度锐化已开启；改 0 关闭
  ```
  锐化默认开启（内置节点、零依赖）。若报节点缺失，把 `sharpen` 改回 `0` 即退回原行为。
- 完整插件清单（帧插值 RIFE、SUPIR 综合修复、TeaCache 等）与一键安装见 [docs/comfyui_plugins.md](docs/comfyui_plugins.md)（`bash setup_comfy_plugins.sh`）。

## 显卡后端：NVIDIA 还是 AMD？

视频生成依赖的具体量化格式不同，ComfyUI 运行时也不同：

| 后端 | 运行时 | 默认权重 | 说明 |
|---|---|---|---|
| **NVIDIA** | CUDA | NVFP4（LTX）/ int4_convrot（MiniMax H3） | 开箱即用，性能最好 |
| **AMD** | ROCm（仅 Linux） | bf16 / INT8 / GGUF 变体 | NVFP4/int4_convrot 是 CUDA 专属，必须换权重 |

**自动探测（尽力而为，给建议）**：`bash detect_gpu.sh` 会看 `nvidia-smi` / `rocminfo` / `/dev/kfd`，输出该用哪条命令。

**Docker 下选择**：
```bash
# NVIDIA（默认）
docker compose --profile gpu up -d

# AMD / ROCm（叠加覆盖文件，把 comfyui 切成 ROCm 镜像 + 直连 /dev/kfd /dev/dri）
docker compose -f docker-compose.yml -f docker-compose.amd.yml --profile gpu up -d
#   .env 里设：GPU_BACKEND=amd  且  COMFYUI_IMAGE_ROCM=你们的 ROCm ComfyUI 镜像
```

**原生脚本下选择**：下载权重时按后端切换（HEAD 自检，源不存在自动跳过）：
```bash
python download_mmh3_models.py --gpu nvidia --models-dir D:\ComfyUI\models   # MiniMax H3
python download_mmh3_models.py --gpu amd    --models-dir /ComfyUI/models     # INT8 变体
python download_ltx_models.py   --gpu nvidia --models-dir D:\ComfyUI\models   # NVFP4
python download_ltx_models.py   --gpu amd    --models-dir /ComfyUI/models     # bf16 官方权重
```
AMD 上把 `.env` 的 `GPU_BACKEND=amd`，Agent 会自动把 LTX 精度注入改为 `bf16`；MiniMax H3 走 INT8 变体即可。

> Windows 上的 AMD 需经 Zluma/DirectML 跑 ComfyUI，不稳定，本交付未内置专门配置；建议 AMD 视频在 Linux(ROCm) 或远程 NVIDIA 机器上跑。

### AMD Ryzen AI Max+ 395（Strix Halo, 128GB 统一内存）

这台机器是目前**本地跑满血 LTX-2.5 22B** 最省事的方案：128GB 统一内存足够直接用官方
**BF16 权重（~44GB）**，不必 GGUF 量化（量化是给 16GB 卡准备的）。三种选法任选：

| 方式 | 命令 / 配置 |
|---|---|
| 环境变量 | `.env` 里设 `GPU_BACKEND=amd` + `HW_TIER=amd395-128g` |
| 部署入口 | `python deploy.py --gpu amd --tier amd395-128g`（会把 `HW_TIER` 写进 `.env`） |
| config.yaml | `hw_tier: amd395-128g`（或 `auto_hardware: true`，在真机上会自动识别成这一档） |

档位 `amd395-128g` 做的事：LTX 精度钉死 `bf16`、`1024x576` / `90` 帧、开 block_cache 与两遍采样、
不 offload、Blender 64 采样、QA 允许 3 次 reroll。别名 `amd395` / `395` / `strix-halo` 均可。

**为什么要显式选**：395 的「显存」是从统一内存切出来的（BIOS 的 UMA Frame Buffer），
而 Windows WMI 的 `AdapterRAM` 是 32 位字段、iGPU 常被报成 512MB~4GB，Linux `lspci` 更是 0 ——
自动检测会把这台顶级机器判成 `cpu` 档。本档位改用「AMD + 内存 ≥96GB + 型号线索」识别，绕开这个坑。

> ⚠️ **BIOS 必须先设 UMA Frame Buffer Size = 75–96GB**（默认只给 iGPU 16–32GB，44GB 模型会 OOM）。

- 验证档位生效：`python tools/hw_profile.py --tier amd395`（打印将应用的覆盖）；
  在真机上直接 `python tools/hw_profile.py` 应自动推荐 `amd395-128g`。
- 完整环境搭建（ROCm / ComfyUI / BF16 权重 / 文本编码器与 VAE）：见
  [LTX25_AMD395_PLAN.md](LTX25_AMD395_PLAN.md) 与 `bash setup_amd.sh`。

### NVIDIA DGX Spark（Project Digits, GB10 Blackwell, 128GB 统一内存）

DGX Spark 与 AMD 395 同属「大统一内存」机器：128GB 统一内存足够直接跑官方 **BF16 权重（~44GB）**，
不必 GGUF 量化。档位 `dgxspark-128g` 与 `amd395-128g` 的覆盖**完全相同**（bf16、1024x576 / 90 帧、
两遍采样、不 offload、Blender 64 采样、QA 3 次 reroll）。三种选法任选：

| 方式 | 命令 / 配置 |
|---|---|
| 环境变量 | `.env` 里设 `HW_TIER=dgxspark-128g`（NVIDIA 机器默认后端即 `nvidia`，无需 `GPU_BACKEND=amd`） |
| 部署入口 | `python deploy.py --tier dgxspark-128g`（会把 `HW_TIER` 写进 `.env`） |
| config.yaml | `hw_tier: dgxspark-128g`（或 `auto_hardware: true`，在真机上会自动识别成这一档） |

别名 `dgxspark` / `dgx-spark` / `dgx` / `digits` / `project-digits` / `gb10` 均可。

**为什么要显式选**：DGX Spark 的「显存」也是从 128GB 统一内存切出来的，`nvidia-smi` 上报的 VRAM 偏小，
且常被误认成独立 HBM 卡（DGX A100/H100、RTX 5090）——后者应留在 `high` 档。本档位改用
「NVIDIA + 内存 ≥96GB + 型号线索（GB10 / DGX SPARK / DIGITS）+ 极低显存兜底」识别，绕开这个坑。

- 验证档位生效：`python tools/hw_profile.py --tier dgxspark`（打印将应用的覆盖）；
  在真机上直接 `python tools/hw_profile.py` 应自动推荐 `dgxspark-128g`。

## 环境变量（Docker / 远程部署用，免改 config.yaml）
| 变量 | 作用 |
|---|---|
| `OLLAMA_URL` | LLM 地址（自动补 `/v1`） |
| `LLM_MODEL` | LLM 模型名 |
| `LLM_API_KEY` | LLM key（Ollama 默认 `ollama`） |
| `COMFYUI_API` | 视频服务地址（ComfyUI 引擎） |
| `SOL_H3_API` | Sol-H3 服务地址（sol_h3 引擎，如 http://<DGX-IP>:8000） |
| `ENGINE_BACKEND` | `comfyui_mmH3` / `comfyui_ltx` / `sol_h3` |
| `GPU_BACKEND` | `nvidia`(默认) / `amd`；`amd` 时自动把 LTX 精度降为 bf16 |
| `HW_TIER` | 硬件档位：`high` / `mid` / `low` / `cpu` / `amd395-128g` / `dgxspark-128g`。`amd395-128g`=AMD Ryzen AI Max+ 395(128G 统一内存)；`dgxspark-128g`=NVIDIA DGX Spark / Project Digits(GB10, 128G 统一内存)。也可用 `AUTO_HW=1` 自动检测 |

## 视频引擎四：Sol-H3-Spark（DGX Spark 远程）

NVIDIA 官方 [Sol-H3-Spark](https://nvlabs.github.io/Sana/Sol-Engine/Sol-H3-Spark/)：单台 DGX Spark
（GB10 Blackwell）上跑的端到端视频生成（H3 草图 + LTX-2.5 细化），输出 1344×768 / 121 帧 / 24fps
带音频 MP4。项目把它封装成**常驻 HTTP 服务**跑在 DGX Spark 上，本机 agent 经 `engine.backend: sol_h3`
远程调用（与「远程 ComfyUI」同一解耦思路，本机无需显卡）。

- 部署 DGX 端：`deploy/sol_h3_spark/deploy_sol_h3_spark.sh`（克隆 Sana sol-engine → 建三环境 →
  `prepare.py` 生成 `paths*.json` → `cache_builder` → `download_checkpoints.py` 下权重 →
  后台启动 `sol_h3_server.py`）。详见 `deploy/sol_h3_spark/README.md`。
- 本机接入：`config.yaml` 设 `engine.backend: sol_h3` 且 `engine.sol_h3.api: http://<DGX-IP>:8000`；
  或用 `ENGINE_BACKEND=sol_h3` + `SOL_H3_API=...`。`python deploy.py` 会打印对应的远程部署步骤。
- 注意：官方声明 "clean installation not validated"，aarch64 编译 CUDA 扩展可能需排错；
  本机只跑 agent，视频全在 DGX Spark 出。

## 给别人交付的最小清单
1. 整个项目目录（含 `config.yaml` / `workflows` / `requirements.txt` / `Dockerfile` / `docker-compose.yml` / 脚本）。
2. 一句说明：需要 Docker，或 Python 3.10+ + Ollama +（视频）ComfyUI。
3. 一份 `.env`（或口头告知 `COMFYUI_API` 指向哪台显卡机）。
4. 视频权重让对方在其显卡机上按上节下载（本项目已内置 LTX-2.5 的免 token 下载脚本）。
