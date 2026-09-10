# 长期记忆 (MEMORY.md) — ai_movie_agent

## 环境
- GPU: RTX 5070 Ti 16GB（Blackwell sm_120，~15.9GB）；RAM 32GB。
- ComfyUI `D:/ComfyUI` 常驻 **8188**（venv python；GGUF + LTXVideo + T8/VideoHelperSuite 节点）。
- 模型根 `E:/ComfyUI_models/`（经 extra_model_paths.yaml 映射，E: 和 D: 都查）。
- 代理 `127.0.0.1:7897`。⚠️ 代理 env 让 urllib 把 127.0.0.1 也走代理→502；访问本机 ComfyUI 须 `install_opener(ProxyHandler({}))` 或 NO_PROXY。
- **cu130 必须**：cu128 下 comfy_kitchen CUDA 禁用→latent 全噪声。已升 torch 2.11.0+cu130，VAE 默认正确，勿带 --fp32/--fp16-vae。
- 项目用 `.venv` 解释器 `d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe`（系统 python 缺依赖）。Edge TTS/外网经 7897 正常。
- **SageAttention（2026-09-10 实测跑通，推翻早期「Win+sm_120 只能 SDPA」的错误结论）**：wheel 用 HF `ussoewwin/Sage-Attention-for-Windows`（含 cu130torch2.11.0-cp313 完美匹配版，SA2 25MB / SA3 4.1MB），安装**必须 `--no-deps`** 防覆盖 torch，且 wheel 文件名须保留完整五段标签（改名成 `sageattn3.whl` 会报 Invalid wheel filename）；另需 `pip install triton-windows`（官方 triton 不支持 Win，社区版可用，装后 torch 不变）。实测(bf16,H=12,D=128)：L=8192 → SA2 **6.22x**(cos .9992) / SA3 **7.05x**(cos .9812)；**L≤512 时两者都比 SDPA 慢 3~4 倍，勿用于短序列**（交叉点在 512~2048）。启用：ComfyUI 加 `--use-sage-attention` = **SA2 全局生效**；SA3 是注册式后端 `sage3`，需 KJNodes 的 PatchSageAttentionKJ（**本机未装 KJNodes**）。H3 走标准 `optimized_attention`/`attn1_patch` 接口可直接吃到。**画质优先选 SA2**（cos .999 近乎无损），SA3 的 FP4 量化误差（cos .981）可能劣化人脸一致性。**H3 实拍(shot4/640×352/56帧/Turbo 热启动)：SDPA 113.1s → SA2 86.6s = 1.31x**（远低于 tensor 的 6x，因含 VAE/音频开销；早先 201s 基准含冷启动不可比）；arcface 人脸相似度 SA2 0.3509 vs SDPA 0.3631（差 0.012，噪声级）→ **SA2 可常态化**。切换见 `_launch_comfy.py` 的 `COMFY_SAGE` 环境变量（默认 1 开）。

## 16GB 出片方案（推荐度）
- **① LTX-2.5**：gguf Q2_K(8.23G)+gemma4-12b int8(12.26G)+vae。入口 `python cli.py ltx`；engine `agent/ltx_engine.py`。真 T2V+自带音频(aac 48k)。帧数 bug 已修(强制 latent batch_size=1)。
- **② LTX-2.3**：gguf Q3(10.1G)+gemma3_12b+connectors+vae×2。`run_ltx23.py`(8200)/`run_ltx23_multishot.py`(18镜→ltx23_film.mp4)。
- **③ Wan2.2**：仅 I2V 无音轨。TI2V-5B Q8(5.03G)+umt5xxl Q5(3.86G)+VAE。TI2V 必传 start_image，负向必填。
- **④ MiniMax H3（主力，2026-09-08 部署）**：INT4 量化档(扩散11.3G+文本15G+视频vae4.9G+音频vae0.6G)；须装 `comfyui-minimax-h3-audio-T8` + `ComfyUI-VideoHelperSuite`(需 opencv-python-headless)。Turbo LoRA(1.96G,4步)已落地→48s 出 768×448/2.33s/立体声。已接入 `agent/mmh3_engine.py`+`cli.py mmh3`+`run_series.py --engine mmh3`。引擎自动修正 H3 两约束(32整除 / 17n+5 吸附)。坑：①本机绕代理 ②`--medvram` 不识别 ③DynamicVRAM 已够 ④VHS_VideoCombine 必填 save_output/loop_count/pingpong ⑤宽高须32整除。⑥**组件勿跨模型混搭**：H3 UNet 必须配 H3 文本编码器 `MiniMaxH3TEModel_`(输出5120维, `token_refiner` 权重写死5120)；用 LTX-2.5 的 `gemma4-12b` CLIP(输出6144) 必报 `mat1 75x6144 × mat2 5120x5376`；VAE/latent 也须 H3 全套(勿用 Wan VAE / SD3 latent)。想用 LTX 就用 LTX 全套。

