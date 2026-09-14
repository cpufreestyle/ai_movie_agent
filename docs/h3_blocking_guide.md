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
| 镜头环绕、推拉、跟拍 | **实测不成立（见 §七）**：H3 未跟随白模运镜，画质反而变差 | 目前**不要开** `use_as_ref_video`；运镜仍靠提示词，或走 3D 控制层路线（`docs/3d_control_pipeline_plan.md`） |
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

## 七、实测结论：白模 ref_video 目前是负作用（2026-09-11）

同 seed、同首帧、同 3 张控制图（depth/normal/line），只切换 `use_as_ref_video`：

| 组 | 耗时 | 平均光流 dx / dy | mag | 画面 |
|---|---|---|---|---|
| **带** ref_video（真灰模 `anim_track`） | **365s** | -0.0089 / +0.0306 | **0.123** | 人脸生硬、构图漂移 |
| **无** ref_video（对照：首帧 + ref_images） | **67s** | -0.0669 / +0.0003 | **0.299** | 自然、接近原角色 |
| **仅首帧**（无 ref_video、无 ref_images，I2VA） | **60s** | -0.0405 / -0.1496 | 0.273 | 与上一组相当，都自然 |
| 灰模参考视频自身 | — | +0.0366 / -0.0014 | 0.261 | 纯几何白模（圆柱+球）、无光照、1280×720 |

**结论**：

1. **接线没问题**——Hybrid 生效，`ref_videos.ref_video_0` 与 `ref_images.ref_image_0..2` 全部进入条件节点。
2. **但"锁走位"不成立**：参考视频是 +dx（右移），出片 ≈0 甚至反向；
3. **反而有害**：运镜被压制（mag 0.123 vs 对照 0.299）、人脸/构图一致性变差、耗时 5.4 倍。
4. 因此 `config.yaml` 的 `use_as_ref_video` **默认关闭**（含 A/B 数据注释）。
5. **`ref_images`（depth/normal/line）本次未见明确收益**：运动与画质都与"仅首帧"组相当
   （67s vs 60s），成本很小但也没看出好处。
   ⚠️ **注意**：本测试用的控制图来自与镜头**无关**的通用白模几何（圆柱+球），
   语义不匹配本身就可能抵消收益；要真正评估，必须用「与该镜 prompt 匹配的白模 spec」
   重新渲染控制图后再比（`python _exp_ref_video.py --no-ref-video` vs `--no-ref-video --no-ref-images`）。
6. **唯一稳定的有效杠杆是「首帧」**：三组都带首帧（普通关键帧，**不是**白模 previs），构图与角色均在位。
   注意 `use_as_i2v_start` 是拿白模 previs 当首帧、会让画面变白模，属另一回事，本次未测。

**推断原因（未证实）**：1280×720 无光照的抽象几何与写实场景语义冲突，H3 折中成"弱运镜 + 差画面"；
H3 的 `ref_videos` 更像内容/结构参考，而非运镜迁移。

**要再试怎么办**（必须做同 seed 对照，别只看单条片子）：

```bash
python _exp_ref_video.py outputs/blocking/anim_track/blocking.mp4   # 带 ref_video
python _exp_ref_video.py --no-ref-video                             # 同 seed 对照
# 脚本会打印两组 motion 指标（cv2 Farneback 平均光流）与抽帧对照
```

> 改进方向：想让白模真正锁住运镜，走 `docs/3d_control_pipeline_plan.md` 的 **3D 控制层 + ControlNet**
> 路线（控制信号是逐帧姿态/深度，而不是"参考视频"这种弱约束）。

---

## 八、2026-09-14 优化：白模构图帧作 I2V 首帧（推荐落地）+ 走位方向实测

### 推荐落地（白模负责构图/站位，H3 负责渲染）
白模先在 3D 里确定性地摆好角色站位/走位起点与机位，渲染一张**构图帧**（`gen_blocking.py --previs-out`，
无头 CYCLES 即可，不依赖 Blender GUI/MCP），把它作 H3 的 `image`(I2V 首帧) 锁构图/站位/机位，
再把角色锚定图(Mira)作 `ref_images`(Hybrid) 给身份，H3「渲染」成实拍。
脚本：`run_blocking_i2v.py`（含 `--ab` 内置对照）。`agent.py` 的 `use_as_i2v_start` 也已补「白模首帧时
自动喂 Mira 锚定图作 ref_image」，防止 H3 把灰模渲染成灰色角色。

