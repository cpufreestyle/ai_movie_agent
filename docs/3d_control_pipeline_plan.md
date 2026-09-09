# 连续剧画质/一致性优化：Tripo + Blender + ComfyUI 联合方案

> 目标：解决当前纯 T2V 连续剧「效果一般」的根因——**角色/场景逐镜漂移、镜头语言不受控、时长受限**。
> 思路：把「几何与运动」交给 3D（确定性），把「材质/光影/氛围」留给 AI（创造性）。

> ## ⚠️ 2026-09-09 决策更新（先看这段）
> **1. 不接 Tripo API** —— 账号无积分。资产改为手工流程：
>    Tripo 网页生成 → 下载 GLB → `Blender: File ▸ Import ▸ glTF 2.0` / ComfyUI 侧按需用 3D 节点导入。
>    本文 §四 Step 1 的 `tools/tripo_gen.py` 自动化**已废弃，不做**。
> **2. 3D 路线建议「暂缓」** —— 理由见 §五·补充评估。当前更优先做 **§七 的低成本 A/B**：
>    关 Turbo（4 步 → 30 步）+ 提分辨率（768×448 → 832×480）+ 减镜数（18×3.75s → 12×~5s），
>    **改几个命令行参数、几小时就能出对比**；3D 路线要数天且有已知翻车风险。
>    A/B 仍不满意，再回来做路线 B。

---

## 一、现状诊断：为什么会「效果一般」

当前链路：`18 镜 × MiniMax H3 T2V（尾帧 I2V 续写）→ ffmpeg 拼接 → edge-tts 旁白 + 双语字幕`。

| # | 失效点 | 根因 | 观感表现 |
|---|--------|------|----------|
| 1 | **角色不一致** | 每镜独立采样，没有身份锚点；尾帧续写只传像素不传身份 | Mira 的脸/发型/服装逐镜变化 |
| 2 | **场景不连续** | 同一空间换机位 = 重新想象 | 街道/店内布局每镜不同，空间关系错乱 |
| 3 | **镜头语言失控** | 提示词只能「祈求」机位与运动，无法精确指定焦距/轴线/运镜 | 越轴、构图随意、运镜抖动 |
| 4 | **单镜时长受限** | H3 帧数约束 `17n+5`、显存限制 | 被迫 3.75s/镜，节奏碎 |
| 5 | **物理/交互错误** | 视频模型不会真的做物理模拟 | 走路、手部、遮挡崩坏 |

**关键判断**：1、2、3 是「连续剧」独有的病（单支短片不明显），也正是 3D 最擅长解决的三件事。**4、5 顺带解决。**

---

## 二、工具分工（能力边界已核实 · 2026-09）

### Tripo（云端 API）
- 文生 3D / 单图生 3D / 多视图生 3D
- 后处理：**纹理生成、网格编辑、骨骼绑定（rigging）、动画重定向（retarget）**
- 导出 GLB / FBX / USDZ / OBJ
- 有 OpenAPI（`docs.tripo3d.ai`、`developers.tripo3d.ai`），也可走阿里云百炼同名服务
- **定位：资产工厂**。负责把「Mira / 前主人 / 霓虹街道 / 记忆商店 / 草地 / 那扇门」变成可复用的 3D 模型。

### Blender（本地）
- 精确相机：机位、焦距、运镜、**轴线**、景深
- 场景组装与灯光；绑定角色做动画（走/转头/坐下）
- 输出**控制层**：Z 深度 pass、Freestyle 线稿、可选骨骼投影
- **定位：导演 + 摄影指导**。几何与运动在这里定死，AI 不许改。

### ComfyUI / Wan2.2（本地，已有基础）
- 本机已有 Wan2.2 TI2V-5B（Q8_0 GGUF）+ UMT5-XXL + Wan2.2 VAE
- **已有可用的 ControlNet（已核实）**：`TheDenk/wan2.2-controlnet`
  - `wan2.2-ti2v-5b-controlnet-depth-v1`（**首选**）
  - `-canny-v1` / `-hed-v1` / `-tile-v1`
  - ComfyUI 侧用 **kijai `ComfyUI-WanVideoWrapper`**
  - **关键点**：该 ControlNet 吃的是**控制视频帧序列**（`controlnet_frames`），不是单张图 → **与 Blender 渲染的控制序列天然契合**
