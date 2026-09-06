# Plan — B 站误传视频删除 + 标题准确性门禁
> Task version: v1
> Status: planning

## Spec Pointers
- No spec required — 改动集中在 `agent/publisher.py` 单文件 + 一个删除端点调研，边界清晰、单 agent 可完成，无需架构级 Spec。

## Reference Pointers
- `BILIBILI_HANDOFF.md`（项目根目录交接文档）：含已验证接口模式（WBI 签名 / edit / view / arc-search）与删除端点排查路线。

## Related Tasks
- Depends on: None
- Blocks: None
- Related: None

## Skills / Tools Used (Optional)
- TaskFlow（本任务框架）
- B 站 API 调研：浏览器自动化（抓 member.bilibili.com 删除按钮 XHR）或读 `bilibili-api-python` 的 `Video.delete()` / `biliup-rs` 源码

## Preconditions
- `outputs/cookies.json` 有效（含 `bili_jct`）。
- 代理 `http://127.0.0.1:7897` 在线。
- `tools/biliup/biliupR-v0.2.4-x86_64-windows/biliup.exe` 存在（用于 `show` 取文件名）。

## Approval
- Approved by: （待用户批准）
- Approved at: YYYY-MM-DD HH:mm +08:00
- Approved version: v1
- Approved scope: PRD / Plan

## Steps

### Step 1 — 定位当前可用的删除端点
- Goal: 找到 B 站当下真实用于删除已投稿视频的 POST 端点与参数/鉴权方式。
- Dependencies: cookies 有效。
- Files: （调研，无代码改动）
- Implementation:
  1. 用浏览器自动化技能，注入 `cookies.json` 登录 `https://member.bilibili.com`，进「创作中心 → 稿件管理」，对 `BV13PtB6AEyg` 点「删除」，在 Network 面板抓取真实 XHR URL + 请求体。
  2. 备选：读 `nemo2011/bilibili-api-python`（`main` 分支）的 `bilibili_api/video.py` 中 `delete()` 方法所用端点。
  3. 备选：查 `biliup/biliup` `crates/biliup/src/uploader/bilibili.rs` 是否已实现删稿。
- Acceptance: 得到可复现的 URL + 参数（bvid/aid/csrf 等）+ 鉴权方式。
- Verification: 脚本对 `BV13PtB6AEyg` 调用，返回 `code=0` 或之后 `view` 报「稿件不存在」。
- Rollback: 找不到则返回手动网页删除路线，不强行实现。
- Status: pending

### Step 2 — 实现真实 `delete_video`
- Goal: 用 Step1 的端点落地删除。
- Dependencies: Step 1
- Files: `agent/publisher.py`（`delete_video`、`upload_and_replace`）
- Implementation: 以 cookie/CSRF 调真实端点；成功返回 `ok=True`，失败返回明确错误。
- Acceptance: `delete_video("BV13PtB6AEyg")` 返回 `ok=True` 且 `view` 返回稿件不存在。
- Verification: `arc/search` 列表数由 60 → 59；其余三集完好。
- Rollback: 保留空壳版本，不接风险调用。
- Status: pending

### Step 3 — 标题准确性门禁
- Goal: 发布前校验标题，不达标阻断。
- Dependencies: 无
- Files: `agent/publisher.py`（新增 `validate_title`、`upload` 调用点）
- Implementation: 在 `upload()` 执行 `subprocess` 投稿前加 `validate_title(title_text, episode, film_title)`：
  - 必须包含片名（如 `《看见未来之前》`）；
  - 必须包含集数标记（`第\d+集` 或中文数字集数）；
  - 基础长度/违禁词检查；
  - 不达标返回 `{"ok": False, "error": "标题不准确，已阻断投稿"}`，**不调用 biliup**。
- Acceptance: 缺集数/片名的标题被拦截，biliup 进程未启动。
- Verification: 手动传坏标题验证返回；传好标题验证放行。
- Rollback: 移除校验逻辑。
- Status: pending

### Step 4 — 修复 `publish_latest` 集数丢失
- Goal: 让发布永远带中文数字集数。
- Dependencies: Step 3
- Files: `agent/publisher.py`（`publish_latest`）
- Implementation: `publish_latest` 不再直接传纯片名，改为拼集数（如 `f"{film_title} · 第{ep_cn}集"`），或在 `upload` 内保证 `title_template` 生效；与用户偏好中文数字一致。
- Acceptance: 产出的 `title_text` 形如「《看见未来之前》 · 第二集」。
- Verification: 调用后检查 `title_text` 内容。
- Rollback: 回退修改。
- Status: pending

## Checkpoints
- After Step 2：确认 `BV13PtB6AEyg` 删除成功且 `BV1Ndti6UETo`/`BV1vdtq6qEQj`/`BV1Ef896tEcx` 完好。
- After Step 4：端到端跑一次发布（可用草稿/不真投），确认标题带集数且门禁生效。

## Verification / Review
- 逐项核对 PRD 的 AC1–AC4。
- 检查 `agent/publisher.py` 改动范围，无无关改动。
- 删除是不可恢复操作，Step 2 前再次口头/日志确认仅删 `BV13PtB6AEyg`。

## Follow-ups
- Q1 `BV1Ef896tEcx` 是否也删（待用户确认）。
- 若 B 站删除端点后续再有变动，更新 `delete_video` 与 `BILIBILI_HANDOFF.md`。

## Version History
- v1 — 2026-09-05 初次创建。
