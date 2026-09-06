# 长期记忆 (MEMORY.md) — ai_movie_agent

## 环境
- GPU: RTX 5070 Ti 16GB（Blackwell sm_120，可用 ~15.9GB）；RAM 32GB。
- ComfyUI: `D:/ComfyUI`，用其 venv python。干净实例端口 **8200**；实测常驻 **8188**（0.34.0 + ComfyUI-GGUF + ComfyUI-LTXVideo）。
- 模型根 **`E:/ComfyUI_models/`**（经 `D:/ComfyUI/extra_model_paths.yaml` 的 `ltx23_e` 段映射；`D:/ComfyUI/models` 只是部分）。**排查权重缺失两目录都查，否则误判。**
- 外网走代理 `127.0.0.1:7897`；HF 用代理 GET 可过、HEAD 必挂（TLS EOF）。
- **cu130 必要条件**：cu128 下 `comfy_kitchen` CUDA 后端禁用 → latent 全噪声。已升 torch 2.11.0+cu130，VAE 默认即正确，勿带 `--fp32/--fp16-vae`。

## 16GB 出片方案（按推荐度）
### ① LTX-2.5（2026-09-05 实测跑通，当前首选）
- 权重 `E:/ComfyUI_models`: `diffusion_models/LTX-2.5-Distilled-Q2_K.gguf`(8.23G) + `text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors`(12.26G，走 **CLIPLoader type=ltxv**) + `vae/ltx-2.5-video-vae-bf16`(1.37G) + `vae/ltx-2.5-audio-vae-bf16`(0.34G)。合计 ~20.5G，靠 RAM 卸载跑通（GPU 99% 满载）。
- 入口 `python cli.py ltx --prompt "..." --out outputs/x.mp4 --frames 33`（引擎 `agent/ltx_engine.py` / workflow `workflows/ltx2_5_t2v_api.json` / config `comfyui_ltx`）。distilled **8 步**。
- **已修 bug（帧数被放大 121 倍）**：workflow 把视频/音频**两个** latent 的 `batch_size` 硬编码成 121，总帧数=`length`×`batch_size`，`length` 由 `duration`×`fps` 推导 → `--frames 33` 曾产出 3993 帧/166s。修复=`_inject()` 把 `EmptyLTXVLatentVideo` 与 `LTXVEmptyLatentAudio` 的 `batch_size` **都**强制为 1（只改一个会 AV 拼接报 `Expected size 1 but got size 121`）。修复后 33 帧→33 帧/1.38s，采样 13s（原 5分27s）。
- `--frames N` 经 `duration=N/fps` 再被 `1+floor(fps*duration/8)*8` 取整（对齐 8 的倍数）间接生效。
- **真 T2V + 自带音频**（成片 mp4 带 aac 48kHz 立体声），优于 LTX-2.3。

### ② LTX-2.3（真 T2V + 音频，可用）
- 权重 `E:/ComfyUI_models`: `diffusion_models/ltx-2.3-22b-distilled-1.1-Q3_K_M.gguf`(10.1G) + `text_encoders/gemma_3_12B_it_fp4_mixed.safetensors` + `text_encoders/ltx-2.3-embeddings-connectors.safetensors` + `vae/ltx-2.3-video-vae.safetensors` + `vae/ltx-2.3-dev-audio-vae.safetensors`。
- 脚本 `run_ltx23.py`（端口 8200、`S.trust_env=False`）；多镜 `run_ltx23_multishot.py`（18 镜四幕剧本，产物 `outputs/ltx23_film.mp4`）。默认 768x512x97@25fps、8步、cfg=1.0。

### ③ Wan2.2 作 I2V 备选（仅 I2V，无音轨）
- 权重 `E:/ComfyUI_models`: `diffusion_models/Wan2.2-TI2V-5B-Q8_0.gguf`(5.03G) + `text_encoders/umt5xxl-encoder-q5_k_m.gguf`(3.86G, CLIPLoaderGGUF type=wan) + `vae/Wan2.2_VAE.safetensors`(1.31G)。
- **"看不清"根因**：TI2V 被当 T2V 用——`Wan22ImageToVideoLatent` 不传 start_image → 退化为平滑色调场。正解=喂真实起始图做真 I2V（`run_wan22_test.py <img>`）。
- 铁律：负向提示词必填 Wan 官方中/英（空→褪色发灰）；shift 480p=5.0/720p=8.0；16GB 稳 832x480×49×30步；KSampler euler/beta cfg=6 denoise=1.0；起始图越写实越清晰（`make_start_image.py`）。
- **纯 T2V-5B 不可行**（HF 无 Wan2.2-T2V-5B GGUF，官方仓库 404）。

