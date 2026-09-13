# 长期记忆 (MEMORY.md) — ai_movie_agent

## 环境
- GPU: RTX 5070 Ti 16GB（Blackwell sm_120）。ComfyUI `D:/ComfyUI` 常驻 **8188**（venv python；GGUF+LTXVideo+T8/VideoHelperSuite）。
- 模型根 `E:/ComfyUI_models/`（extra_model_paths.yaml 映射，E/D 都查）。代理 `127.0.0.1:7897`；⚠️ 代理 env 让 urllib 把 127.0.0.1 也走代理→502，访问本机 ComfyUI 须 `install_opener(ProxyHandler({}))` 或 NO_PROXY。
- **cu130 必须**（cu128 下 CUDA 禁用→latent 全噪声）。项目 venv：`d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe`（系统 python 缺依赖）。Edge TTS/外网经 7897 正常。
- 终端 **PowerShell**（非 cmd）：`cd /d`、`&&` 会失败；用 `Set-Location; & 'python' args`。长任务用 `Start-Process ... -RedirectStandardOutput/-Error -NoNewWindow -PassThru` 后台跑 + `Get-Content -Tail` 轮询。

## SageAttention（2026-09-10 跑通，推翻"Win+sm_120 只能 SDPA"）
- wheel 用 HF `ussoewwin/Sage-Attention-for-Windows`（cu130torch2.11.0-cp313 匹配版，SA2/SA3）。安装**必须 `--no-deps`** 防覆盖 torch；wheel 文件名须保留完整五段标签；另 `pip install triton-windows`。
- 实测(bf16,H=12,D=128)：L=8192→SA2 **6.22x**(cos .9992)/SA3 **7.05x**(cos .9812)；**L≤512 时两者比 SDPA 慢 3~4x，勿用于短序列**（交叉点 512~2048）。
- H3 注意力走 ComfyUI 全局 `optimized_attention`（**不是** `wan_video_dit.sageattn`，monkey-patch 无效——H3 早于 custom node 捕获引用）。启用 SA2：启动加 `--use-sage-attention`。启用 SA3：改 `D:\ComfyUI\comfy\ldm\modules\attention.py` 在 `sage_attention_enabled()` 后加 `elif os.environ.get("SA3")=="1" and SAGE_ATTENTION3_IS_AVAILABLE: optimized_attention = attention3_sage`，并用 `launch_comfy.py --sage3`。
- **H3 生产推荐 SA2**（cos .999 近无损，86.6s）；SA3(FP4) 短序列不划算（93.5s，慢~8%）且 cos .981 或劣化人脸一致性。arcface：SA2 0.3509 vs SDPA 0.3631（噪声级）→ SA2 可常态化。切换见 `_launch_comfy.py` 的 `COMFY_SAGE`（默认 1）。

## 16GB 出片方案（推荐度）
- **① LTX-2.5**：gguf Q2_K+gemma4-12b int8+vae；`python cli.py ltx`，engine `agent/ltx_engine.py`。真 T2V+自带音频。帧数 bug 已修。
- **② LTX-2.3**：gguf Q3+gemma3_12b+connectors；`run_ltx23.py`(8200)/`run_ltx23_multishot.py`。
- **③ Wan2.2**：仅 I2V 无音轨。TI2V-5B Q8+umt5xxl Q5+VAE，必传 start_image、负向必填。
- **④ MiniMax H3（主力）**：INT4 档（扩散11.3G+文本15G+视频vae4.9G+音频vae0.6G）；须 `comfyui-minimax-h3-audio-T8`+`ComfyUI-VideoHelperSuite`。Turbo LoRA→48s 出 768×448/2.33s/立体声。入口 `agent/mmh3_engine.py`+`cli.py mmh3`+`run_series.py --engine mmh3`。引擎自动修 H3 约束(32整除/17n+5 吸附)。坑：本机绕代理；`--medvram` 不识别；DynamicVRAM 够；VHS_VideoCombine 必填 save_output/loop_count/pingpong；宽高须32整除。⑥**组件勿跨模型混搭**（H3 UNet 须配 H3 TE `MiniMaxH3TEModel_` 输出5120，勿用 LTX CLIP 6144；VAE/latent 也须 H3 全套）。

## H3 白模控制走位（已被否，归档）
- 路线：`ref_videos`(≥5帧,≤3段)+`task_type=Hybrid`(首帧锁形象+参考视频锁走位)。`pip install bpy`→`gen_blocking.py` 建白模→H3 Hybrid。
- **⚠️ 白模路线被否（2026-09-10 实测）**：成片「人物内容乱」。arcface：白模Hybrid 0.122 vs 锚定原 0.146 vs **锚定换脸后 0.466**。白模 Hybrid 结构性崩坏（部分镜人物形态乱，换脸也救不回）→ **ep1 主成片=`outputs/ep1_vo_mmh3_fs.mp4`（换脸+旁白），白模弃用，ep2/ep3 不走白模**。
- 教训：评估质量须同时量 **arcface 身份一致性 + 人脸检出率**，不能只量「人脸面积/运动幅度」，否则会把"运动可控但人物崩坏"误判为可行。
- 身份增强手段（`MiniMaxH3AudioConditioningT8`，Autogrow）：`ref_images`(≤9张，当前只给1张首帧，信号弱)、`last_frame` 锁尾帧、关 Turbo 提步数、降白模运动。

