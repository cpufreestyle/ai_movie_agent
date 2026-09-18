# 项目长期记忆：ai_movie_agent
权威目录 `.workbuddy/memory/`（旧 `.codebuddy/` 已合并停止跟踪）。

## 环境 / 出片
- RTX 5070 Ti 16GB (sm_120)。ComfyUI `D:/ComfyUI`:8188。主 venv `d:/ai sheare/repo/ai管理/.venv`（**cu130 必须**）。代理 127.0.0.1:7897。
- 引擎默认 `comfyui_mmH3`（INT4，须 `comfyui-minimax-h3-audio-T8`+VideoHelperSuite），入口 `run_series.py --engine mmh3`。LTX-2.5 / Wan2.2 备选。
- 走位用 **H3 Fun Control**（depth，strength 0.8~1.2，≥1.5 崩坏）；白模走位须在视野内（±2.2）。`blender.use_as_fun_control:true` 已全链路接入。

## 无真人脸 / 动漫化（硬性）
- H3 原片 → `anime_redraw.py` 逐帧重绘（Counterfeit-V3.0, denoise .70）→ `make_narration` 加旁白。固定同一种子保连贯。默认风格日式动漫 `config.project.style`。
- 🚫 不做真人脸检测：`qa.face_check` 恒 false，不进 preflight 闸门（用户已否决，勿再提）。

## 画质增强（2026-09-18）
- `agent/encode.py` = 编码唯一权威：4 档 `draft/standard/high/anime`（crf 23/18/16/16；tune -/-/film/animation），统一 `yuv420p`+`profile high`+`level`(>2048宽或>1080高→5.1)+2s GOP+`aac 192k`+`+faststart`。动漫走 `tune animation`。
- `SR_MODELS`：anime→`RealESRGAN_x4plus_anime_6B.pth`，real→`4x-UltraSharp.pth`。
- `enhance_video.py`：**RIFE 插帧 → ESRGAN 超分 → 高质量重编码**（Video2X：高动态先插值再放大）。ComfyUI 不在则降级为仅重编码；失败保留原片。
- 接入：`cli.py enhance`；`run_series.py --enhance <档位>` / `config.quality.enhance_profile`（CLI 优先）。产物 `*_enhanced.mp4`。
- **`agent/comfy_models.py`：权重一律以 ComfyUI `/object_info` 实际扫到的为准**，缺失即回退+告警。
  曾踩「动漫默认推荐的模型本机根本没有 → 超分被静默跳过」的假成功。RIFE 自动选版本号最大的（本机 rife426）。
- **`quality_probe.py`**：VMAF/PSNR/SSIM 客观度量（ffmpeg 自带）。自比 98.1、crf38 劣化片 65.9。
- `--loudnorm`：EBU R128 -16 LUFS，多集发布响度一致。
- 实测：原成片 `moov` 在尾部（不能边下边播），增强后 `moov@36` ✓。

## 配音 / 发布
- `make_narration.py --film --out --fit-film --fps 24 [--series-script ... --ep N --shots N]`。B 站 `tid=172`（短片）绕过 21150。`_bili_upload_anime.py::upload_one`。
- 投稿元数据：`GET /api/metadata?ep=N`（`&llm=1` 才调模型）；CLI `cli.py metadata --ep N [--no-llm] [--preflight]`；逻辑 `agent/metadata.py`。

## WebUI
- Flask :8000；服务层 `services/`，视图 `views/`。关键路由：`/api/anchor`、`/api/hw`（默认只读）、`/api/series`、`/api/storyboard`、`/api/anchor/file`、`/api/metadata`。
- **启停统一 `supervise.py`**：看护 8188+8000，日志 `outputs/_webui.log` / `_comfy.log`。自愈链 `登录自启 → run_guard.bat → supervise.py → 两服务`。别再手动 `cli.py webui`。
- ⚠️ `schtasks.exe` 被沙箱拉黑 → 登录自启须用户手动跑 `register_autostart.bat`。
- `webui.py main()` 启动前 `socket.bind` 预检端口（防旧实例偷请求→新路由 404）；模块级注入 `NO_PROXY`。
- 路径穿越防护统一 `state.safe_under(base,name)`。config.yaml 不入库。

## 守卫 / 工具坑（高频）
- 访问本机服务必须 `NO_PROXY=127.0.0.1,localhost`。
- 后台服务用 `run_in_background`；ComfyUI 别用 `launch_comfy.py`（子进程被回收→8188 拒连）。
- 端口双绑定：旧实例挂 :8000 时新进程也能 bind 但请求被旧进程接走 → 全 `taskkill /F` 再起。
- ComfyUI venv 曾因 base 解释器改名起不来 → 改 `pyvenv.cfg` 指向托管 3.13。
- SAFE_DELETE 守卫：批量删 >50 会让 pytest rc=1（看 `N passed` 判绿）。
- Bash shim 缺 coreutils；`python -c` 内 `\n` 变字面 → 写文件再跑。
- 偶发 Edit 成功未落盘 → 改完 grep 复核；同文件并行 Edit 须串行。

## Lint / 测试 / Git
- `ruff check .`（`envs/default/Scripts/ruff.exe`，max-complexity 10）。**一律全仓跑**，单文件会漏「用了没 import」。
- 冻结基线：**新增 CLI 子命令 → 改 `tests/test_cli_registry.py`；新增路由 → 改 `tests/test_webui_routes.py`**。当前 `pytest -q` **446 passed**。
- 路径是 Junction → `D:\ai sheare\repo\ai_movie_agent`；远端 GitHub `cpufreestyle/ai_movie_agent`。推送 `git -c http.proxy= -c https.proxy= push`。禁 `git rebase`。
- Release：无 gh → GitHub REST API（token 走 `git credential fill`）；notes 发布前必须 grep 对齐 CLI/配置键真名。查 CI `GET /actions/runs?per_page=10`。

## 收尾
EP1-5 均出片+配音+已发 B 站。
