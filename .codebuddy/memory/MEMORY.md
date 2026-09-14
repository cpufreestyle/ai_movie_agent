# 长期记忆 (MEMORY.md) — ai_movie_agent

## 环境
- GPU: RTX 5070 Ti 16GB (sm_120)。ComfyUI `D:/ComfyUI` 常驻 8188 (venv python; GGUF+LTXVideo+T8/VideoHelperSuite)。
- 模型根 `E:/ComfyUI_models/` (extra_model_paths.yaml，E/D 都查)。代理 127.0.0.1:7897；访问本机 ComfyUI 须 NO_PROXY/ProxyHandler({}) 防 502。
- cu130 必须 (cu128→CUDA 禁用→latent 全噪)。venv: `d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe`。终端 PowerShell；长任务 Start-Process 后台 + Get-Content 轮询。

## SageAttention (2026-09-10)
- wheel: HF `ussoewwin/Sage-Attention-for-Windows` (cu130torch2.11.0-cp313, SA2/SA3)，装须 `--no-deps`；另 `pip install triton-windows`。
- L≤512 比 SDPA 慢 3~4x，勿用于短序列。H3 走全局 optimized_attention：SA2=`--use-sage-attention`；SA3=改 attention.py + `launch_comfy.py --sage3`。
- 生产推荐 SA2 (cos .999 近无损, 86.6s)；SA3 慢~8% 且 cos .981 或劣化人脸。SA2 可常态化 (COMFY_SAGE=1)。

## 出片引擎 (16GB)
- LTX-2.5 (Q2_K+gemma4-12b int8): `cli.py ltx`, `agent/ltx_engine.py`。真 T2V+音频。
- Wan2.2 TI2V-5B Q8: 仅 I2V 无音轨，必传 start_image、负向必填。
- **MiniMax H3 (主力)**: INT4 档；须 comfyui-minimax-h3-audio-T8 + VideoHelperSuite。Turbo LoRA→48s 出 768×448/2.33s/立体声。入口 `agent/mmh3_engine.py`+`cli.py mmh3`+`run_series.py --engine mmh3`。引擎自动修 32整除/17n+5 吸附。组件勿跨模型混搭 (H3 UNet 须配 H3 TE 输出5120)。

## H3 白模控制走位
- 首帧/ref_video 路线已证无效 (2026-09-14): 首帧只弱锁起始构图/站位；连"角色走位灰模 ref_video"也左移且慢1.7× → ref_video 彻底弃用。
- **✅ H3 Fun Control = 真正能控走位 (2026-09-14 实测突破)**: H3 原生节点 `MiniMaxH3FunControlLoader/ApplyT8Advanced` 把逐帧 depth/pose/edge `control_video` 注入 DiT(第0/10/20/30/40层)。
  - 权重 `minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors`(2.3GB, 单权重支持 Canny/Depth/HED/MLSD/Pose); 本机 HF 被封→从 ModelScope `Comfy-Org/MiniMax-H3` 拉(`download_ms.py`, 直连CN CDN, 设 `MS_PROXY=none`); 放 `E:/ComfyUI_models/model_patches/`(T8 loader 查 controlnet+model_patches)。
  - pruned 控制须配 pruned 主模型(fl2va_pruned_int4=8维AdaLN 兼容)。control_kind 仅描述性, 须自预处理。17n+5 帧/24fps/fit_mode=exact 几何须严格匹配。
  - **A/B(768×448/56帧/Turbo/seed12345, 白模角色 0.1W→0.9W 左→右走)**: 基线(无FC) net −10.9px(左)/act1.98; FC depth strength0.8 → net +13.7px(右)/act2.14(干净); **strength1.2+end1.0 → net +37.1px(右)/act10.78(明显增强,画面较活跃)**; strength1.5 → act13.33 崩坏(net−5.7失稳)。→ **FC 纠正走位方向(H3默认左→跟随右)**; **strength 是主杠杆, 推荐 0.8~1.2(<1.5)**; 幅度仍远小于白模意图(+37 vs +614px)。
  - 落地: `gen_blocking.py --depth`(相机视距 MapRange 近白远黑, 背景/地面压黑只留角色); `run_h3_funcontrol.py`(FunControlApply 插 LoRA后/采样前, VHS_LoadVideo 锁 768×448×56; 支持 `--walk` 端到端自动生成控制视频)。strength≈0.8~1.2 起点。
  - ⚠️ 白模走位须落在相机视野内: static/no-track 相机(0,-7) 在角色深度处水平可见约 ±2.6; walk ±3 会起末出画(实测仅46/56帧可见,控制信号丢失), 用 ±2.2 全程可见(56/56, 屏幕x 0.07W→0.93W)。要更大范围就拉远相机或 `--cam lateral`。
  - 视野影响 A/B(seed12345/s1.2/e1.0): 出画(walk±3) net+37.1px/act10.78 vs 全程可见(±2.2) net+21.2px/act9.42 —— 都右移✓; 推荐 ±2.2(控制信号完整、画面略稳)。**度量口径: 以 net_shift(帧差前景净位移)为主**, 光流 trend 在大运动下不稳(曾 dx 正/trend 负矛盾)。
  - **复杂走位**: `gen_blocking --walk` 支持多点折线 `x1,y1:x2,y2:...`(按累计弧长匀速)。闭环 `-2,1:2,1:2,-1:-2,-1:-2,1`(俯视矩形循环+景深, 面积2.8%→5.2%, 56/56可见) → H3 出片屏幕x亦**先增后减**(argmax f33中部, 首末1/4 367≈372px 闭环)→ 跟随的是**逐帧运动结构**而非单向位移。
  - **主线集成**: `agent/mmh3_engine.py` 已内建 -> `generate(control_video=.., fc_strength=..)` 或 config `engine.comfyui_mmH3.fun_control`(enable/control_net/control_kind/fit_mode/strength/end_percent/video); 引擎自动插节点 41/42/43(避让 ref_images 20~28/post 30~32/BlockCache 40/二采 50~57), guider 改接 Apply 输出。真机引擎路径 net_shift +223.7px(右移)✓。
  - **全链路接入(agent.py + blocking.py)**: config `blender.use_as_fun_control: true` → `render_assets` 每镜产 **fcvideos**(走位 depth 序列→fc.mp4) → `generate_one_scene` 自动作 control_video 喂引擎。走位来源: `blender.fun_control_walk`(默认 `-1,0:1,0`, 归一化 ±1=画面左右) + 分镜文本识别(左→右/走近/来回/绕圈, `parse_spec`)。`_FC_TAIL` 模板按镜头距离自动换算世界坐标+留边距→不出画; 控制序列分辨率/帧数严格对齐出片(fit_mode=exact, 帧数吸附 17n+5)。需 Blender+BlenderMCP(9876) 运行。
  - 坑: FunControl int8 2.3GB+H3 累积易 OOM 使 ComfyUI 崩(连续 A/B 两版后崩); 重启 `python launch_comfy.py --sage-attention`; 脚本须用 /prompt 返回的 prompt_id 轮询(非本地 uuid)。
