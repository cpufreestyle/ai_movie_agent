# Plan — B 站发布管线加固与稿件管理

- Task Version: v1
- State: completed（2026-09-05 13:40 验证全过：A1=0 / A2=ALL_PASS / A3 预检生效 / A6=0；A4、A5 已实测记录）

## Approval

- Approved by: user（聊天内逐项指令：「修复根因」「以后标题要准确才发」「把删除和修改标题功能加入 web ui」「放弃自动删除功能」「修复d盘」等，即范围批准）
- Approved at: 2026-09-04 20:xx ~ 2026-09-05 13:3x（跨会话）
- Approved version: v1
- Approved scope: PRD / Spec / Plan（本文件为 2026-09-05 补记，用户指令 $taskflow 要求规划/执行/验证/归档）

## Steps（全部 done；工作实际完成于 2026-09-04~05 各会话）

| # | Step | 目标 | 涉及文件 | 验收 | 状态 |
|---|------|------|----------|------|------|
| S1 | 标题门禁 + 中文集数 | validate_title/_cn_episode；publish_latest/publish_only 走模板 | agent/publisher.py, agent/agent.py | A1/A2 | done |
| S2 | 真实删除端点落地→后按用户决定放弃 | 端点情报留存 docstring；delete_video 改指引；upload_and_replace/cli --replace 移除调用 | agent/publisher.py, cli.py | A6 | done |
| S3 | 稿件管理 API | list/detail/update + WBI 签名 + biliup show | agent/publisher.py | A5 | done |
| S4 | WebUI 稿件管理 | 端点 ×3 + 前端区块（列表/行内改标题/无删除）+ 重复 JS 去重 | webui.py, webui/pipeline.html | A1/A5/A6 | done |
| S5 | Wan 脚本 D: 盘防御 | check_models 预检；/view 拉取主副本；find_shot_file 双目录；concat 修复 | run_wan22_multishot.py | A3/A4 | done |
| S6 | ComfyUI 启动固化 | launch_comfy.py 默认 --fp32-vae | launch_comfy.py | 编译/参数检查 | done |

## Verification（2026-09-05 13:3x 复核）

| 项 | 命令/方法 | 结果 |
|----|-----------|------|
| A1 编译 | `python -m py_compile` × 5 文件 | ✅ 全部 exit 0 |
| A2 门禁自测 | `_test_title_gate.py`（8 用例） | ✅ ALL_PASS: True |
| A3 模型预检 | `check_models()` 实测（翻转窗口） | ✅ 正确返回 False 并列出 3 个缺失文件 |
| A4 /view 拉取 | GET /view official webm | ✅ HTTP 200，646063 bytes 与磁盘一致 |
| A5 列表链路 | WebUI 8123 → /api/bili/videos | ✅ 到达 B 站 API（-799 风控冷却，非代码问题；冷却后复测） |
| A6 引用清零 | grep deleteBiliVideo/api/bili/delete/delete_video 调用 | ✅ 仅剩 delete_video 定义（指引版）；前端与端点清零 |

失败分类：无新增失败；-799 属环境（B 站风控）；D: 盘不可见属环境（已记录于项目记忆与 prd 风险）。

## Risks / Deviations

- 偏差：S2 实现真实删除后又按用户决定回退为指引版（端点情报留存），属用户批准的范围变更。
- 风险：D: 盘故障未修；B 站 -799 冷却未过；git 未提交变更堆积。

## Follow-ups（移交用户/后续任务）

1. `BV13PtB6AEyg` 创作中心手动删除（用户操作）。
2. `BV1Ef896tEcx` 去留待用户决定。
3. D: 盘备份 + CrystalDiskInfo 细项 SMART + 检修；修复后跑金丝雀验证。
4. B 站 -799 冷却后复测 `/api/bili/videos`。
5. 适当时机"同步仓库"提交全部变更（含本任务目录）。

## Rollback

- 各文件均为独立修改，可按 git 工作区逐文件 `git checkout -- <file>` 回退；无数据迁移、无配置格式变更。