## 后期：英文配音+双语字幕（离线）
- `make_narration.py --film <in> --out <out> --fit-film --fps 24 [--series-script outputs/series_script.json --ep N] [--shots N] [--orig-vol 0.18]`。voice=en-US-AndrewMultilingualNeural；中文 zh-CN-XiaoxiaoNeural。`--orig-vol 0`=去原音。
- `--fit-film`(隐含 --auto-dur)按「N_SHOTS 镜 / n 段，通常2镜1段」做镜头块对齐，起点按 slot 整数倍排布，消除累积漂移。台词优先级：series_script > lines-json > storyboard > 内置。
- **mp4 必加 faststart**：`ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4`（否则浏览器能取封面但不能播）。`outputs/_serve.py`(:8777) 本地 Range 预览。

## 无真人脸 / 动漫化（2026-09-10 用户硬性要求，核心）
- 要求：成片**不得出现真人脸**（《看见未来之前》系列）；并**明确反对全片模糊**（要保清晰）。
- 画风由参考图决定，文本说了不算：写实锚定图必出真人，即使 prompt 写 anime。→ 改动漫风须先换动漫资产（`outputs/anchor/mira_*.png`、`scene_*.png` 皆写实）。
- H3 无法靠文本出真 2D 动漫：人脸特写弱/强 anime 提示仍 98%/100% 检出；H3 **不支持负向提示词**。
- 真 2D 动漫尝试（下 `Counterfeit-V3.0` 2GB 到 E:/ComfyUI_models/checkpoints，重启 ComfyUI 才识别）：用 SD 生成动漫 Mira 三视图(`gen_anime_anchor.py`)+逐镜动漫关键帧(`--shots epN`)→**关键帧 PNG 自身 0 检出(纯动漫)**；但 H3 动画时把动漫首帧**漂回写实**→全片仍 **38%** 检出。H3 真人视频模型固有漂移，首帧方案无法稳定归零。
- **后期局部处理（保清晰的关键）**：
  - ❌ **平涂(kmeans)+模糊 反生效**：清晰写实大脸被平涂后肤色均匀→RetinaFace 更易检出(0.786→0.814)。`cartoonize.py` 旧 face_paint 此路不通。
  - ✅ **像素化(马赛克)人脸区域**：`pixelate(roi, bs=18)` 缩小再最近邻放大→彻底破坏五官→**检出 0.0**，背景锐利。`cartoonize.py` 默认 `--mode pixelate`（此方案现已弃用，见下）。
  - ❌ **像素化被用户否决（2026-09-11，「脸部不要打码」）**：马赛克观感差，弃用。改为 **逐帧动漫重绘**（`anime_redraw.py`：ComfyUI + Counterfeit-V3.0 动漫 checkpoint 对每帧做 img2img）→ 脸变成**画出来的 2D 动漫角色**，且非打码。代价：每帧~2s（每集约25–90min）、逐帧独立可能轻微闪烁、脸变通用动漫角色(无 Mira LoRA 不保身份)。⚠️ ComfyUI 单 GPU，**不能同时跑多个 anime_redraw**（input 目录 `redraw_XXXXX.png` 文件名冲突），须逐集串行。
  - **画面连贯性（2026-09-11 关键）**：用户看片判"画面混乱"。根因=旧版 `seed=基准+帧号`（**逐帧变种子**）→帧间抖动 44（源片~5，约 8 倍）。**修复=全片固定同一种子**（默认已改），抖动降到 ~15（3× 改善）且保持 0% 检出；`--vary-seed` 退回旧行为。
  - ⚠️ **ControlNet-Canny 在此项目不可用**：其 Canny 边缘会锁死真人脸眼/鼻/嘴轮廓→动漫模型画出贴近真人脸结构的脸→RetinaFace 检出率从 0% 飙到 68%。`anime_redraw.py` 默认 `--controlnet ""`（禁用）。
  - **denoise 定档 0.70（2026-09-11 实测）**：ep2 全片 0.6→**10%**检出、ep1 旧版0.55→**32%**；难帧样本 0.70→**0%**、0.75→2%（更高无益）。故统一 `--denoise 0.70 --steps 20`。`run_anime_series.py` 调 `REDRAW_DENOISE`（默认0.70）强制重绘 ep(2,1,3)。
  - **⚠️ 花屏根因（2026-09-12 定案）**：`anime_stable.py` 的 **prop 模式（逐帧传播+轻刷新）会发散**——
    每帧闭环「光流 warp → VAE编码 → 低 denoise 重刷 → VAE解码」累积 VAE 损失+光流误差，约 10~14 帧后成**彩色噪点(花屏)**。
    逐帧相关性扫描(各输出第 i 帧 vs 源第 690+i 帧)：朴素逐帧 0.60(零坏帧) / keyframe 0.57(2坏帧) / **prop 0.14(第14帧起全坏)**。
    → **弃用 prop**（默认已改 keyframe，prop 加警告）。`--mode keyframe --key 1` 等价朴素逐帧重绘。
  - keyframe 模式（每 K 帧重绘关键帧+中间帧光流搬运）不花屏且抖动更低（实测 5.18 vs 逐帧 12.35），可作较快的选项；
    ⚠️ `cv2.remap` 的 map 必须=恒等网格+位移（绝对坐标）。参数 `--key 8 --blend 0.85 --flow-scale 0.5`。
  - ⚠️ 3D 画风(`--style3d`)：不花屏但**真人脸检出 70%**（皮克斯风让脸更写实）→ 与"无真人脸"冲突。
  - 现用流程：H3 原片 → `anime_redraw.py`（朴素逐帧，0% 检出，最稳）或 `anime_stable.py --mode keyframe` → `make_narration` 加旁白 → faststart。检出率目标 0%。
