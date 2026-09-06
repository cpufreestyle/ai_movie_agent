# ai_movie_agent 项目总结（截至 2026-09-03）

> 硬件基线：RTX 5070 Ti 16GB 显存（可用 ~15.9GB）+ 32GB 系统内存；计划迁移到 AMD Ryzen AI MAX+ 395 128GB 统一内存。

## 1. 主要请求与意图
- 在 16GB 卡上找到**可实际出片**的视频生成路线（放弃物理不可行方案，选定 Wan2.2-TI2V-5B GGUF 与 LTX-2.3 GGUF）。
- 沉淀并修复 ComfyUI 工作流具体坑（VAE 注入、采样参数、负向词褪色、多实例抢显存）。
- 为 AMD 395 机器规划部署（NVFP4 不兼容 → 改用 BF16/FP8 transformer）。
- 提供端到端脚本与 CLI：`cli.py ltx`、`run_wan22_*`、`run_ltx23.py`、`launch_comfy.py`。

## 2. 关键技术概念
- **量化格式**：NVFP4(convrot_w4a4, Blackwell 专用)、GGUF(Q3/Q4/Q5_K_M)、BF16、FP8(e4m3fn)、int8 文本编码器。
- **ComfyUI 节点**：UnetLoaderGGUF / CLIPLoaderGGUF / VAELoader / Wan22ImageToVideoLatent / ModelSamplingSD3 / KSampler / VAEDecode；LTX 原生节点 91 个（含 GGUF）。
- **显存/内存约束**：16GB 可用 ~15.9GB；32GB 跑 22B+12B 双量化仍 OOM。
- **采样参数**：Wan2.2 720p 需 `ModelSamplingSD3 shift=8.0`（480p/T2V 才是 5.0）；cfg 5–6、steps 30–40、euler/beta。
- **多镜头连贯**：ffmpeg `-sseof` 抽尾帧 → `POST /upload/image` → I2V 续写 → xfade 拼接。
- **环境**：venv 解释器、本地代理 `127.0.0.1:7897`、ComfyUI 默认 8188 / 干净实例 8200。

## 3. 文件与代码段落
- `agent/ltx_engine.py`：`_resolve_vae_nodes` 把两个 VAELoader 分别注入 `video_vae`/`audio_vae`，修复 `LTXVEmptyLatentAudio` 抛 `'PixelspaceConversionVAE' has no attribute 'latent_frequency_bins'`。
- `config.yaml`：模型路径映射（已迁 `E:/ComfyUI_models/`，经 `extra_model_paths.yaml` 的 `ltx23_e` 段），含节点映射。
- `cli.py`：`ltx --prompt "..." --out outputs/ltx_clip.mp4 [--frames N]`；`feishu poll`；prompt 含空格须整体传参。
- `launch_comfy.py`：重启 ComfyUI，支持 `--lowvram/--novram/--reserve-vram/--vram-headroom/--disable-smart-memory/--fp8-text-enc`。
- `tools/comfyui_client.py`、`tools/blender_mcp.py` / `tools/blender_server.py`：ComfyUI 通信与 Blender MCP 集成。
- 生成脚本：`run_wan22_test.py`(832x480x49)、`run_wan22_scifi.py`(1280x704x81)、`run_wan22_multishot.py`(5 镜 720p，支持 `--shot N`/`--concat`)、`run_ltx23.py`(GGUF Q3_K_M)、`run_ltx_gguf_test.py`、`download_ltx_models.py`、`download_hf_mt.py`。

## 4. 错误与修复
- **22B NVFP4 OOM（16GB 死路）**：降分辨率/帧数无效（瓶颈在常驻权重 ~17.8GB）。`--lowvram/--novram/--reserve-vram/--fp8-text-enc` 全部失败；`--novram` 走 CPU 时 32GB 内存也装不下 → CPU OOM。
- **LTX-2.5 双 VAELoader 退化**：未注入时退化为 `pixel_space` 报错 → `ltx_engine.py` 注入修复。
- **Wan2.2 发灰/褪色**：空负向词导致 HSV 饱和度仅 34/255。修复：填 Wan 官方中文负向词；褪色补救 `ffmpeg -vf eq=saturation=2.1:contrast=1.18,colorbalance...`。
- **Wan2.2 shift 用错**：720p 用 `shift=5`（应为 8）致对比度/饱和度坍缩 → 改 8.0。
- **16GB 显存抖动**：81帧x50步触发 offload 抖动（5min→25–59min 崩溃）→ 稳定区间 720p x 65帧 x 30步。
- **ComfyUI 多实例抢显存**：手动实例与 `--fp32-vae` 实例并存占满显存 → 批量前确认单实例（`Get-CimInstance Win32_Process`）并 `POST /queue {"clear":true}`。

## 5. 问题求解
- **16GB 卡可行路线**：Wan2.2-TI2V-5B GGUF（分钟级出片，质量第一梯队）+ LTX-2.3 GGUF Q3_K_M（~70s/768x512x97，不发灰）。
- **AMD 395 路线**：必须换 BF16(~44GB)/FP8(~22GB) transformer（NVFP4 不兼容）；UMA 帧缓冲 BIOS 设 75–96GB；Linux+ROCm 最稳。
- **放弃**：LTX-2.5 FP8（27GB 物理死路）、GGUF 量化路线（仅 16GB 卡需要，AMD 128GB 不需）。

## 6. 当前权重清单（16GB 卡已下载）
| 角色 | 路径 | 大小 |
|---|---|---|
| Wan2.2 transformer | diffusion_models/Wan2.2-TI2V-5B-Q8_0.gguf | 5.03GB |
| Wan 文本编码器 | text_encoders/umt5xxl-encoder-q5_k_m.gguf | 3.86GB |
| Wan VAE | vae/Wan2.2_VAE.safetensors | 1.31GB |
| LTX-2.5 transformer(NVFP4) | diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors | 18.72GB（16GB 卡不可用） |
| LTX-2.5 文本编码器 | text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors | 13.17GB |
| LTX-2.5 视频/音频 VAE | vae/ltx-2.5-video-vae-bf16 / ltx-2.5-audio-vae-bf16 | 1.47GB / 0.36GB |
| LTX-2.3 GGUF(Q3_K_M) | unsloth/LTX-2.3-GGUF（16GB 跑通） | 10.1GB |

## 7. 待办任务
- 验证 Wan2.2 多镜头成片 `outputs/scifi_multishot.mp4` 连贯性与转场质量。
- （可选）AMD 395 机器落地：下载 BF16/FP8 transformer，BIOS 调 UMA，WSL2/Linux + ROCm 部署。
- 清理未跟踪临时脚本（`_probe*.py`、`_status.py` 等）与 `.err` 日志。

## 8. 当前工作
项目处于**经验沉淀 + 多镜头成片收尾**阶段：Wan2.2-TI2V-5B 已成功出片（`wan22_scifi_contest_00001_.webm`），`run_wan22_multishot.py` 已实现尾帧续写 + xfade 拼接并支持断点续跑。LTX-2.3 GGUF 路线已在 16GB 跑通验证。

## 9. 可选下一步
- `python run_wan22_multishot.py --concat` 确认 5 镜拼片产出 `outputs/scifi_multishot.mp4`；若某镜失败用 `--shot N` 单镜重跑。