- **定位：渲染器**。把 3D 控制序列「上色」成写实影像。

---

## 三、三条路线（按性价比排序）

### 路线 0：零 3D 参与的轻量改进（今天就能做）
不改引擎，只把 Blender/Discord 级的**关键帧图**塞进现有 I2V 通路：
- 用 Tripo 生成 Mira 的**多机位渲染图**（正/侧/背/特写）作为「角色卡」
- 每镜生成时，用 Blender 渲染的该镜**构图参考图**作 `start_image`（`LTXEngine.generate(image=...)` 已支持）
- 收益：一致性明显提升，成本极低
- 局限：仍受 T2V 模型理解力限制，运动仍不可控

### 路线 A：全 3D 渲染成片
Blender 直接渲染 → AI 只做后期（超分/氛围）。
- 一致性 100%、镜头语言专业、任意时长
- 代价：建模/绑定/动画/灯光门槛高，写实角色易陷恐怖谷
- **建议只用于：场景、大远景、载具、建筑**（AI 生成这些最容易崩）

### 路线 B：3D 控制层 + AI 风格化 ★推荐
```
Tripo 生成资产 (GLB)
      ↓
Blender 搭场景 + 设相机/动画
      ↓
渲染控制序列：Depth / Lineart（24fps PNG 序列）
      ↓
Wan2.2 TI2V + ControlNet(depth)  ← 提示词只描述「材质/光影/氛围」
      ↓
成片（几何运动=Blender，质感=AI）
```
- **这是本项目的最优解**：几何与运动 100% 一致，AI 只负责它最擅长的质感
- 每个场景/角色只建一次，之后**换机位零成本**（连续剧的核心收益）
- 单镜时长不再受 `17n+5` 约束，由 Blender 序列长度决定

### 路线 C：分层合成（长期）
3D 渲染背景 + AI 生成角色（alpha/绿幕合成）。
- 最强，但合成工作量和穿帮风险也最高，留到路线 B 跑通后再说。

---

## 四、路线 B 落地步骤

### Step 1 · 资产（Tripo）
从 `outputs/series_bible.json` 提取角色/场景描述 + 已有关键帧图作为输入：

| 资产 | 输入方式 | 备注 |
|------|----------|------|
| Mira（主角） | 图生 3D（用现有 Mira 关键帧）+ 多视图 | 必须开 rigging |
| 前主人 | 文生 3D + rigging | 第 3 集关键 |
| 记忆商店内景 / 霓虹街道 / 草地 / 那扇门 | 文生 3D → 场景件 | 不需要绑骨 |
| 关键道具（发光小瓶、芯片、椅子） | 文生 3D | 小物件，Tripo 质量好 |

落地（**手工，不接 API**）：
1. 从 `outputs/series_bible.json` 抄角色/场景描述，或把已有关键帧图丢进 **Tripo 网页版**生成
2. 需要动画的角色开 **rigging**；纯场景件不必
3. 下载 **GLB**，统一放 `assets/tripo/`，文件名带角色/场景名（如 `mira_rigged.glb`）
4. Blender 里 `File ▸ Import ▸ glTF 2.0` 导入，另存进 `blender/lib/`
5. **在 `assets/tripo/MANIFEST.md` 手工登记**：资产名 / 来源提示词或原图 / 生成日期 —— 没有 API 就没有 task_id 可追溯，必须靠这个表复现

> 注意：无 API 意味着**资产无法批量重生成**。所以资产要「一次做对」，优先做跨集复用的（Mira、前主人、街道、商店、草地、门）。

