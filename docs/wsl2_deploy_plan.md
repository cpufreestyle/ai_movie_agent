# WSL2 部署规划（MiniMax H3 / ComfyUI on RTX 5070 Ti 16GB）

> 背景：本项目在 Windows 原生 ComfyUI 上已跑通 H3（INT4 量化档 + Turbo 4 步），但存在若干 Windows 侧不确定项：
> - 启动参数里长期带 `--fp32-vae`（已确认 cu130 下 VAE 默认精度正确，该参数属多余，正按方案 A 去掉验证）；
> - `comfy_kitchen` 的 triton backend 在 Windows 下 `ImportError: No module named 'triton'`（disabled），部分 kernel 走 eager fallback；
> - Windows 的 WDDM 显存调度 / 长进程稳定性偶发问题。
>
> WSL2 作为**备选/对照环境**，目标是验证「同一套 H3 工作流在 Linux 内核 + CUDA 下是否更稳定、triton kernel 可用、VAE/attention 行为更可预期」。

## 1. 目标与判定标准
- [ ] WSL2 内 `nvidia-smi` 可见 RTX 5070 Ti，显存 ≈16GB。
- [ ] ComfyUI 在 WSL2 启动，`comfy_kitchen` triton backend `available: True`。
- [ ] 用同一份 H3 工作流跑通一个最小镜头（如 768×448 / ~1.8s），VAE 解码正常（无全噪声 / 无尺寸崩溃）。
- [ ] 与 Windows 侧出片做质量对照（人脸检出率 + arcface 一致性）。

## 2. 前置条件（Windows 主机侧）
- Windows 11 + 已启用 WSL2（`wsl --install` 或手动装 Ubuntu 22.04/24.04）。
- **主机装最新 NVIDIA 驱动（Game Ready / Studio 均可）**：WSL2 的 CUDA 由主机驱动提供，无需在 WSL 内装独立驱动。
- 验证：`wsl` 内执行 `nvidia-smi` 能看到 GPU。

## 3. WSL2 资源配置（`.wslconfig`，Windows 用户目录）
```
[wsl2]
memory=24GB          # 给 WSL 留足，避免默认吃掉一半物理内存
processors=8
# GPU 显存与 Windows 共享，无法单独扩大；16GB 仍是硬上限
localhostForwarding=true
```
改完 `wsl --shutdown` 重启生效。

## 4. ComfyUI + 节点安装（WSL2 内）
```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv python3-pip git
git clone https://github.com/comfyanonymous/ComfyUI.git ~/ComfyUI
cd ~/ComfyUI && python3.11 -m venv venv && source venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130   # 对应 cu130
pip install -r requirements.txt

# 自定义节点（与 Windows 侧一致）
cd custom_nodes
git clone <comfyui-minimax-h3-audio-T8>
git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
git clone https://github.com/city96/ComfyUI-GGUF
git clone <ComfyUI-LTXVideo>
git clone <comfyui-tokendance-h3>
pip install opencv-python-headless   # VideoHelperSuite 依赖
```

## 5. 模型复用（关键：避免重复下载 ~40GB）
- Windows 模型根 `E:/ComfyUI_models` 在 WSL2 中挂载于 `/mnt/e/ComfyUI_models`，**但跨 NTFS 挂载加载慢且易锁**。
- 推荐：`extra_model_paths.yaml` 指向 `/mnt/e/ComfyUI_models` 只读复用；若加载慢，把 H3 量化档（扩散 11.3G + 文本 15G + VAE）**复制进 WSL 本地 ext4**（如 `~/ComfyUI_models/`），VAE/tokenizer 小文件可 symlink。
- 注意路径映射：Windows `E:\` → `/mnt/e/`、`D:\` → `/mnt/d/`。

## 6. 显存与精度要点（与 Windows 侧对齐）
- 同样走 H3 INT4 量化档；`comfy_kitchen` convrot/int8 kernel 在 Linux 下支持更完整。
- **不要带 `--fp32-vae`**：cu130 下 VAE 默认精度正确（同期 Windows 侧已按方案 A 去掉验证）。
- 16GB 仍是硬约束：H3 全套（扩散 11.3G + 文本 15G + 视频 VAE 4.9G + 音频 VAE 0.6G）靠 DynamicVRAM 分页，WSL2 下 WDDM 换页可能略慢，需实测吞吐。
- `VHS_VideoCombine` 必填 `save_output/loop_count/pingpong`；宽高须 32 整除；H3 帧数走 `17n+5` 吸附。

## 7. 对照实验（验证价值）
| 维度 | Windows（现状） | WSL2（待验证） |
|------|----------------|----------------|
| triton backend | disabled（eager fallback） | 期望 available |
| VAE 精度 | 去 fp32-vae 后默认 | 默认 |
| 长进程稳定性 | 偶有 connection reset | 待测 |
| 单镜耗时 | Turbo 4步 ≈48s | 待测 |
| 出片质量 | arcface 0.466（换脸后） | 对照 |

## 8. 决策阈值
- 若 WSL2 下 triton 可用且出片质量/稳定性明显优于 Windows → 迁移主产出到 WSL2，Windows 侧保留应急。
- 若无明显收益（16GB 仍是瓶颈、跨挂载慢）→ 维持 Windows 主方案，WSL2 仅作对照组，不投入迁移。

## 9. 风险
- WSL2 GPU 显存与 Windows 共享，无法突破 16GB；NVFP4/convrot 量化在 Linux 同样依赖 `comfy_kitchen`，无免费午餐。
- NTFS 跨挂载加载大模型慢且有文件锁风险 → 建议模型落地 WSL 本地盘。
- 节点在 Linux 下的兼容性需逐个验证（尤其 minimax-h3-audio-T8 的 T8 依赖）。

---
*本规划对应待决策项 B；A（去 --fp32-vae 重启验证）在 Windows 侧同步执行。B 仅规划，是否真正落地取决于第 8 节决策阈值。*