- **验收工具**：`python diag_face_rate.py <视频...>` 输出真实人脸检出率与平均置信（insightface buffalo_l，真实照片训练）。`PERFRAME=1`+传两视频逐帧对比。判断是否"有真人脸"**一律量化，不凭肉眼**。
- ⚠️ 教训：①本会话模型**读不了图片**，绝不能凭想象描述画面，须用可量化指标并声明"请你目视确认"。②局部平涂会帮倒忙，像素化才是杀检测且保锐利的正确局部手段。
- **交付物（现用逐帧动漫重绘路线）**：`outputs/epN_anime_mmh3.mp4`（无旁白，2D 动漫整片，0% 检出）、旁白版 `outputs/epN_vo_anime_mmh3.mp4`。工具 `gen_anime_anchor.py`/`anime_redraw.py`/`make_narration.py`/`diag_face_rate.py`。`cartoonize.py` 的像素化模式已弃用（用户否）。ep1 原像素化版 `ep1_faceonly_mmh3.mp4` 已被动漫重绘路线取代。

## 其他（备查）
- WebUI `python webui.py`(:8000)。A–H 老管线(SkyReels)仅采集+企划，G 断；出片走 LTX/H3+storyboard.json。
- B站：`outputs/cookies.json`，走代理7897；删除接口失效候 `bilibili-api-python` Video.delete()，交接 BILIBILI_HANDOFF.md。
- **⚠️ B站投稿 21150 绕过（2026-09-13 实测定案，重要）**：B站 2025-09-23 升级后，**动画分区 tid=174 的 `x/vu/web/add/v3` 被端点级拦截**（返回 `21150 投稿入口升级中`），biliup 全系(v0.2.4/v1.2.4/master)与 bilibili-api 9.1.0(其 submit 端点甚至还是旧 `add` 无 v3) 全部失效。但**其它分区(如 172 短片/17 单机游戏/21 日常…)的 add/v3 仍正常接受请求**（假文件名时返回 `21015 视频上传问题`，真文件即成功）。
  - ✅ **解法=切换分区 id 到非 174**（用户原话"切换分区"思路成立）：AI 短片用 `tid=172`(短片) 直投成功。
  - 脚本：`_bili_upload.py`（已落地，EP2→BV1y5YZ69EwS、EP3→BV1y5YZ69EwR）。关键：用 `outputs/cookies.json` **完整 cookie 头(含 sec_ck)** + `finger/spi` 补 `buvid3`，不能用 bilibili-api 9.1.0 的 `Credential`(只带 sessdata/bili_jct/buvid3 三字段→412 风控)；upos 分块上传复用 bilibili-api 9.1.0 video_uploader 逻辑。
  - 验证：`view` 接口查 bvid，`state=0` 即正常发布；标题须含片名+集数(用户规则"标题要准确才发")。
  - 注：动画分区(174)若用户坚持要，只能走真实 Web 投稿界面(browser-use，但本机未装 CLI)。
- 三集连贯：时间线1进城→2觉醒→3对抗前主人；台词源 `outputs/series_script.json`、Mira 档案 `outputs/series_bible.json`。
- AMD395(128GB)：NVFP4 不兼容，需 BF16(~44G)/FP8(~22G)；本机 ltx_engine/config/cli 可复用。
- H3 优化备选（未装插件）：BlockCache 加速（tokendance-h3 的 YixuAnH3AccSwitch，运动小镜头显著加速）；PDD 8步；学习型 latent 放大；Prompt Relay。推荐装 Manager/KJNodes/RIFE(补帧48fps)。