### 真机 A/B（768×448 / 56 帧 / Turbo；walk=-3,0:3,0 即"白模左→右走位"，灰模基准 dx=+0.367 右移）
| 组 | dx（横向） | mag | 耗时 | 任务类型 |
|---|---|---|---|---|
| 灰模基准（白模想表达的走位） | +0.367（右） | 0.37 | — | — |
| 对照组：Mira 锚定图作首帧（当前生产默认） | −0.773（左） | 1.23 | 54s | I2VA |
| 测试组：白模构图帧首帧 + 锚定 ref | −0.451（左） | 0.82 | 56s | Hybrid |
| 测试组 + ref_video（角色走位灰模） | −0.386（左） | 0.71 | 95s | Hybrid |

### 结论（重要，与"白模负责走位"诉求直接相关）
1. **首帧(I2V-start)只能弱锁"起始构图/站位"**：测试组首帧最亮列从对照的 0.64W 左移到 0.48W，
   说明白模把角色往左带了一点；但整体质心仍在 0.50W（H3 仍把角色渲染在画面中部附近），
   并非"角色精确落在白模指定坐标"。→ 构图可偏弱引导，**不能精确定位**。
2. **走位方向完全不被 H3 接受**：白模意图右移(dx+0.37)，但三组出片**全都左移**(dx<0)，
   连"角色走位灰模作 ref_video"也左移且更慢(95s vs 55s)。→ **白模的运动信号被 H3 自身先验覆盖**，
   既靠不住首帧、也靠不住 ref_video 把走位方向传给 H3。
3. **ref_video（含角色走位灰模）确认无效且更慢**：方向不传、mag 未增、耗时 +73%，
   与 §七"相机运镜灰模"结论一致 → **彻底弃用 ref_video**（之前的失败不是因为灰模是"通用几何"，
   而是 H3 根本不接收白模运动）。

### 要真正"白模负责走位"该走哪条路
H3 的 `ref_video` 是「弱内容/结构参考」，不是运动迁移。要逐帧锁住角色姿势/位移，必须上
**稠密逐帧条件**：每帧的 depth/normal/pose 经 **ControlNet** 注入（见 `docs/3d_control_pipeline_plan.md`
的 3D 控制层路线）。该路线与"参考视频"是两套机制——后者已证无效，前者尚未在本项目接入 H3。
> 当前 I2V-start 路径（白模构图帧 + 锚定 ref）的价值是**稳定构图/起始站位 + 身份一致**，
> 适合"画面别乱飘、角色别乱跑"的保底需求；真正的走位编排需另接 ControlNet。

---

## 九、2026-09-14 突破：H3 原生 Fun Control 能控制走位方向

§八 说"H3 的 ref_video 传不动走位、真正的走位需另接 ControlNet"——**该能力已原生存在于 H3 内**：
MiniMax H3 自带 **Fun Control** 节点（`MiniMaxH3FunControlLoader/ApplyT8Advanced`），把逐帧的
depth/pose/edge 控制视频**注入 DiT**（第 0/10/20/30/40 层）。这是真正的「运动迁移」，与 ref_video 是两套机制。

### 落地
- 控制权重：`minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors`（2.3GB，单权重支持
  Canny/Depth/HED/MLSD/Pose）。本机 HF 被封，改从 **ModelScope `Comfy-Org/MiniMax-H3`** 拉取（`download_ms.py`）；
  落盘 `E:/ComfyUI_models/model_patches/`（T8 loader 同时查 `controlnet`+`model_patches`）。