## H3 白模控制走位（2026-09-09 POC，核心）
- **接入**：H3 原生 `ref_videos`(≥5帧,≤3段) + `task_type=Hybrid`(首帧锁形象 + 参考视频锁走位/运镜)。`pip install bpy`(.venv py3.11.9 匹配官方 wheel,无需桌面端)→`gen_blocking.py` 建白模(圆柱+球头+地面+相机TrackTo+运镜关键帧)→PNG→ffmpeg→`MMH3Engine.generate(image=锚定图, ref_video=白模)`(VHS_LoadVideoPath 吃本地绝对路径免上传)。
- **Autogrow API 大坑**：`ref_videos` 在 prompt 里是 `ref_videos.ref_video_0`(i 从0起,带父级前缀),写错静默丢弃→报 "HYBRID requires at least one reference media input"。
- **bpy 5.0 坑**：action.fcurves 不存在(改插值 try/except)、相对路径解析到 C:\ 须绝对路径、输出前缀 f0001.png(ffmpeg 用 f%04d.png)、相机 TrackTo 跟拍时人物恒中心(横向走位须去约束)。
- **控制力边界（_run3 验证）**：白模可靠控「推近幅度 push_in 面积3.25x」与「相机静止 static 1.01x」；拉远 pull_out 1.21x(弱)、环绕 orbit 0.92x(无效)、横向 walk 中心恒0.502(失控)。结论：控的是 Motion/运镜，非 Blocking/人物构图——严格"控制走位"只达成一半。
- **全量 ep1 完成**：`run_blocking_batch.py`(读 `outputs/blocking/ep1_blocking_map.json`, bpy白模+H3 Hybrid, `--start N` 断点续跑)跑18镜→`outputs/blocking/ep1_blocking_film.mp4`(28.77MB/67.5s/1620帧)。运镜设计:shot1/5/11 push_in, 余 static。
- **SAFE_DELETE 大坑**：gen_blocking 删临时 png 的 rmtree/os.remove 在长进程累计删>500 后被 turn 级拦截崩溃(第12/18镜崩)。改用 `os.system('del /q')` 绕过；ffmpeg 加 `-start_number 1 -frames:v <frames>`；拼接用目录 `??.mp4` glob。
- **⚠️ 白模路线被否（2026-09-10 用户实测）**：白模成片观感「人物内容乱」。量化（arcface vs mira_anchor）→ 白模Hybrid 平均 **0.122（13/18镜检出脸）** vs 锚定原 0.146（17/18） vs 锚定**换脸后 0.466**。归因：①对比的两版都没换脸，"乱"不是漏换脸，而是 **白模 Hybrid 本身**——shot5/6/8/18 在锚定版能检出脸、白模版**检不出**（人物形态崩坏，参考视频运动语义与真人冲突），且每镜 3.75s（锚定 1.86s）运动余量翻倍、推近/静止人工跳变。②**最终交付必须用换脸版**（face-swap 把一致性 0.146→0.466）。
  - **结论**：白模 Hybrid 是结构性缺陷（部分镜人物崩坏，换脸也救不回），净负收益 → **ep1 主成片 = `outputs/ep1_vo_mmh3_fs.mp4`（换脸后+旁白），白模路线归档弃用，ep2/ep3 不走白模**。
  - **教训**：评估生成质量不能只量「人脸面积/中心」（运动幅度），必须同时量 **arcface 身份一致性 + 人脸检出率**，否则会把"运动可控但人物崩坏"误判为路线可行。
  - **崩坏可修方向（节点能力已确认，2026-09-10）**：`MiniMaxH3AudioConditioningT8` 参考输入上限 **图片9 / 视频3 / 音频3**（`ref_images` / `ref_videos` / `ref_audios`，都是 Autogrow：键名 `ref_images.ref_image_0` 形式，i 从 0）。可用手段：① **`ref_images`（最多9张）**——当前只给 1 张 `first_frame`，身份信号太弱被参考视频运动压过，加多张 Mira 参考图可显著增强身份（节点生成 `ref_image_N` 标签编入 media_map，无需强制手写 prompt 标签）；② **`last_frame`** 锁尾帧，防结束时人物漂移；③ **关 Turbo 提步数**（Turbo 4 步太糙→30 步，每镜 ~5min）；④ 降低白模运动幅度 / 缩短参考帧数。参考视频受 `reference_video_policy=official_2_to_15s` 约束：48–360 帧@24fps、每段≥5 帧。

