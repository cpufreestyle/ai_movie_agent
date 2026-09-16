# HANDOFF.md — ai_movie_agent 交接总览

> 给接手 Agent 的「地图」。详细约定以 `.workbuddy/memory/MEMORY.md`（权威长期记忆）为准，本文件只给脉络与高频坑。
> 最后同步状态：release **`v0.13.0`**（2026-09-16，tag `v0.13.0` → 提交 `83bb132`）；HEAD `e5a80a9`（记忆批次，与 `origin/main` 一致）；工作树干净。

---

## 0. 项目是什么

**一句话**：把「一句话创意」变成一个**无真人脸的 2D 动漫短剧**——自动生成世界观 / 分镜，再用 **Blender 白模(blocking) 控制走位** + **MiniMax H3 扩散引擎出片** + 配音字幕，最终发布到 B 站 / 魔搭创空间。

- 成品：《看见未来之前》三集（进城 / 觉醒 / 对抗前主人），已发布；**第 4 集「记忆空间」（白模续集）已出片**（`outputs/ep4_series_film_mmh3.mp4`），待配音发布。
- 硬性用户要求：**成片禁止出现真人脸**，且反对全片模糊；画风由参考图定（动漫锚定图 → 动漫风）。

---

## 1. 当前进度 / 交付物（已达成）

| 项 | 状态 | 位置 / 链接 |
|---|---|---|
| 三集成片（mmh3 原片 + 英文旁白 + 中英双语字幕） | ✅ | `outputs/videos/epN_vo_film_mmh3.mp4`（1024×576 / 24fps / 59s） |
| EP4「记忆空间」（6 镜，白模走位续集） | ✅ 出片 | `outputs/ep4_series_film_mmh3.mp4`（768×448 / 24fps / ~11.5s / 自带音轨）；分镜 6 段见 `outputs/series_shots.json` 的 `ep4` |
| B 站发布（分区 tid=172 短片） | ✅ | EP1 `BV17uYi6UEPx` / EP2 `BV1ouYi6mE8Z` / EP3 `BV1ouYi6mEwL` |
| 魔搭创空间部署 | ✅ Running | https://www.modelscope.cn/studios/Michaelqiu/ai-movie-agent |
| 代码质量 | ✅ CI 全绿（py3.10/3.12 双矩阵）、`ruff check .` 绿、圈复杂度 C901=0、测试基线 ~424 passed | `.github/workflows/ci.yml` |
| 参赛报名 / 提交 | ⏳ 需**用户本人登录** | 小红书发帖、官网提交链接、魔搭开发者实践发手记（Agent 无法代登） |

> 比赛报名/提交窗口是 09-14；当前 09-16 已过窗口期，但交付物（成片+Space）均已完成，仅剩上述需登录的运营动作。

---

## 2. 仓库结构与关键入口

- `cli.py` — 命令行总入口（`cli.py mmh3` / `ltx` / `run` / `publish` / `preflight` / `tts` / `mix` …）。
- `run_series.py --engine mmh3` — 系列连贯出片。独立 venv `d:/ai sheare/repo/ai管理/.venv` 可用；**托管 venv 也可**（`C:/Users/michael/.workbuddy/binaries/python/envs/default/Scripts/python.exe`，2026-09-16 实测跑通 `--ep 4 --engine mmh3`）。
- `agent/` — 核心：`agent.py`(编排) / `mmh3_engine.py`(H3 引擎, 内建 Fun Control) / `blocking.py`(Blender 白模+走位) / `ltx_engine.py` / `publisher.py`(B站) / `preflight.py` / `align.py` / `tts.py` / `audio_mix.py`。
- `webserver/`（`state.py` / `services/` / `views/`） + `webui.py`（:8000）+ `webui/pipeline.html` — WebUI。
- `gen_blocking.py`（白模 depth/走位控制视频）、`run_h3_funcontrol.py`（Fun Control 真机验证）、`make_narration.py`（配音字幕）、`upscale_video.py`（4x 超分）、`download_ms.py`（ModelScope 拉权重）。
- `config.example.yaml` 入库；**`config.yaml` 不入库**（WebUI 会把 key 写回它）。缺失时回退读模板。
- **`test_stop_and_funcontrol.py` / `tests/test_*.py`** — 行为锁，CI 跑。
- 深度参考：`docs/h3_blocking_guide.md`（白模→H3 走位全记录）、`BILIBILI_HANDOFF.md`（B站发布专文）、`docs/参赛手记_电影Agent.md`、`docs/小红书报名文案.md`。

