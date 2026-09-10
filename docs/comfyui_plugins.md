# ComfyUI 性能与质量 节点 / 插件 推荐

本项目视频引擎跑在 ComfyUI 上（MiniMax H3 / LTX-2.5）。下面是为「提速 + 提质」筛选的
节点/插件清单，分 **性能（后端）** 与 **质量（后处理）** 两类。流水线里已内置的开关见
`config.yaml` 的 `engine.comfyui_mmH3.post` / `engine.comfyui_ltx.post`，以及
`launch_comfy.py` 的 `--sage-attention`。

> 优先级建议：**先上 SageAttention（性能，零代码改动）+ 超分/锐化（质量，已内置开关）**，
> 这两项是投入产出比最高的；其余按需装。

## 一、性能（后端加速，不改工作流）

| 插件 / 开关 | 作用 | 启用方式 |
|---|---|---|
| **SageAttention** | 替换默认注意力后端，支持的显卡上采样提速 20~40%、更省显存 | `pip install sageattention` 后 `python launch_comfy.py --sage-attention`（已内置开关） |
| **xFormers** | 另一套注意力/算子优化，部分模型比 Sage 更稳 | ComfyUI 自带支持，`pip install xformers` + 启动加 `--use-xformers`（与 Sage 二选一） |
| **fp8 / NVFP4 量化** | 权重占显存大幅下降（已在引擎默认走 int4/int8） | 无需额外操作 |
| **TeaCache / FirstBlockCache** | 缓存残差跳步，少算若干步（提速明显） | LTX-2.5 装 `ComfyUI-TeaCache`；H3 需确认官方节点是否暴露 cache 输入（暂未内置，待节点支持） |
| **关 torch.compile** | 22B 模型编译易卡死，已默认 `TORCH_COMPILE_DISABLE=1` | 无需操作 |

安装 SageAttention 后，AMD/老卡若报错，移除 `--sage-attention` 回退默认后端即可。

## 二、质量（后处理，已内置开关）

流水线在「解码 → 保存视频」之间插入后处理，**只增强图像、不动音频**（避免音画不同步）。
帧插值会改变帧率，故**不接入带音频的主管线**，作为离线增强单独用。

| 增强 | 节点 | 启用（config.yaml） | 备注 |
|---|---|---|---|
| **超分** | `UpscaleModelLoader` + `ImageUpscaleWithModel`（内置） | `post.upscale_model: "4x-UltraSharp.pth"` | 模型放 `ComfyUI/models/upscale_models/` |
| **锐化** | `ImageSharpen`（内置） | `post.sharpen: 0.3`（0~1） | 轻度锐化去糊，过大易出噪点 |
| **帧插值** | `ComfyUI-Frame-Interpolation`（RIFE） | 离线手动加 / 后期剪辑 | 翻倍 fps 更顺滑；**仅用于无声片段或重做音轨** |
| **综合修复** | `ComfyUI-SUPIR` / `SUPIR` | 离线 | 画质天花板高，显存占用大 |
| **细节增强** | `Detail Daemon`、`FreeU` | 离线 | 注入采样过程，增强纹理/结构 |

**默认状态**：`sharpen: 0.2` 已默认开启（内置节点，零依赖，仅轻微去糊）；
`upscale_model` 留空（超分需先自行下载 ESRGAN 模型，填了才启用）。

```yaml
engine:
  comfyui_mmH3:
    post:
      upscale_model: "4x-UltraSharp.pth"   # 留空 = 不超分；填模型名 = 启用超分
      sharpen: 0.2                         # 0~1，0=关闭（出问题时兜底）
```

> 若 ComfyUI 缺少相关节点导致提交报错，把 `sharpen` 改回 `0` 即可退回原始行为。

## 三、安装方式

ComfyUI 自定义节点两种装法，二选一：

1. **ComfyUI-Manager（推荐）**：在 ComfyUI 网页端 `Manager → Install Custom Nodes` 搜索，
   或命令行 `cm-cli install <节点仓库>`。
2. **Git 克隆**到 `ComfyUI/custom_nodes/`：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/AndrewB22/ComfyUI_Frame_Interpolation      # RIFE 帧插值
git clone https://github.com/AUTOMATIC1111/ComfyUI-SUPIR                # SUPIR 综合修复
# SageAttention / xFormers 是 pip 包，不是 custom_nodes
```

超分模型（ESRGAN）下载后放 `ComfyUI/models/upscale_models/`：

- `4x-UltraSharp.pth`（常用，锐利）
- `4x_NMKD-Siax_200k.pth`（平滑，动画友好）

可一键执行 `bash setup_comfy_plugins.sh`（默认装 SageAttention + 拉取推荐 custom_nodes，
`COMFYUI_ROOT` 指向你的 ComfyUI 根目录）。
