# MiniMax H3 × Blender 白模（blocking）使用指南

> 结论先说：**AI 只负责它不擅长的部分**。几何与运动交给 Blender 白模（确定性），
> 材质/光影/氛围交给 H3。本文说明「什么任务适合用白模喂 H3」以及本项目里对应的开关与参数。
>
> 适用引擎：`engine.backend = comfyui_mmH3`（`agent/mmh3_engine.py`）。
> 白模侧实现见 `agent/blocking.py`，接线见 `agent/agent.py` 的 `generate_one_scene`。

---

## 一、任务 × 预期 × 建议（已按本项目代码核实）

| 任务 | H3 用白模的表现预期 | 本项目的建议做法 |
|---|---|---|
| 人物走、跑、转身、跳跃、舞蹈 | **较好**。白模提供清晰的动作轨迹与重心变化 | 白模需有完整肢体轮廓、避免自遮挡。控制图（depth/normal/line）走 `use_as_ref_images` |
| 镜头环绕、推拉、跟拍 | **较好**，特别适合做运镜参考 | 先在白模里做出目标镜头路径，再用灰模 mp4 走 `use_as_ref_video`；机位 `static` 的镜头不产生灰模动画 |
| 二次元角色动画 / OC PV | **好**。图锁角色 + 视频锁动作 | 角色设定图（`--anchor` / `ref_images`）+ 白模动作视频一起喂。注意 **H3 没有负向提示词**，"负向强化"对它无效，压崩坏只能靠多加参考图 |
| 产品展示、UI/UX、游戏界面动效 | **很适合** | 需把"产品不变、结构不变"写进提示词；静态结构优先用 `ref_images`（比 `ref_video` 更稳） |
| 复杂武打、多人交互、道具接触 | **可尝试，失败率较高** | 拆成 3–6s 的单一动作镜头再后期拼接。见 §二 帧数换算 |
| 长剧情、连续多镜头、角色跨段绝对一致 | **不适合一键完成** | 按镜头生产，保留每镜的参考素材与成功 seed/提示词。本项目已自动落盘（见 §五） |

**一句话选型**：动作/运镜/空间关系要"定死"→ 用白模喂 H3；只是想要好看 → 纯 T2V/I2V 更省事。

---

## 二、H3 的硬约束（决定了你能怎么拆镜）

### 1. 帧数只能落在 `17n+5` 网格

`agent/mmh3_engine.py` 里 `LEN_BASE = 5`、`LEN_STEP = 17`，传入非法值会被**向上吸附**：

| n | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| 帧数 | 5 | 22 | 39 | **56** | **73** | **90** | **107** | **124** | **141** | 158 |
| @24fps | 0.21s | 0.92s | 1.63s | **2.33s** | **3.04s** | **3.75s** | **4.46s** | **5.17s** | **5.88s** | 6.58s |

- 当前默认 `engine.comfyui_mmH3.num_frames = 56`（2.33s/镜）。
- **"3–6s 单一动作镜头" = 73 / 90 / 107 / 124 / 141 帧**。
- 分辨率必须被 **32 整除**，同样会被自动修正。

### 2. 参考视频是官方 2~15s 策略（实测：按「帧数」判定 48~360 帧）

条件节点输入 `reference_video_policy: "official_2_to_15s"`。**2026-09-11 实测确认约束是帧数**，
越界时条件节点 `MiniMaxH3AudioConditioningT8` 直接抛错、整镜生成中止：

```
ValueError: ref_video_1 has 24 frames; official guidance is 48-360 frames at 24fps
```

→ 即 **48 ~ 360 帧 @24fps = 2.0s ~ 15.0s**（下界含 2.0s，正好 48 帧）。
> 复现脚本：`_exp_ref_video.py <ref.mp4> --force`（`--force` 绕过时长闸门、探测节点真实反应）。

项目做了两层保护：

- `blender.anim_frames: auto`（默认）→ 自动对齐出片帧数，并强制 ≥ `2s × fps`（24fps 下 = 48 帧）；
  **写死整数则按原值使用、不做兜底**（尊重显式配置，越界由下一层拦）；
- `agent/agent.py` 传 `ref_video` 前预检时长，`<2s` 则**跳过并打印原因**，保证本镜照常出片
  （而不是整个镜头因参考视频被拒而失败）。

### 3. 参考图上限 9 张，且 Hybrid 下身份会被运动信号压过

- `ref_images` 最多 9 张，`ref_image_size: "match"` 自动匹配尺寸。
- `image`(首帧) + 任意参考媒体 → 走 **Hybrid**；只有 `image` → 走 **I2VA**；
  而 **I2VA 禁止携带任何参考媒体**（节点会直接抛错，代码里已用 `has_any_ref` 规避）。
- **实操经验**：Hybrid 下只靠 1 张首帧锁形象时，参考视频的运动信号会把人物形态压崩；
  补 2–3 张同角色参考图（正/侧/半身）能显著改善。

### 4. H3 没有负向提示词

`config.yaml` 的 `prompting.negative`（含 `quality`/`identity` 预设）**对 H3 不生效**
——H3 走 flow matching，BasicGuider 只有 model + conditioning。别指望 negative 治崩坏，
那条路只对 LTX 之类有效。

---

## 三、三种喂法与本项目开关

