# PRD — B 站发布管线加固与稿件管理

- Task ID: `2026-09-05-bili-pipeline-hardening`
- Task Version: v1
- State: checking
- 日期: 2026-09-05（工作实际发生于 2026-09-04 ~ 09-05 多个会话）

## 目标与用户价值

1. 修复发布管线"丢集数"根因，落实用户规则**「以后标题要准确才发」**：不准确的标题在投稿前被硬性阻断。
2. 为 WebUI 增加 B 站稿件管理：列出全部投稿、行内改标题；**自动删除按用户决定放弃**（风控人机验证无法脚本化），改走创作中心手动下架。
3. 为 Wan2.2 生成脚本增加 D: 盘故障防御（模型预检、/view 产物拉取），避免渲染 30 分钟产出灰片。

## 已确认背景事实

- 2026-09-04 交接文档 `BILIBILI_HANDOFF.md` 记录：线上出现无集数重复投稿 `BV13PtB6AEyg`；删除接口未知；`publish_latest()` 直传纯片名压制 `title_template`（`title or template` 短路）。
- 真实删除端点经创作中心前端 bundle 抓包确认：`POST member.bilibili.com/x/web/archive/delete`（form: bvid+csrf），但风控返回 `code=340022 验证码错误`，需人机验证，纯脚本不可绕过。
- D: 盘（Colorful CN600 2TB NVMe）存在文件系统"视图翻转"：模型/产物文件对新句柄间歇或持续不可见（同盘灰片根因），旧句柄（ComfyUI 进程）正常。
- 用户标题偏好：集数用中文数字（第一集/第二集）。

## 需求

1. R1 标题门禁：`upload()` 投稿前校验——非空、≤80 字、含片名（提供 film_title 时）、带集数标记（`第N集`/中文数字集数），不达标即阻断，不调 biliup。
2. R2 集数修复：`publish_latest()` 与 `publish_only()` 统一走 `title_template` 并转中文数字集数。
3. R3 删除：`delete_video()` 返回手动下架指引（放弃自动删除）；`upload_and_replace`/cli `--replace` 不再发起删除调用。
4. R4 稿件管理 API：`list_my_videos()`（WBI 签名分页）、`get_video_detail()`、`update_video()`（edit 接口 + biliup show 取服务端文件名 + 门禁校验）。
5. R5 WebUI：发布页签"稿件管理"区块（列表/行内改标题/删除按钮已移除），DOM 用 textContent 构建。
6. R6 Wan 脚本防御：`check_models()` 预检、产物经 `/view` API 拉取到项目 `outputs/shots/`、`find_shot_file` 双目录查找容忍计数漂移、concat 改用查找结果。

## 验收标准（可观察）

- A1 `python -m py_compile` 对 agent/publisher.py、agent/agent.py、cli.py、webui.py、run_wan22_multishot.py 全部通过。
- A2 门禁自测 8 项全过（中文数字 1/2/12/21/30 + 通过/拦截用例）。
- A3 `check_models()` 在模型不可见窗口正确返回 False 并列出缺失文件（实测成立）。
- A4 `/view` API 实测拉回完整产物字节（646063 bytes，HTTP 200）。
- A5 WebUI `/api/bili/videos` 链路打通（实测到达 B 站 API；返回 -799 为 B 站风控冷却，非代码问题）。
- A6 全代码库无 `deleteBiliVideo`/`/api/bili/delete`/自动删除调用残留（grep 清零）。

## 范围

**内**：上述 R1-R6；`launch_comfy.py` 固化 `--fp32-vae`。
**外**：`BV13PtB6AEyg` 手动删除（用户操作）；`BV1Ef896tEcx` 去留（待用户决定）；D: 盘硬件检修与备份（用户操作）；B 站 -799 风控冷却后的列表复测；git 提交（用户控制"同步仓库"节奏）；生成参数调优（用户持续迭代中）。

## 风险与遗留

- D: 盘故障未修复前，任何生成任务输出不可信（灰片）；修复后应复跑金丝雀验证。
- B 站列表接口因当日 API 调用频繁触发风控（-799），冷却后需复测一次确认可用。
- 未提交变更堆积（含用户 LTX 实验），建议尽快"同步仓库"留档。