- 评估须量化: arcface 身份 + 人脸检出率 + 走位方向(frame-diff 前景质心 net_shift / optical flow trend)，不凭肉眼。

## 无真人脸 / 动漫化 (用户硬性要求)
- 成片不得出现真人脸，且反对全片模糊。画风由参考图定，文本说了不算 (写实锚定→真人)。
- 现用路线: H3 原片 → `anime_redraw.py` 逐帧动漫重绘 (Counterfeit-V3.0, denoise 0.70, steps 20, 0% 检出, 最稳) 或 `anime_stable.py --mode keyframe` → `make_narration` 加旁白 → faststart。
- 弃用: 像素化 (用户否"脸部打码")、prop 模式 (花屏发散)、ControlNet-Canny (锁真人脸→检出飙68%)、3D 画风 (70% 检出)。
- 连贯性: 全片固定同一种子 (逐帧变种子→抖动8x)。单 GPU 不能同时跑多个 anime_redraw。
- 验收: `diag_face_rate.py` (insightface buffalo_l) 量化，目标 0%。

## 后期配音
- `make_narration.py --film --out --fit-film --fps 24 [--series-script outputs/series_script.json --ep N] [--shots N] [--orig-vol 0.18]`。en-US-Andrew / zh-CN-Xiaoxiao。mp4 必加 faststart。

## 发布
- B站 21150 绕过: 动画分区 tid=174 的 add/v3 被端点拦截；切 `tid=172`(短片) 直投成功。`_bili_upload.py` (用 cookies.json 完整 cookie + buvid3)。
- 三集连贯: 1进城→2觉醒→3对抗前主人。台词 outputs/series_script.json。

## 其他
- WebUI `python webui.py`(:8000)。AMD395(128GB) 须 BF16/FP8 (NVFP4 不兼容)。
- **Lint 基线(2026-09-15)**：仓库根目录 `ruff.toml`（首次固化），只开 `E9/F63/F7/F82 + F`，exclude 含 legacy/outputs；CI 直接 `ruff check .`。
  暂不开 E4/E7（E402 与「惰性 import 重依赖」设计冲突）。⚠️ `tools/blender_server.py` 的 `import bpy` 必须保留（exec 全局依赖），删了会让远端脚本拿不到 bpy。
- **git 推送(2026-09-15)**：本机 `127.0.0.1:7897` 代理已不通 GitHub（curl 走代理 000 / 直连 200）。
  push/ls-remote 用 `git -c http.proxy= -c https.proxy= push origin main`（勿改 git config）。PowerShell 的 `curl` 是别名，用 `curl.exe`。