### Step 2 · Blender 工程
```
blender/
  lib/            # 导入的 GLB（符号链接到 assets/tripo）
  scenes/ep1.blend … ep3.blend
  shots/          # 每镜：一个 Camera + 关键帧
  export/ep{N}/shot{M:02d}/  # 控制序列 PNG（depth_0001.png …）
```
约定（**必须和引擎参数对齐**）：
- 分辨率：`832×480`（Wan2.2 常用档；当前 768×448 也可，但 832×480 是更标准的 latent 对齐尺寸）
- 帧率 **24fps**；单镜帧数建议 **97 帧 ≈ 4.04s**（可自由调整，不再受 17n+5 限制）
- Pass：
  - **Depth（Z pass）** — 主力控制信号，`Mist`/`Z` pass 输出，或直接用 Compositor 归一化成灰度
  - **Lineart（Freestyle）** — 角色轮廓强化，和 depth 叠加用（ControlNet weight 建议 depth 0.8 / lineart 0.3~0.4）
- 相机：每镜一个 Camera 对象，命名 `cam_shot01`，用关键帧做推/拉/摇；**明确设定轴线**避免越轴
- 渲染设置：控制层不需要真实光照 → 用 **Workbench/Eevee + 无材质** 渲染，速度极快，几乎不占显存

输出：每镜一个目录的 PNG 序列（depth / lineart 各一份）。

### Step 3 · ComfyUI 接入
1. 装节点：`git clone https://github.com/kijai/ComfyUI-WanVideoWrapper` → `D:/ComfyUI/custom_nodes/`（装完重启 ComfyUI）
2. 下权重（HF，走代理 7897）：
   - `TheDenk/wan2.2-ti2v-5b-controlnet-depth-v1` → `E:/ComfyUI_models/controlnet/`
   - （可选）`-hed-v1`、`-canny-v1`
3. 搭 workflow：`workflows/wan22_ti2v_control_depth_api.json`
   - 主模型：`Wan2.2-TI2V-5B-Q8_0.gguf`（已在 `E:/ComfyUI_models/diffusion_models`）
   - 文本编码器：`umt5xxl-encoder-q5_k_m.gguf`（已有）
   - 控制输入：Blender 导出的 depth PNG 序列
   - 建议参数：`controlnet_weight=0.8`、`guidance_start=0.0`、`guidance_end=0.8`、`stride=3`、步数 30~50

### Step 4 · 引擎接入（本项目代码）
新增与 `MMH3Engine` 同接口的引擎，复用现有三集编排：
- 新增 `agent/wan22_control_engine.py`（`Wan22ControlEngine`，实现 `generate(prompt, out, image=..., control_dir=...)`）
- `config.yaml` 加 `engine.comfyui_wan22ctl`（api / workflow / 分辨率 / 帧数）
- `cli.py` 加 `wan22ctl` 子命令（参数：`--prompt --control --frames --resolution`）
- `run_series.py` 加 `--engine wan22ctl`：读 `blender/export/ep{N}/shot{M:02d}/` 作为每镜控制序列
- `run_series.py` 的分镜提示词改造：**只写「材质/光影/氛围/表演情绪」**，几何与运动描述删掉（否则和 ControlNet 打架）

### Step 5 · 后期
不变：`make_narration.py`（镜头块对齐旁白 + 双语字幕）继续复用。
建议同时把分辨率提到 832×480 甚至 960×544（16GB 下 832×480 安全）。

---

## 五、风险与坑（务必先看）

| 风险 | 说明 | 应对 |
|------|------|------|
| **5B 棋盘伪影** | ControlNet 作者明确说 *"Currently, chess artifacts are observed in the 5B model inference"* | 先跑单镜验证；若明显，降 `controlnet_weight` 到 0.5~0.6，或改用 `-hed`（边缘更软）/ `-tile`；严重则退回路线 0 |
| **GGUF 主模型 + fp16 ControlNet 兼容性** | 本机 Wan2.2 是 GGUF Q8，ControlNet 是 fp16/bf16 | kijai wrapper 通常支持混装，但**必须实测**；不兼容则需下 fp16 版 TI2V-5B（约 10GB，16GB 显存吃紧，要开 lowvram） |
| **显存** | 5B + ControlNet + VAE + UMT5 在 16GB 上偏紧 | 控层渲染几乎不占显存（Blender 可与 ComfyUI 分时跑）；ComfyUI 侧开 `--lowvram` + teacache（`threshold 0.6`） |
| **Tripo 无积分** | API 不可用，只能网页手工生成、手工导入 | 资产**无法批量重生成** → 必须先做低成本 A/B 确认方向，再投入手工建模；否则白做 |
| **角色绑定质量** | Tripo auto-rig 对人形尚可，动画仍需 K 帧 | 第 1 轮先做**简单动作**（站立/转身/走），复杂表演仍交给 AI |
| **工期** | 资产 + Blender 分镜是新增工作量 | 见下节 MVP：先 1 镜验证，别一上来铺 54 镜 |

