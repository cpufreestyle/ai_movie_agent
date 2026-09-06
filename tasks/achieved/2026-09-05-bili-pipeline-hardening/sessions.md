# Sessions

| 字段 | 值 |
|------|-----|
| 平台 | Codely CLI |
| 代码工作目录 | `D:\ai sheare\repo\ai_movie_agent`（C 盘入口 junction：`C:\Users\michael\CodeBuddy\ai_movie_agent`） |
| 任务工件目录 | `tasks/2026-09-05-bili-pipeline-hardening/`（归档于 tasks/achieved/） |
| Task 版本 / 阶段 | v1 / completed |
| 执行跨度 | 2026-09-04 ~ 2026-09-05，多个会话；Primary：本 Codely 会话 |
| 关键上游输入 | `BILIBILI_HANDOFF.md`（另一 agent 交接：卡点、已验证接口、WBI/cookie 用法） |
| 复跑命令 | `python -m py_compile agent/publisher.py agent/agent.py cli.py webui.py run_wan22_multishot.py`；`python _test_title_gate.py` |

## 会话摘要

1. 09-04 会话 A：按交接文档实现门禁/集数修复/真实删除端点；实测 340022 风控 → 挖出 /x/web/archive/delete 与 /x/risk/archive/del；稿件管理 API + WebUI 上线。
2. 09-04 会话 B：用户决定放弃自动删除 → 四文件回退为指引版；顺手清理前端重复 JS。
3. 09-04~05 会话 C：Wan2.2 灰片排查结案（D: 盘视图翻转为根因）；run_wan22_multishot.py 加模型预检、/view 拉取、双目录查找。
4. 09-05 会话 D（本会话）：$taskflow 补记 PRD/Spec/Plan、全量复核验证、归档。

## 环境注意事项（接手者必读）

- D: 盘存在文件系统视图翻转（模型/产物对新句柄间歇不可见；ComfyUI 进程旧句柄正常）——生成前必须过 `check_models()`，产物一律走 `/view`。
- Clash TUN 会劫持回环 HTTP（无监听端口返回 502）；B 站外网请求需代理 127.0.0.1:7897。
- B 站侧存在 -799 风控冷却（当日 API 调用频繁触发）。