## 后期：英文配音+双语字幕（离线）
- `make_narration.py --film <in> --out <out> --fit-film --fps 24 [--series-script outputs/series_script.json --ep N] [--shots N] [--orig-vol 0.18]`。voice=en-US-AndrewMultilingualNeural；中文 zh-CN-XiaoxiaoNeural。`--orig-vol 0`=去原音。
- **旁白-画面同步**：`--fit-film`(隐含 --auto-dur)按「N_SHOTS 镜 / n 段旁白, 通常2镜1段」做镜头块对齐，起点按 slot 整数倍排布，不再顺序平铺累积漂移。
- **mp4 必加 faststart**：concat/成片 moov 在尾→浏览器能取封面但播不了。预览/上传前 `ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4`。`outputs/_serve.py`(:8777) 本地 Range 预览服务。
- 台词优先级：series_script > lines-json > storyboard > 内置。

## WebUI / 老管线 / B站 / 三集重做（备查）
- WebUI `python webui.py`(:8000)；ComfyUI 探测 8188/8200/8189/8288。子进程失败须 sys.exit(非0)。
- A–H 老管线(SkyReels)仅采集+企划，G 断；出片走 LTX/H3 + storyboard.json。
- B站：`outputs/cookies.json`(bili_jct/SESSDATA/DedeUserID=108682014)，走代理7897；删除接口失效候 `bilibili-api-python` Video.delete()，交接 BILIBILI_HANDOFF.md。标题须含片名+集数。
- 三集连贯化：`docs/series_coherence_plan.md`；时间线1进城→2觉醒→3对抗前主人；台词源 `outputs/series_script.json`、Mira 档案 `outputs/series_bible.json`。H3 三集 `run_series.py --engine mmh3`→`outputs/ep1_series_film_mmh3.mp4`(18镜/33.5s/768×448/24fps/立体声)，旁白版 `outputs/ep1_vo_mmh3.mp4`。尾帧续写 + 跨集闭环(阳光/芯片/前主人脸/门)。

## 下载/网络 / AMD395
- `download_hf_mt.py`：强制代理7897+GET(Range)取size+gated token。HF 直连无代理正常。LTX-2.5 NVFP4(17.4G)/2.3 FP8(27G) 16GB OOM 用 GGUF。
- AMD395(128GB)：NVFP4 不兼容，需 BF16(~44G)/FP8(~22G)，BIOS UMA 75-96GB+Linux+ROCm；本机 ltx_engine/config/cli 可复用。
