# LTX-2.5 在 AMD Ryzen AI MAX+ 395（128GB 统一内存）部署计划

> 制定：2026-09-01。背景：当前 RTX 5070 Ti（16GB）机器上 LTX-2.5 工程代码/配置已就绪（VAE 注入修复完成），但 16GB 显存装不下 22B NVFP4 模型。换到 AMD 395 MAX（128GB UMA）后无需量化即可跑通，且可复用全部已修复工程。

## 0. 核心结论
- 换机器 = **换权重精度 + 换环境（ROCm）**，代码层（ltx_engine/config/cli）几乎零改动。
- **不要**走 GGUF 量化路线（那是给 16GB 卡准备的）。128GB 内存直接用官方 BF16。

## 1. ⚠️ 最关键架构认知
- **NVFP4 是 NVIDIA Blackwell 专用 4-bit 格式（convrot_w4a4），AMD RDNA3.5 不支持**。当前 `D:/ComfyUI/models/diffusion_models/ltx-2.5-22b-distilled-transformer-nvfp4.safetensors` 在 AMD 机器上**无法加载**，必须弃用。
- AMD 机器用 **BF16 transformer（~44GB）** 或 **FP8 transformer（~22GB）**。128GB 统一内存对 BF16（44G 模型 + 13G 文本编码器 + 2G VAE ≈ 60GB）绰绰有余，剩 60+GB 给采样激活。

## 2. 环境搭建（AMD 机器）
- **OS**：推荐 Linux（Ubuntu 22.04/24.04）+ ROCm 6.2+；Windows 可用但 ROCm/AMDXDNA 支持弱，建议 WSL2 或直接装 Linux。
- **PyTorch**：ROCm 版，例如 `pip install torch --index-url https://download.pytorch.org/whl/rocm6.2`（或 6.3）。
- **ComfyUI**：标准安装（git clone + venv + 依赖）。
- **LTX-2.5 节点**：ComfyUI v0.32+ 已原生内置 LTX 节点（LTXVBaseSampler 等）；或装 `lxxxy6/LTX-2.5` 自定义节点。
- **启动参数**：`--use-pytorch-cross-attention`（AMD 兼容性），**不要**加任何 NVFP4/lowvram 相关开关。

## 3. 第一坑：统一内存 (UMA) 帧缓冲分配【成败关键】
- Strix Halo 默认给 iGPU 的 UMA 帧缓冲只有 16–32GB。若不调整，ROCm/ComfyUI 只能看到这点“显存”，44GB 模型仍会 OOM。
- **必须**：进 BIOS 把 `UMA Frame Buffer Size` 设为 **75–96GB**（或启用动态/Game 分配模式）。Linux 同样需在 BIOS 设置。
- 设置后用 `rocm-smi` 或 `amd-smi` 确认 iGPU 可用显存 ≈ 分配值。

## 4. 权重（在 AMD 机器）
| 角色 | 来源 | 大小 | 备注 |
|---|---|---|---|
| transformer BF16 | `Lightricks/LTX-2.5` 或 `lxxxy6/LTX-2.5` 的 `ltx-2.5-22b-distilled-transformer-bf16.safetensors` | ~44GB | 主模型，需重新下载 |
| transformer FP8(备选) | `Guillaume-127/LTX-2.5-FP8` | ~22GB | 需确认 ROCm FP8 支持，优先 BF16 |
| 文本编码器 | `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` | 13.17GB | **本机已下，可直接拷贝复用** |
| 视频 VAE | `ltx-2.5-video-vae-bf16.safetensors` | 1.47GB | **本机已下，可拷贝复用** |
| 音频 VAE | `ltx-2.5-audio-vae-bf16.safetensors` | 0.36GB | **本机已下，可拷贝复用** |
- 放置目录：transformer → `models/diffusion_models/`（或 `models/unet/`）；文本编码器 → `models/text_encoders/`；VAE → `models/vae/`。
- 下载策略：若 AMD 机器网络好，直下最快；否则本机下好经 NVMe/移动盘拷过去（文本编码器与 VAE 已在本机 `D:/ComfyUI/models/`）。

## 5. 复用本机工程（与硬件无关）
- `agent/ltx_engine.py`（VAE 注入修复）直接复制使用。
- `config.yaml` 中 `checkpoint` 指向 BF16 文件名（去掉 nvfp4）；`video_vae`/`audio_vae` 字段保留。
- `download_ltx_models.py` 增加 BF16/FP8 选项（可选，或直接用 HF 下载）。
- `cli.py ltx --prompt "..." --out outputs/ltx_clip.mp4 [--frames N]` 命令不变。

## 6. 验证步骤
1. BIOS 设好 UMA 帧缓冲 → 启动 ComfyUI（ROCm）→ `system_stats` 确认 iGPU 大显存可见。
2. 放好权重 → 跑 `python cli.py ltx --prompt "A cat walking on grass" --out outputs/ltx_clip.mp4 --frames 33`。
3. 预期：44GB BF16 全载入统一内存，采样流畅，产出 mp4（端到端首测建议用低分辨率 768x432 / 33 帧验证管线）。

## 7. 待确认（影响精确执行步骤）
- AMD 机器 OS（Windows / Linux）？能否被我远程访问操作，还是你手动执行？
- transformer 用 BF16（推荐，44GB）还是 FP8（22GB）？
- 权重在本机下载后拷贝，还是 AMD 机器直下？
