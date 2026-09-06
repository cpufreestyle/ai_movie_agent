# 把 NVFP4 · LTX-2.5 · ComfyUI · 本地大模型串起来

把视频生成阶段（G）从 SkyReels-V2 换成 **ComfyUI 跑 LTX-2.5（NVFP4 量化）**，
并用**本地大模型（Ollama）**写提示词，形成完整闭环：

```
本地大模型(Ollama, qwen2.5:7b)
   │  写分镜 / 运动提示词
   ▼
agent.director  →  agent.ltx_engine.LTXEngine
   │  提交 workflow
   ▼
ComfyUI  ──(运行 LTX-2.5, 模型加载 precision=fp4)──▶  短视频片段 .mp4
   │                                                  （Blackwell / RTX 50 系用 NVFP4 加速）
   ▼
agent 续写 / 封装  →  outputs/film.mp4
```

本机环境已确认：NVIDIA RTX 5070 Ti（Blackwell，compute cap 12.0），**支持 NVFP4**；
Ollama 已安装。只需补装 ComfyUI + LTX-2.5 节点与模型即可跑通。

---

## 1. 安装 ComfyUI

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

启动（默认端口 8188，与 config 一致）：

```bash
python main.py --listen 127.0.0.1 --port 8188
```

浏览器打开 http://127.0.0.1:8188 确认能加载。

## 2. 安装 LTX-2.5 的 ComfyUI 节点

Lightricks 官方节点（任选其一，按你的 LTX 版本）：

- LTX-Video / LTX-2：`ComfyUI-LTXVideo`（https://github.com/Lightricks/ComfyUI-LTXVideo）
- LTX-2.5：对应新版节点（留意 Lightricks 仓库与 HuggingFace 上的 LTX-2.5 发布说明）

放入 `ComfyUI/custom_nodes/` 并重启 ComfyUI，侧栏出现 LTX 相关节点即成功。

## 3. 拉取 LTX-2.5 模型

在 ComfyUI 的 Model 目录放入 LTX-2.5 权重（transformer / VAE / text encoder）。
若节点支持 **fp4 权重**（Blackwell），优先用 fp4 版以最大化 NVFP4 收益；
否则用 `precision: fp4` 让节点在加载时做 fp4 量化（见第 5 步）。

## 4. 启用本地大模型（Ollama）

Ollama 已安装，拉一个模型即可作为"大脑"写提示词：

```bash
ollama pull qwen2.5:7b          # 16GB 显存够用；更大可选 qwen2.5:14b
```

`config.yaml` 中 `llm` 段已将 `disabled: false`（无模型时自动降级为模板，管线照跑）。

## 5. 导出 LTX-2.5 workflow 并接线

1. 在 ComfyUI 里搭好 LTX-2.5 文生视频 / 图生视频工作流，能正常出片；
2. 右上角 **Manager / 启用 Dev mode** → 把工作流 **Save (API Format)** 存成 JSON；
3. 把该 JSON 绝对路径填到 `config.yaml`：

```yaml
engine:
  backend: skyreels            # 改成 comfyui_ltx 即切换为 LTX-2.5 引擎
  comfyui_ltx:
    api: "http://127.0.0.1:8188"
    workflow: "C:/abs/path/to/ltx2_api.json"   # ← 第 2 步导出的文件
    precision: "fp4"          # NVFP4（Blackwell）；非 50 系改 "fp8" 或 "default"
    resolution: "768x768"
    fps: 25
    num_frames: 97
    seed: 0
```

`LTXEngine` 会自动把以下字段注入你的 workflow（无需改节点名）：

| 想要的注入 | 节点输入字段名 |
|---|---|
| 提示词 | `text` 或 `positive`（字符串） |
| 帧率 | `frame_rate` |
| 帧数 | `num_frames` |
| 分辨率 | `width` / `height` |
| 随机种子 | `seed` |
| NVFP4 精度 | `precision`（模型加载节点的该输入会被设为 `fp4`） |
| I2V 起始帧 | `image`（提供 `--image` 时自动上传并填入） |

> 只要你的节点字段名匹配上表，就能被正确注入；否则在 ComfyUI 里手动固定即可。

## 6. 切换引擎 / 跑通

- **只测 LTX**（不影响默认 SkyReels 管线）：

  ```bash
  python cli.py ltx --prompt "a cyberpunk cityscape, slow dolly in" \
                    --image outputs/keyframes/keyframe_000.png \
                    --out outputs/ltx_clip.mp4
  ```

  未装 ComfyUI 会提示未就绪并退出（不报错崩）。

- **整条管线用 LTX-2.5 续写**：把 `config.yaml` 的 `engine.backend` 改为 `comfyui_ltx`，
  再 `python cli.py pipeline --max-scenes 3`。引擎未就绪时管线会自动停止（同 Blender 降级逻辑）。

## 7. NVFP4 说明与排错

- NVFP4 需要 **Blackwell 架构（RTX 50 系，compute cap ≥ 12.0）** + 较新 ComfyUI / torch。
  本机 RTX 5070 Ti 满足。非 50 系请把 `precision` 改 `fp8` 或 `default`。
- `precision` 字段不存在于你的加载节点时，引擎不会强行注入（安全跳过），
  可在 ComfyUI 里手动把加载节点设为 fp4。
- 若 `python cli.py ltx` 报 "未产出视频"：检查 workflow 的输出节点
  （SaveAnimatedWEBM / VideoCombine）是否已连接到 LTXVDecode。
- 若 ComfyUI 端口不通：确认 `main.py` 用 `--port 8188` 启动且防火墙放行。