- 白模 depth 序列：`gen_blocking.py ... --depth`（相机视距经 MapRange 渲成"近白远黑"灰度；背景/地面压黑，
  只留角色 → 干净的 depth/silhouette，角色屏幕位置即走位信号）。
  - ⚠️ **走位必须落在相机视野内**：`static` + `--no-track` 相机固定在 (0,-7)，角色深度处水平可见约 **±2.6**。
    `--walk -3,0:3,0`（±3）会让角色在**起末帧出画**（实测仅 46/56 帧可见，起末段控制信号丢失）；
    改成 **±2.2**（`--walk -2.2,0:2.2,0`）即 **56/56 帧全程可见**（屏幕 x 0.07W→0.93W）。
    要更大范围走位就把相机拉远（更大 |cam Y|）或换 `--cam lateral`。
  - **多点折线走位**：`--walk` 支持 `x1,y1:x2,y2:x3,y3:...`（按累计弧长**匀速**分配时长）。例：闭环复杂走位
    `--walk -2,1:2,1:2,-1:-2,-1:-2,1`（俯视矩形循环 + 前后景深变化：角色屏幕面积 2.8%→5.2%，56/56 帧全程可见）。
- 接线：`run_h3_funcontrol.py` 把 `FunControlApply` 插在 LoRA 后、采样前（`MODEL`/`CONDITIONING` 经它再进 guider）；
  控制视频用 `VHS_LoadVideo`（`custom_width/height`+`frame_load_cap` 锁成 768×448×56，`fit_mode=exact`）。
- **主线集成（生产可用）**：`agent/mmh3_engine.py` 已内建 Fun Control——
  `generate(control_video=<mp4>, fc_strength=…)` 逐镜传入，或 `config.yaml → engine.comfyui_mmH3.fun_control`
  （`enable/control_net/control_kind/fit_mode/strength/end_percent/video`）全局开启。
  引擎自动插节点 41/42/43（Loader / `VHS_LoadVideoPath` / Apply）并把 guider 的 MODEL/CONDITIONING 改接 Apply 输出，
  与首帧/ref_images/BlockCache/二采/post 共存（节点 ID 已避让）。真机验证：引擎路径 net_shift **+223.7px（右移）**。
- **全链路接入（`agent/agent.py` + `agent/blocking.py`，主管线可用）**：`config.yaml → blender.use_as_fun_control: true` 后，
  `BlockingGenerator.render_assets` 每镜额外产出 **`fcvideos`**（白模走位 depth 控制序列 → `fc.mp4`），
  `generate_one_scene` 自动把它作 `control_video` 喂引擎。走位来源：`blender.fun_control_walk` 默认归一化走位，
  分镜文本里的"从左到右 / 走近镜头 / 来回 / 绕圈"会被 `parse_spec` 识别并**逐镜覆盖**。
  走位用**归一化坐标**（±1=画面左右），`_FC_TAIL` 模板按镜头距离自动换算世界坐标并留 20% 边距 →
  **自动适配镜头、角色全程不出画**；控制序列分辨率/帧数严格对齐出片（`fit_mode=exact`，帧数吸附 17n+5）。
  需 Blender + BlenderMCP(9876) 运行；强度由 `blender.fun_control_strength`（0.8~1.2）传给引擎。

### 真机 A/B（768×448 / 56 帧 / Turbo / seed 12345；控制视频=白模角色 0.10W→0.90W 左→右走）
| 组 | trend(px/帧) | net_shift(px) | 画面活跃度 | 判定 |
|---|---|---|---|---|
| 基线（无 FunControl） | −0.226 | −10.9 | 1.98 | H3 默认**左移** |
| FunControl depth（strength 0.8） | **+0.139** | **+13.7** | 2.14 | **跟随白模右移** ✓ |
| FunControl depth（strength 1.5） | −1.388 | −5.7 | **13.33** | 过强 → 画面崩坏 |

### 结论
1. **Fun Control 能把走位方向从"H3 默认左移"纠正为"跟随白模右移"**（基线 net −10.9 → control net +13.7），
   这是 `ref_video`/首帧都做不到的 —— 白模的运动**确实进入了** H3。
