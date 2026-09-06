# B 站误传视频删除 + 标题准确性门禁
> Task version: v1
> Status: planning

## Goal
删除漏写「第二集」的重复误传视频 `BV13PtB6AEyg`，并修复发布管线丢集数的根因，落实用户规则「以后标题要准确才发」：发布前加标题校验门禁，并真正落地删除接口（替换 `publisher.py` 里 `delete_video` 的空壳）。

## Background / Confirmed Facts
- 账号 uid = `108682014`；登录态 `outputs/cookies.json`（`bili_jct`=CSRF、`SESSDATA`、`DedeUserID`）；外网走代理 `http://127.0.0.1:7897`。
- 《看见未来之前》共 4 个投稿：
  | bvid | 标题 | 时长 | 处置 |
  |---|---|---|---|
  | BV1Ndti6UETo | 《看见未来之前》 · 第一集 | 01:02 | ✅ 保留 |
  | **BV13PtB6AEyg** | **《看见未来之前》（无集数）** | 01:02 | ❌ **待删**（漏写第二集的重复误传，比正确第二集早 150 秒） |
  | BV1vdtq6qEQj | 《看见未来之前》 · 第二集 | 01:02 | ✅ 保留 |
  | BV1Ef896tEcx | 《看见未来之前》 | 00:27 | ⚠️ 早期测试片，删除与否未确认（默认先不删） |
- 用户确认要删的是「发布的 b 站视频」= `BV13PtB6AEyg`。
- 已验证可用接口（详见 `BILIBILI_HANDOFF.md`）：
  - 拉信息 `GET https://api.bilibili.com/x/web-interface/view?bvid=`；
  - 编辑 `POST https://member.bilibili.com/x/vu/web/edit`（JSON body + `?csrf=` + WBI 签名），已成功把两集标题改中文「第一集/第二集」；
  - 列投稿 `GET https://api.bilibili.com/x/space/arc/search` + WBI 签名。
- **删除接口全部 404**（路径已失效）：`/x/vu/web/del`、`/x/vu/web/del/v2`、`/x/vu/web/recall`、`/x2/creative/web/archive/del`、`/x2/creative/web/archive/delete`（form/json、bvid/aid/bvids 各种组合都试过）。
- **根因**：`agent/publisher.py` 的 `publish_latest()`(145–149) 把 `title=agent_state["title"]`（纯片名）传入 `upload()`，因 `upload()` 里 `title or title_template` 的 `title` 非空 → `title_template`（`{title} · 第{n}集`）永不拼集数 → 第二集被投成无集数（`BV13PtB6AEyg`）。`delete_video()`(152) 是空壳（注释明说 biliup-rs 无 delete 子命令）。
- 交接文档：`BILIBILI_HANDOFF.md`（项目根目录）。

## Requirements
- **R1** 删除 `BV13PtB6AEyg`（通过找到的当前真实删除端点）。
- **R2** 标题准确性门禁：在 `upload()` 真正调用 biliup 之前，校验标题必须含「片名」与「集数标记」（如 `第N集` / 中文数字集数），不达标则阻断投稿。
- **R3** 修复 `publish_latest` 永远带集数（中文数字「第一集/第二集」，与用户偏好一致）。
- **R4** 用找到的端点实现真实 `delete_video(bvid)`，替换空壳；`upload_and_replace` 的下架旧片真正生效。
- **R5** 保留 `BV1Ndti6UETo` / `BV1vdtq6qEQj`；`BV1Ef896tEcx` 默认不删（待确认）。

## Acceptance Criteria
- **AC1** 删除调用后，`view?bvid=BV13PtB6AEyg` 返回「稿件不存在」，且 `arc/search` 列表数由 60 → 59。
- **AC2** 发布时若 `title` 缺集数/片名，`upload()` 返回 `ok=False` 且**未**启动 biliup 子进程（日志含「标题不准确，已阻断投稿」）。
- **AC3** `publish_latest` 产出的 `title_text` 形如「《看见未来之前》 · 第二集」（中文数字集数）。
- **AC4** `delete_video(bvid)` 返回 `ok=True` 且真实删除；`upload_and_replace` 的旧片被删。

## In Scope
`agent/publisher.py` 的 `upload` / `publish_latest` / `delete_video` / `upload_and_replace` / 新增 `validate_title`；`config.yaml` 的 `publish.title_template`；删除端点调研与落地。

## Out of Scope
`BV1Ef896tEcx` 删除（待确认）；WebUI 文案改动；biliup-rs 二进制升级；创建新投稿流程。

## Risks / Deferred Items
- 删除端点未知：需抓现网前端 XHR 或查 `bilibili-api-python` 的 `Video.delete()`。若长期找不到，退回用户手动网页删除。
- 风控：删除不可恢复，先仅删 `BV13PtB6AEyg`，确认无误再考虑测试片。
- Cookie 可能过期：`cookies.json` 失效会导致删除 `401`/未登录，需先验证登录态。

## Open Questions
- **Q1** `BV1Ef896tEcx`（00:27 测试片）是否一并删除？
- **Q2** 删除接口的真实路径？（需调研）

## Version History
- v1 — 2026-09-05 初次创建，由 `BILIBILI_HANDOFF.md` 收编。