---

## 3. 环境（高频坑都在这）

- **GPU**：RTX 5070 Ti 16GB (sm_120)。ComfyUI 在 `D:/ComfyUI`，监听 `:8188`（GGUF+LTXVideo+T8/VideoHelperSuite）。模型根 `E:/ComfyUI_models/`。
  - 🔧 **起服务别用 `launch_comfy.py`**（2026-09-16 实测）：它用 `DETACHED_PROCESS` 起 ComfyUI，但沙箱会在前台命令结束时**回收整棵进程树** —— 日志停在 `Starting server …` **无任何报错**，8188 随即拒连，上游误报「ComfyUI 未就绪」。改用**常驻后台任务**：

    ```bash
    # cwd=D:/ComfyUI，清空 PYTHONPATH，带 NO_PROXY
    D:/ComfyUI/venv/Scripts/python.exe D:/ComfyUI/main.py \
      --listen 127.0.0.1 --port 8188 --fp32-vae --use-sage-attention
    # 环境：TORCH_COMPILE_DISABLE=1 / NO_PROXY=127.0.0.1,localhost / PYTHONPATH=
    ```

    实测 16s READY 且可持续出片。
  - 🔧 **venv 的 base 解释器可能已失效**（2026-09-16 实测修复）：`D:/ComfyUI/venv/pyvenv.cfg` 原写 `home = D:\Program`、`executable = D:\Program\python.exe`（3.13.15），而该目录已被改名成 `D:\Program.backup` → 报 `did not find executable at 'D:\Program\python.exe'`（且备份里的 python.exe 自身 prefix 仍指向 `D:\Program`，独立跑同样 `Failed to import encodings`，**不能直接拿来用**）。
    **修法（可回滚，已留 `pyvenv.cfg.bak`）**：把 `home`/`executable` 改指现存完整 3.13（`C:\Users\michael\.workbuddy\binaries\python\versions\3.13.12\`）。3.13.x 内 ABI 兼容（cp313），venv 的 site-packages（torch 2.11.0+cu130 / sage）**无需重装**。
- **cu130 必须**（cu128 → CUDA 禁用 → latent 全噪）。
- **Python**：主 venv `d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe`；WebUI 用它。托管 Python `C:/Users/michael/.workbuddy/binaries/python/envs/default/` 装了 `ruff.exe`（**跑 lint 用这个，别 `python -m ruff`**），也能跑 `run_series.py`。
- **本机服务必须绕代理**：env 里 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:13761` 且 `NO_PROXY` 为空 → 访问 `127.0.0.1:8188` 会被送去代理、回 **502**（"服务在跑却探不到"的假故障）。跑 `run_series.py` / 任何探测前一律 `NO_PROXY=127.0.0.1,localhost`（或 `ProxyHandler({})`）。
- **代理对 GitHub 时通时不通**（最坑）：
  - 先试普通 `git push origin main`（全局默认代理 `127.0.0.1:7897`，多数时候可用）。
  - 失败再试 `git -c http.proxy= -c https.proxy= push origin main`（直连，部分时段可用；09-16 实测走这条成功）。
  - 同一端口会时通时不通，两条都试一遍；PowerShell 的 `curl` 是别名，用 `curl.exe`。
  - ⚠️ **`git fetch` 直连可能失败而 `git push` 直连成功**（09-16 实测）→ 别用 `git fetch` 的结果判断「tag/远端有问题」，校验 tag 走 API。
  - **禁止 `git rebase`**（曾丢 `.git`）；**新 clone 后先 `git config core.autocrlf true`**。

---

## 4. 绝不能违反的硬约定（改错会白干/炸 CI）

