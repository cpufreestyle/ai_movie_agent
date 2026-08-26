# H 阶段 · 自动发布 — Skill 方法论

## 职责
把生成的最终影片自动投稿到 B 站。

## 方法论（固化自 biliup 投稿 SOP）
- 首次 `biliup login` 一次（扫码/密码，存 cookies.json，约 1~3 月有效）。
- 投稿参数：标题/简介/标签(`--tag`单数,逗号分隔)/分区`tid`/封面/动态/`--dtime`延时。
- 生成结束自动投稿（`config.publish.enabled`）或 `python cli.py publish` 单独投。

## 工具
`agent/publisher.py :: Publisher.upload(video, episode, title, logline)`
- 配置：`config.publish.{enabled, binary, title_template, tags, tid, ...}`

## 兜底
未装 biliup / 未登录 → 打印安装/登录指引，不中断管线。