2. **strength 是主要杠杆**（同 seed 12345，控制视频锁 768×448×56）：
   | strength | end_percent | net_shift | 画面活跃度 act | 说明 |
   |---|---|---|---|---|
   | 0.8 | 0.85 | +13.7px | 2.14 | 画面干净，走位弱 |
   | 1.2 | 1.0 | **+37.1px** | 10.78 | 走位明显增强；画面较活跃（部分是真实大运动，需目视确认无伪影） |
   | 1.5 | 0.85 | −5.7px | 13.33 | 过强 → 崩坏、走位失稳，**勿用** |
   → 推荐先试 **0.8（保守）／1.2（强）**，按画面质量取舍；**≥1.5 崩坏**。增强走位还可：让白模角色在画面里更大（控制信号更强）、或 `control_kind` 换 edge/pose。
3. **幅度仍远小于白模意图**（control 仅 net +13.7px vs 白模 +614px）：H3 目前只做"轻微右移"。要更强逐帧走位，
   需继续调：更高分辨率/更长镜、`control_kind` 换 pose/edge、多控制叠加、或调采样步数/guidance。
4. 至此"白模负责走位、H3 负责渲染"**方向可行**：构图/起始站位走 §八 的 I2V-start，**走位方向走 §九 Fun Control**。
5. **走位幅度 × 视野**（seed 12345 / strength 1.2 / end 1.0）：角色**出画**（walk ±3，46/56 帧可见）→ net +37.1px / act 10.78；
   **全程可见**（walk ±2.2，56/56 帧）→ net +21.2px / act 9.42。两者都成功右移（方向正确）；出画版幅度更大是因角色扫过整幅
   （0→1.0W），但起末 10 帧控制信号丢失（H3 靠先验补）；全程可见版控制信号完整、画面略稳 → **推荐让走位全程可见（±2.2）**，
   要更大幅度应**拉远相机**（保持可见），而非让角色出画。
   > 度量口径：以 **net_shift**（帧差前景净位移）为主；光流 `trend` 在大运动下不稳（本次出现 dx 正 / trend 负的矛盾即为例）。
6. **复杂走位（闭环往返）也能跟随**：白模 `-2,1:2,1:2,-1:-2,-1:-2,1`（俯视矩形循环 + 景深变化）→ H3 出片屏幕 x 亦呈
   **先增后减的往返**（argmax 在中部，首末 1/4 均值相近 367≈372 px，闭环），与白模轨迹形态一致（幅度更小）。
   → Fun Control 传递的不只是单向位移，而是**逐帧运动结构**。
7. **主管线真机端到端（目前最佳落地）**：`blender.use_as_fun_control: true` 走完整链路
   （`BlockingGenerator` → BlenderMCP 渲染**归一化走位** depth 序列 → `generate_one_scene` 作 `control_video` → H3 Fun Control）：
   - 白模控制序列：归一化 ±1 自动映射为屏幕 **0.10W→0.90W**，**56/56 帧全程可见**、匀速、留 10% 边距（镜头自适应）。
   - H3 出片：**trend +10.8 px/帧 ≈ 白模意图 11.2 的 97%**；**net_shift +281px**（vs 出画版 +37px，**7.6×**）；
     画面活跃度 act 6.48（比 ±3 出画版的 10.78 更稳）。
   → **"白模负责走位、H3 负责渲染"达到可用水平**：走位幅度、方向、构图均由白模确定性控制。
   > 修复两处真机坑：① `_common` 统一把 `out_dir` 转**绝对路径**（Blender 把相对路径解析到它自己的 cwd →
   > 静默"无产物"）；② `_FC_TAIL` 走位改**绝对定位**（原"相对起点"会让角色整体偏移并冲出画面）。

## 附：相关文件

- `agent/mmh3_engine.py` — H3 工作流拼装（约束吸附、Hybrid/I2VA 判定、ref 注入）
- `agent/blocking.py` — 白模渲染（previs / depth / normal / line / 灰模动画）
- `agent/agent.py` — 白模资产 → 引擎的接线（含 ref_video 时长预检）
- `agent/record.py` — 每镜参数落盘
- `config.yaml` → `blender` / `engine.comfyui_mmH3` — 开关与出片参数
- `docs/3d_control_pipeline_plan.md` — 3D（Tripo + Wan2.2 ControlNet）路线的整体规划
