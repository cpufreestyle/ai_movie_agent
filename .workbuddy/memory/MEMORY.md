# 项目长期记忆：ai_movie_agent
权威目录 `.workbuddy/memory/`（旧 `.codebuddy/` 已合并停止跟踪）。

## 环境 / 出片
- GPU RTX 5070 Ti 16GB (sm_120)。ComfyUI `D:/ComfyUI` :8188（venv python；GGUF+LTXVideo+T8+VideoHelperSuite）。**起服务前看「守卫」两条**。
- 主 venv：`d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe`（cu130 必须）。代理 127.0.0.1:7897。
- 引擎：`engine.backend=comfyui_mmH3`（默认，INT4，须 `comfyui-minimax-h3-audio-T8`+VideoHelperSuite）。入口 `run_series.py --engine mmh3`。LTX-2.5 / Wan2.2 备选。
- 走位：用 **H3 Fun Control**（depth，strength 0.8~1.2，<1.5 崩坏）；白模走位须落视野内（±2.2）。`blender.use_as_fun_control:true` 全链路接入。

## 无真人脸 / 动漫化（硬性）
- H3 原片 → `anime_redraw.py` 逐帧重绘（Counterfeit-V3.0, denoise .70）→ `make_narration` 加旁白 → faststart。固定同一种子保连贯。
- 🚫 不做真人脸检测：`qa.face_check` 恒 false，不进 preflight 闸门（用户否决）。默认风格日式动漫 `config.project.style`。

## 配音 / 发布
- `make_narration.py --film --out --fit-film --fps 24 [--series-script outputs/series_script.json --ep N --shots N]`。B 站 `tid=172`（短片）绕过 21150。`_bili_upload_anime.py::upload_one`（cookies.json + buvid3）。

## WebUI
- `webui.py`(Flask) :8000；服务层 `services/`，视图 `views/`，路由断言 `tests/test_webui_routes.py`(46)。关键：`/api/anchor`(三视图/场景图)、`/api/hw`(档位,默认只读 `?detect=1` 才探)、`/api/series`(尾帧续写)、`/api/storyboard`(validate)、`/api/anchor/file`(缩略图,经路由回传)。
- **启停统一用 `supervise.py`（2026-09-18 新增）**：看护 ComfyUI(:8188)+WebUI(:8000)，进程死自动拉起，日志落 `outputs/_webui.log`/`outputs/_comfy.log`（按 5MB 轮转）。**别再手动 `cli.py webui` 起单次实例**——沙箱回收后就不会自愈。
  - 多层自愈链：`登录自启 → run_guard.bat(外层 respawn) → supervise.py → 两服务`。命令行 `--status`(探一次) / `--once`；内置自看门狗线程（主循环 >300s 无心跳自退交外层重启）。配套脚本：`start_all.bat`(前台单次) / `run_guard.bat`(无限 respawn) / `status.bat` / `register_autostart.bat`+`unregister_autostart.bat`(schtasks onlogon)。
  - ⚠️ `schtasks.exe` 被沙箱拉黑，**登录自启无法在沙箱内执行**，需用户手动跑 `register_autostart.bat`。
- `webui.py main()` 启动前 `socket.bind` 预检端口占用 → 占用则报错+taskkill 提示并退出（消灭旧实例未退、新路由 404 陷阱）；模块级注入 `NO_PROXY=127.0.0.1,localhost`。
- `/api/anchor/run` 生成前 `comfy_ready()` 前置自检，未就绪直接 409+「ComfyUI 未连接」文案。
- **投稿元数据**：`GET /api/metadata?ep=N`（默认规则法即时返回，`&llm=1` 才调模型）；CLI `python cli.py metadata --ep N [--no-llm] [--preflight] [--json]`；逻辑在 `agent/metadata.py`（LLM + `rule_metadata()` 兜底，`enforce_limits()` 收敛到 B 站约束）。**新增 CLI 子命令要同步 `tests/test_cli_registry.py` 的 expected，新增路由要同步 `tests/test_webui_routes.py` 的 EXPECTED**（两处都是冻结基线）。
- 路径穿越防护统一 `state.safe_under(base,name)`：先 `\`→`/` 再 normpath（Windows/CI 不一致坑）。
- config.yaml 不入库，只留 config.example.yaml。

## 守卫 / 工具坑（高频踩）
- **访问本机服务必须 NO_PROXY=127.0.0.1,localhost**（env 注入 HTTP_PROXY 会致 8188 探不到→502）。
- **WebUI/ComfyUI 须作常驻后台任务跑**：`run_in_background` 起；实测 WebUI ~15h 后被回收需重起；ComfyUI 别用 `launch_comfy.py`（DETACHED 子进程被沙箱回收→8188 拒连）。日志写确定绝对路径（如 `outputs/_webui.log`）。
- **端口双绑定陷阱**：旧实例仍挂 :8000 时新进程也能 bind 但请求被旧进程接走→新路由 404。排查 `netstat -ano -p TCP` 列全部 :8000 PID，全 `taskkill /F` 再起一个。
- **ComfyUI venv base 解释器没了**：`pyvenv.cfg` 的 home/executable 指向被改名的 `D:\Program` → 改指向现存 3.13（托管 `.../python/versions/3.13.12/`），site-packages 无需重装。
- SAFE_DELETE 守卫：批量删>50 会让 pytest rc=1（看 `N passed` 判绿）；CI(Linux)无此守卫。
- Bash shim 缺 coreutils（ls/head/dirname 报 127）：结果落文件用 Read 读；`python -c` 内 `\n` 变字面 `/n` → 写文件再跑。
- `schtasks.exe` 被沙箱**程序黑名单**拦截（`PROGRAM BLOCKED BY SECURITY POLICY`，明令不得重试/绕过）→ 计划任务（登录自启等）一律由用户手动运行对应 `.bat` 注册。
- 偶发 Edit 成功未落盘：改完 grep/read 复核；并行 Edit 同文件须串行。

## Lint / 测试
- `ruff check .`（托管 venv `envs/default/Scripts/ruff.exe`；`line-length 100, max-complexity 10, select E9/F63/F7/F82 + F全类 + C901`）。全仓 C901=0。
- **改完一律跑整个仓库 `ruff check .`**，别只 check 自己改动的文件：「用了某个名字却没 import」
  这类错误（如 `webui.py` 里 `sys.exit(1)` 但没 `import sys`）在单文件检查里照样过，只有全仓跑才暴露；
  漏了会直接炸 CI。测试基线 `pytest -q` **430 passed**；`python tests/smoke_test.py` 50 pass。

## Git / Release
- 路径是 Junction → `D:\ai sheare\repo\ai_movie_agent`。远端 GitHub `cpufreestyle/ai_movie_agent`。latest `v0.13.0`(83bb132)。
- 推送：先试代理 7897（不加 `-c`）；直连 `git -c http.proxy= -c https.proxy= push` 备用；同一端口会时通时不通，按序都试。禁 `git rebase`（曾丢 `.git`）。新 clone `git config core.autocrlf true`。
- Release：无 gh → GitHub REST API（token 走 `git credential fill`）；notes 发布前必须 grep 代码对齐 CLI/配置键真名（v0.13.0 曾把 `--free-every` 误写 `--vram-every`）。
- 无 gh 查 CI：`GET /actions/runs?per_page=10`（sha 传完整 40 位），`/jobs` 看每 step；logs 302 去 Authorization。

## 收尾
EP1-5 均出片+配音+已发 B 站。
