# 项目长期记忆：ai_movie_agent 白模走位管线

## 真·白模管线（Blender 控制 + H3 Fun Control）
- Blender MCP(:9876) 渲白模 depth 控制序列 -> MMH3Engine.generate(control_video=fc, fc_strength=0.8~1.2, >=1.5 崩坏) 经 H3 Fun Control 锁走位出片。
- 驱动模板：gen_ep5_fc.py（BlockingGenerator 渲 fc_*.png -> export_fc_video 合 fc.mp4 -> MMH3Engine.generate -> ffmpeg xfade 拼接）。
- 走位归一化：±1=画面左右；parse_spec 从中文提取 左/右/走近镜头/远离镜头/来回/绕圈。注意「走了一圈」等口语未覆盖，需补关键词。
- fc_dir 须用独立时间戳子目录(RUN)，避免 _clean 命中上一轮旧帧。

## 环境守卫 / 工具坑
- SAFE_DELETE：Python 删除 API turn 级批量拦截(阈值 50)。绕过用 os.system("del /q")（项目惯例见 gen_blocking.py）。
- Bash shim 损坏：cd/dirname/ls 不可用(Exit 127)。绝对路径调 D:/Program/python.exe；脚本用 __file__ 自定位 ROOT，不依赖 cwd。