---

### 补充评估：为什么建议「暂缓 3D 路线」

| 维度 | 低成本 A/B（§七） | 3D 路线（§四 路线 B） |
|------|------------------|----------------------|
| 工作量 | 改命令行参数，**几小时** | 建模 + 分镜 + 调参，**数天** |
| 前置依赖 | 无 | Tripo 手工资产（无 API，不可批量重做） |
| 失败风险 | 几乎为零 | 5B 棋盘伪影 + GGUF/fp16 混装未验证 |
| 解决「角色漂移」 | 部分（只能靠首帧锚定） | 根本解决 |
| 解决「镜头语言」 | 不能 | 根本解决 |
| 解决「画质偏软」 | **能**（步数/分辨率是主因） | 间接改善 |

**结论**：当前「效果一般」最可能的三个主因是 **Turbo 4 步采样太激进、分辨率偏低、18 镜切太碎**——全是参数问题。
先花几小时 A/B 掉这三件事，如果画质和观感已经够用，3D 就不用上了；
如果**一致性**仍是硬伤（脸认不出是同一个人），那才是 3D 真正该出场的信号。

---

## 六、MVP 验证计划（建议先做这个）

**目标**：用 1 个镜头证明「3D 控制 → AI 上色」链路可用且一致性提升。

1. **选镜**：第 1 集 shot01（Mira 走进霓虹街道，中景，缓推）
2. **Tripo**：只生成 `Mira`（图生 3D + rigging）+ `霓虹街道`（文生 3D）
3. **Blender**：搭一个最简场景，一台 Camera 做 4s 缓推，渲染 depth 序列（97 帧 @24fps / 832×480）
4. **ComfyUI**：装 WanVideoWrapper + 下 depth ControlNet，跑单镜
5. **验收标准**（三条全过才算成功）：
   - 构图/机位/运镜与 Blender 一致（不是「大致像」）
   - 无棋盘伪影，或降到可接受
   - 同一角色换机位再渲一镜，**脸和服装能认出是同一个人**
6. **通过** → 铺开到 18 镜；**不通过** → 退回路线 0（关键帧 I2V），或换 `-hed` / `-tile` 控制类型重试

---

## 七、不做 3D 也能立刻改善的几件事

如果暂时不想碰 3D，这几项性价比很高：

1. **角色卡 + IP-Adapter / 参考图**：每镜带一张 Mira 固定参考图，一致性立刻提升
2. **提高分辨率与步数**：768×448 → 832×480；H3 Turbo 4 步 → 8 步（`--no-turbo`），画质明显变好，代价是时间翻倍
3. **拉长单镜 + 减少镜数**：18 镜 × 3.75s 太碎；改 12 镜 × 5s，节奏更稳、漂移机会更少
4. **负向提示词强化**：把「deformed face, inconsistent identity, extra limbs」等写全
5. **后期统一调色**：ffmpeg 加 LUT / 颗粒 / 暗角，掩盖模型质感差异（`make_narration.py` 渲染阶段加滤镜链）

---

## 附：核实来源
- Tripo OpenAPI 文档（模型生成 / 纹理 / 绑骨 / 动画重定向 / 格式转换）：`docs.tripo3d.ai`
- Wan2.2 ControlNet 权重与用法：`github.com/TheDenk/wan2.2-controlnet`
- ComfyUI 集成节点：`github.com/kijai/ComfyUI-WanVideoWrapper`
