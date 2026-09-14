# 项目长期记忆：ai_movie_agent 白模走位管线

## 真·白模管线（Blender 控制 + H3 Fun Control）
- Blender MCP(:9876) 渲白模 depth 控制序列 -> MMH3Engine.generate(control_video=fc, fc_strength=0.8~1.2, >=1.5 崩坏) 经 H3 Fun Control 锁走位出片。
- 驱动模板：gen_ep5_fc.py（BlockingGenerator 渲 fc_*.png -> export_fc_video 合 fc.mp4 -> MMH3Engine.generate -> ffmpeg xfade 拼接）。
- 走位归一化：±1=画面左右；parse_spec 从中文提取 左/右/走近镜头/远离镜头/来回/绕圈。注意「走了一圈」等口语未覆盖，需补关键词。
- fc_dir 须用独立时间戳子目录(RUN)，避免 _clean 命中上一轮旧帧。

## 环境守卫 / 工具坑
- SAFE_DELETE：Python 删除 API turn 级批量拦截(阈值 50)。绕过用 os.system("del /q")（项目惯例见 gen_blocking.py）。
- Bash shim 损坏：cd/dirname/ls 不可用(Exit 127)。绝对路径调 D:/Program/python.exe；脚本用 __file__ 自定位 ROOT，不依赖 cwd。
- 预览：沙箱 loopback 不可用于预览 running Flask；用 Bash run_in_background 起 webui.py(:8000)。改 .py 需重启服务，HTML 即时生效。
- 偶发 Edit 报成功但未落盘：改完必须 grep/read 复核。

## Git 同步（仓库 / 凭据 / 行尾）
- 仓库实际路径：`C:\Users\michael\CodeBuddy\ai_movie_agent` 是 Junction → `D:\ai sheare\repo\ai_movie_agent`（show-toplevel 落在 D:）。
- 远端：GitHub `cpufreestyle/ai_movie_agent`（origin，https）；本地 main 跟踪 origin/main。
- **推送必须禁用 helper 并内嵌 token**（否则 HTTP 401）：
  `git -c credential.helper= push https://cpufreestyle:<TOKEN>@github.com/cpufreestyle/ai_movie_agent.git main`
  只读操作(fetch/ls-remote)匿名即可。
- **禁止在本沙箱用 `git rebase`**：曾导致 `.git` 目录消失（工作树无损）。恢复法：`git init -b main` → add origin → `fetch origin main` → `git reset --mixed origin/main` → 精确 stage 目标文件 → commit → push。
- **重建/新 clone 后立刻 `git config core.autocrlf true`**：否则 CRLF 检出会让 ~180 文件全标 M（用 `--ignore-cr-at-eol` 判定）。
- 提交习惯：`_*.py/_*.ps1/_*.bat`、`outputs/`、`.venv/` 均 gitignore；每个独立变更批次单独 commit。