1. 🚫 **不做真人脸检测**：`qa.face_check` 恒为 `false`，不把它当发布闸门、不在流水线跑人脸检出。「真人脸」只从**源头**解决（G 阶段提示词锁动漫 + 动漫风格锚定图）。我曾提议加闸门被**否决**。
2. ✅ **白模走位靠 H3 Fun Control**（不是首帧、不是 ref_video）。`strength` 主杠杆 **0.8~1.2（<1.5）**；走位须落在相机视野内（static/no-track 相机约 ±2.2，否则出画丢信号）。幅度瓶颈在 FunControl 侧，增益只能补一部分。
3. ⚠️ **`tools/blender_server.py` 的 `import bpy` 必须保留**（已 `# noqa: F401`）：`_exec()` 是 `exec(code, globals())`，远端脚本依赖全局 `bpy`。
4. 🔧 **硬件档位 `HW_TIER`** 用 `config_env.normalize_tier()` 归一别名，别各处硬列档位名；判定单一真相源 = `explain_tier()`。
5. 🔒 **`config.yaml` 不入库**；新增顶层配置段先动 `config.example.yaml` 与 `state._config_read_path()` 回退逻辑。
6. 🧪 **凡「CI 红但本机绿」先疑平台差异**（路径/编码/换行）。`tests/fixtures/mmh3_wf_fingerprint.json` 必须跨平台可复现（`_norm` 要把整个临时目录前缀换成 `TMPDIR` 并 `\`→`/`）。
7. 🗑 **批量删除走 `os.system("del /q")` 或 Python**，且 SAFE_DELETE 守卫会让 `pytest` 退出码失真（判绿看 stdout 的 `N passed` 而非 rc）。
8. 🧠 **权威长期记忆 = `.workbuddy/memory/MEMORY.md`**（`.codebuddy/memory` 已并停、仅留作备份）。交接/续做前先读它。
9. 📝 **文档/发布 notes 里引用 CLI 开关或配置键前，先 `grep` 代码确认真实拼写**。曾把 `--free-every` 凭印象写成 `--vram-every` 并进了公开 release notes（已用 API `PATCH` 修正）。

---

## 5. 常用命令

```powershell
# 起 ComfyUI（常驻；必须 NO_PROXY，别用 launch_comfy.py）
$env:NO_PROXY='127.0.0.1,localhost'; $env:PYTHONPATH=''; $env:TORCH_COMPILE_DISABLE='1'
& 'D:/ComfyUI/venv/Scripts/python.exe' 'D:/ComfyUI/main.py' --listen 127.0.0.1 --port 8188 --fp32-vae --use-sage-attention

# lint（用托管 ruff）
& 'C:/Users/michael/.workbuddy/binaries/python/envs/default/Scripts/ruff.exe' check .

# 测试（safe_delete 守卫会让 rc 失真，看 passed 数）
& 'd:/ai sheare/repo/ai管理/.venv/Scripts/python.exe' -m pytest tests -q --basetemp=.pytest_tmp
python tests/smoke_test.py

# 单镜重渲（必须 --force，否则命中 manifest 缓存跳过）
python run_ltx25_multishot.py --shot N --force --concat

# 系列连贯出片（EP4 实测跑通；--free-every N 每 N 个新渲染镜释放一次显存）
& 'd:/ai sheare/repo/ai管理/.venv/Scripts/python.exe' run_series.py --engine mmh3 --ep 4 --anchor auto --free-every 3

# 配音字幕（成片已自带；重做用）
python make_narration.py --film outputs/videos/epN_vo_film_mmh3.mp4 --out <out> --fit-film --fps 24 --series-script outputs/series_script.json --ep N

# 推送（代理不稳，先直推；失败再直连）
git push origin main
git -c http.proxy= -c https.proxy= push origin main
```

---

## 6. 可继续的方向（接手后可选）

- **EP4 收尾**：出片已 OK，可接 `make_narration` 配旁白字幕、再考虑发布。
- **白模走位幅度**：FunControl strength 与 `fun_control_walk_gain`（硬顶 1.0）只能补一部分，瓶颈在 FunControl 侧——可研究更稠密的条件注入或换控制类型。
- **长片防 OOM**：`run_series --free-every N` 已接（EP4 实测生效），可继续压显存占用。
- **运营动作**：小红书/官网/魔搭手记需用户登录，可提醒用户完成。
- **README / docs 同步**：结构已对齐，但功能演进快，文档易过期，改代码顺手补。

> 接手第一件事：**读 `.workbuddy/memory/MEMORY.md` 全文**，再动手。改动前 `git status` 确认没有「无故 D」（`webui/pipeline.html` 等曾无故从磁盘消失，`git checkout --` 恢复）。