| 开关（`config.yaml` → `blender`） | 传入 H3 的字段 | 作用 | 默认 |
|---|---|---|---|
| `use_as_i2v_start` | `image`（首帧） | 锁构图 / 站位 / 机位 | `false` |
| `use_as_ref_images` | `ref_images`（depth/normal/line） | 锁结构与形体 | `false` |
| `use_as_ref_video` | `ref_video`（灰模运镜 mp4） | 锁走位与镜头运动 | `false` |

三个开关**默认全 false**（与旧行为一致，避免无意中改变出片效果）。按需开启：

```yaml
blender:
  anim_frames: auto        # 自动对齐出片帧数，并兜底 2s（H3 参考视频下限）
  use_as_i2v_start: true   # 建议开：白模 previs 作首帧，构图最稳
  use_as_ref_images: true  # 建议开：depth/normal/line 锁结构
  use_as_ref_video: true   # 有运镜需求再开：喂灰模 mp4 锁走位
```

`agent/agent.py` 用 `inspect.signature` 判断引擎是否支持这些参数，
SkyReels / LTX 不支持时**自动跳过且不报错**。

### 推荐组合

| 目标 | 推荐组合 |
|---|---|
| 只要构图稳 | `use_as_i2v_start` |
| 角色一致性优先（OC PV） | `use_as_i2v_start` + `use_as_ref_images` + 2–3 张角色参考图 |
| 运镜/走位必须精确 | `use_as_ref_video`（+ 首帧） |
| 产品/UI 结构不变 | `use_as_ref_images`（静态结构比视频更稳） |

---

## 四、按任务类型的参考配方

| 场景 | num_frames | 白模喂法 | 备注 |
|---|---|---|---|
| 单人走/转身/舞蹈（单动作） | 56–73 | 首帧 + 控制图（+ 灰模视频） | 白模肢体要完整、无自遮挡 |
| 复杂武打 / 多人交互 | 73–124，**拆 2–3 镜** | 每镜单独出灰模视频 | 拆短镜是唯一稳妥解，后期 `concat_shots` 拼 |
| 环绕 / 推拉 / 跟拍 | 56–90 | **必须** `use_as_ref_video` | 白模里先做出目标镜头路径 |
| 产品 / UI 动效 | 39–73 | 控制图为主 | 提示词写明"结构不变、产品不变" |
| 长剧情连续多镜头 | 逐镜独立 | 每镜保留素材 | 见 §五 可复现 |

---

## 五、可复现：每镜参数自动落盘

「按镜头生产」的前提是每镜都能复现，本项目已经把两件事做好：

1. **`series_manifest.json`**：每镜 key → 成片路径（`run_series.py`），已生成的镜自动跳过。
2. **`gen_params.json`**：`agent/record.py` 记录该镜的
   `engine / resolution / num_frames / fps / steps / lora / seed / attempt / prompt /
   image / ref_images / ref_video / style_anchor / qa`。
   - 白模的**参考图与灰模动画都会入档**（`ref_images` / `ref_video`），失败镜可照着复现。
   - `agent/agent.py`（概念片通路）与 `run_series.py`（三集通路）都会写这份档。

**seed 规则**（`run_series.py`）：

- 基准：`BASE_SEED + ep * 1000 + idx` → 同镜同 seed，可复现；
- 质检不达标自动重 roll：`+ attempt * 7919`（确定性换 seed，失败样本可回查）；
- 变体文件名带 seed（`ep1_shot1_s77`），便于 A/B。

---

## 六、常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 白模条件没生效 | 三个开关默认 false | 打开 `blender.use_as_*`，日志会打印「白模条件已接入引擎: ...」 |
| 提示「跳过 ref_video：灰模动画 x.xxs 低于 2s 下限」 | 灰模动画过短 | `blender.anim_frames: auto` |
| 人物形态崩坏 | Hybrid 下身份信号被运动压过 | 加 2–3 张同角色 `ref_images`；**别用 negative** |
| 报错 "I2VA cannot include reference media" | 只给了首帧却带了参考媒体 | 已由 `has_any_ref` 规避；若自造工作流需自行保证 |
| 帧数/分辨率被"改"了 | 自动吸附到 `17n+5` / 32 整除 | 正常行为，日志会提示吸附后的值 |
| 日志写 `I2VA`，但明明给了参考素材 | `mmh3_engine.generate` 的提交日志只看 `image` 有无，**不反映真实 task_type** | 以干跑输出里的 `task_type=Hybrid` 为准，别被那行日志误导 |
| `ValueError: ref_video_1 has 24 frames; official guidance is 48-360 frames` | 参考视频帧数 <48（<2s） | `blender.anim_frames: auto`；或把该镜的 `use_as_ref_video` 关掉 |
| 单镜时长与预期不符 | 用 `num_frames / fps` 算，注意吸附 | 见 §二 换算表 |

---

## 附：相关文件

- `agent/mmh3_engine.py` — H3 工作流拼装（约束吸附、Hybrid/I2VA 判定、ref 注入）
- `agent/blocking.py` — 白模渲染（previs / depth / normal / line / 灰模动画）
- `agent/agent.py` — 白模资产 → 引擎的接线（含 ref_video 时长预检）
- `agent/record.py` — 每镜参数落盘
- `config.yaml` → `blender` / `engine.comfyui_mmH3` — 开关与出片参数
- `docs/3d_control_pipeline_plan.md` — 3D（Tripo + Wan2.2 ControlNet）路线的整体规划