## 下载/网络
- `download_hf_mt.py`：强制代理7897、GET(Range)取size、gated token 走 `Authorization: Bearer` 头（?token= 对 API 无效）。
- LTX-2.5 NVFP4(17.4G) / LTX-2.3 FP8(27G) 在 16GB 物理 OOM，勿用；用 GGUF Q2_K/Q3 路线。

## AMD 395(128GB) 部署（备查）
- NVFP4 不兼容 AMD；需 BF16(~44G)/FP8(~22G)。BIOS UMA Frame Buffer 75-96GB，Linux+ROCm。本机 ltx_engine.py/config.yaml/cli.py 可直接复用。

## 后期：默认英文配音+中英双语字幕（离线）
- **用户规则（2026-09-06）：默认都用英文和双语字幕**。所有成片（`ltx23_film_vo.mp4` / `ltx25_film_vo.mp4`）、WebUI 一键出片、LTX-2.3/2.5 链路一致。临时回中文：`make_narration.py --lang zh --subs zh`。
- 脚本 `make_narration.py`：默认 `LANG="en"`（voice=`en-US-AndrewMultilingualNeural`）+ `SUBS="bilingual"`（上行英文 Arial 20pt y=h-th-66，下行中文 simhei 23pt y=h-th-38）。中文 `zh-CN-XiaoxiaoNeural` 改为备用 `VOICE_ZH`。`LINES_EN` 是 9 段英文行，与 `LINES` 逐段对应。
- 字幕：ffmpeg `drawtext` 中文走 `simhei.ttf`、英文走 `arial.ttf`，文本走 `textfile`（绝对规避 shell GBK 乱码），ffmpeg 用 `cwd=outputs/nar` 切到字幕文本目录。
- 响度：`loudnorm I=-16:TP=-1.5:LRA=11`；解说 `volume=2.6`、原环境音压 `0.18`。
- `--auto-dur` 探时长：imageio_ffmpeg **无 ffprobe**，必须用 `ffmpeg -i` 解析 stderr 的 `Duration:` 字段。`--film/--out` 必须绝对路径（`build_and_render` 把 cwd 切到 `outputs/nar`，相对路径解析失败）。
- **双语成对覆盖**（`_apply_storyboard`）：双语模式要求 storyboard 同时提供等长 `narration_en` 才覆盖中英；否则 `[warn]` 保留内置成对台词，避免「英讲A、中讲B」错位（`storyboard.json` 当前是 Mira 中文，只有 `narration` 无 `narration_en`，故永远保留内置《看见未来之前》对）。
- TTS：默认 `edge_tts`（联网），未装则回退 Windows SAPI（Huihui 离线中文；英文场景离线无法合成会抛异常）。要换中文神经语音 `pip install edge-tts`。
- 当前 LTX-2.5 最终成片：`outputs/ltx25_film_vo.mp4`（英文配音+双语字幕，768×448，约 64s）；源片 `outputs/ltx25_film.mp4`（LTX-2.5 原生环境音轨）。`outputs/ltx23_film_vo.mp4`（61s）同款。

## WebUI（webui.py + webui/pipeline.html）
- 项目 venv `python webui.py`(:8000)，`/` 服务 `pipeline.html`（A–H 控制台）。
- **日志不落文件**：后台任务的 stdout/stderr 进内存 `_state["logs"]`。查实时/最近一次任务日志走 `Invoke-RestMethod http://127.0.0.1:8000/api/logs`（读 `.logs`/`.result`/`.running`）。**排查任何"出了片但不对"的问题，先看这里。**
- **ComfyUI 端口**：常驻 **8188**（`run_ltx23_multishot.py` 曾硬编码 8200 → 每镜 ConnectionError）。现两脚本均自动探测 8188/8200/8189/8288，也可设 `COMFY_URL` 指定。
- **子进程脚本失败必须 `sys.exit(非0)`**：`run_script()` 按 returncode 判定成败，脚本内部 `return` 会让退出码为 0 → 链路"假成功"往下跑。曾导致 G 渲染 18 镜全失败却报「全自动流程全部完成」，后续拿几天前的旧成片配音封装，用户毫无感知。
- 成片页签（统一入口，不按模型分页）：列 `outputs/*.mp4` 播放+分镜/解说编辑+重生成；接口 `/api/films`、`/api/film/play?name=`、`/api/storyboard`(GET/POST)、`/api/film/render`。保存→`outputs/storyboard.json`→`run_ltx23_multishot.py`/`make_narration.py` 启动即覆盖内置。
- **成片页支持模型切换**：`<select id="film-model">` 下拉 LTX-2.3/LTX-2.5，`/api/film/render` 与 `/api/run_full` 三处按 model 路由（G 渲染/解说/H 封装）。LTX-2.5 走 `run_ltx25_multishot.py`(复用 LTXEngine，原生音视频联合，ffmpeg xfade+acrossfade 双轨拼接) + `make_narration.py --film outputs/ltx25_film.mp4 --out outputs/ltx25_film_vo.mp4 --auto-dur`(ffprobe 探真实时长均分解说，模型无关)。
- 坑：需 `imageio_ffmpeg`；`json_resp` 显式 `charset=utf-8`；视频 `send_file(conditional=True)` 才有 `Accept-Ranges`；`/api/film/play` 用 `basename` 防穿越。

## A–H 老管线（SkyReels，仅采集+企划用）
- A 跳过因 `config.collector.urls` 空数组；填了 `trust_env` 走 Windows 代理(7897) 出网。
- Ollama `D:\OllamaApp\ollama.exe` 不自启需 `ollama serve`；**配置用 `gemma4:e2b`**（非推理、直出中文好）。`qwen3.5:9b` 是推理模型→content 空→各阶段降级，勿用。
- 各阶段：A/B/C 可用；D 需 ComfyUI workflow（未就绪）；**G 需 SkyReels 未装→到 G 断**。出片走 LTX+`outputs/storyboard.json`。

## B 站发布（bilibili）
- 凭证 `outputs/cookies.json`（`bili_jct`=CSRF/`SESSDATA`/`DedeUserID`=108682014）；走代理7897。
- 编辑 `POST member.bilibili.com/x/vu/web/edit?csrf={bili_jct}`（body JSON，filename 由 `biliup show {bvid}` 在 outputs/ 拿）。
- **删除接口失效**：`publisher.py delete_video()` 空壳；老 `/x/vu/web/del` 等全 404。待查真实端点（见 `bilibili-api-python` 的 `Video.delete()`）。交接 `BILIBILI_HANDOFF.md`。
- **⚠️ 2026-09-06 的「第一集」`BV1g2bW6cEfz` 是错稿**：标题写「第一集」，但画面=另一集、字幕=第二集的《看见未来之前》台词 → 声画/集数错位，**待用户手动下架**。（根因：把脚本内置台词当成通用台词套用，见下方「集数-台词映射」）
- **集数 ↔ 画面 ↔ 台词映射（务必先确认再动手）**：
  | 集 | 画面源片 | 台词 | bvid |
  |---|---|---|---|
  | 第一集 | 未定 | 未定 | `BV1Ndti6UETo`(中文版，待处理) |
  | 第二集 | `ltx23_film.mp4`(61s) | 《看见未来之前》9 段 | `BV1vdtq6qEQj` |
  | 第三集 | `ltx25_film.mp4`(64s, LTX-2.5) | Mira 9 段(storyboard narration) | `BV1uubW6xEtJ` |
  - **脚本内置 `LINES/LINES_EN` 只属于第二集**（《看见未来之前》）。给别的集配音前必须确认该集台词来源，否则必然错集。
  - 想用 `storyboard.json` 的 narration，必须同时在里面写等长 `narration_en`，双语才会成对覆盖。
- 待删误传 `BV13PtB6AEyg`《看见未来之前》；`BV1Ef896tEcx`(00:27 测试)待确认。
- 根因 bug：`publisher.py publish_latest()` 传纯片名→`title_template` 永不生效→第二集无集数。**用户规则(09-04)：标题必须含片名+集数才发，否则拦截**（`upload()` 的 `validate_title` 门禁已实装，episode 从标题正则反推，缺失返回 error）。**09-06 新规则：默认都用英文和双语字幕**。
- biliup 投稿：**biliup cwd 是 `outputs/`**，所以 `publisher.upload(video_path)` 一定要传绝对路径，传相对路径会变 `outputs/outputs/...` 找不到文件。biliup-rs `client` 接口已废弃会自动回退 `APP` 接口（rust 日志会写「客户端接口已失效」），正常。看到 `APP接口投稿成功` 即完成。B 站 view 接口有数秒缓存（~25s），刚投的稿件 `code:-404 啥都木有`，等同步后会变 `code:0 OK`。
