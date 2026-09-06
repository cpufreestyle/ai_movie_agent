# Spec — B 站发布管线加固与稿件管理

- Task Version: v1
- 定性：大任务（跨 publisher/agent/cli/webui/前端/生成脚本多模块，含接口契约变更）→ 本文件必填。

## 模块边界

| 模块 | 职责 | 不得越界 |
|------|------|----------|
| `agent/publisher.py` | 投稿、标题门禁、集数渲染、稿件列表/详情/编辑、删除指引 | 不做 UI；不写 config |
| `agent/agent.py` | `publish_only` 标题组装（模板+中文集数）后委托 upload | 不直接拼 biliup 命令 |
| `webui.py` | HTTP 端点：`/api/bili/videos`、`/api/bili/video/<bvid>`、`/api/bili/update`；直接构造 Publisher（不依赖 MovieAgent 初始化） | 不含业务逻辑 |
| `webui/pipeline.html` | 稿件管理 UI（列表/行内改标题），DOM 用 textContent（XSS 安全） | 不再含删除入口 |
| `run_wan22_multishot.py` | 生成前模型预检；产物经 `/view` 拉取到项目目录；双目录查找 | 不改用户调参 |

## 接口契约

### validate_title(title, episode, film_title) -> str | None
- 返回 None=通过；否则返回中文原因。
- 规则：非空且非空白；`len ≤ 80`；`film_title` 非空时必须包含；`episode` 非 None 时必须匹配 `第[0-9一二三四五六七八九十百]+集`。
- `_cn_episode(n)`：1→一、12→十二、21→二十一、30→三十；≥100 回退阿拉伯数字。

### publish_latest / publish_only
- 标题 = `title_template`（默认 `{title} · 第{n}集`）经 `_fill` 后将 `第{n}集` 替换为中文数字集数；再经门禁校验后投稿。

### delete_video(bvid)
- 恒定返回 `{"ok": False, "error": 手动下架指引}`，不发起任何网络请求。端点情报保留在 docstring（`/x/web/archive/delete`，form: bvid+csrf，无验证码令牌时 code=340022），供未来 B 站放开时恢复。

### 稿件管理 API（publisher 方法）
- `list_my_videos(page_size=30, max_pages=5)`：WBI 签名（mixin 表 + nav 取 img/sub key）调 `x/space/arc/search`，返回 `[{bvid,title,created,length,play}]`；mid 取自 cookies `DedeUserID`。
- `update_video(bvid, title, desc, tag)`：view 取详情/aid/cid → `biliup show` 取服务端文件名 → 从旧标题推断集数交门禁校验 → POST `member.bilibili.com/x/vu/web/edit?csrf=`（JSON body，videos=[{filename,title:"movie_final",cid}]）。
- `check_delete_risk(bvid)`：GET `/x/risk/archive/del`（保留，供未来决策）。
- 凭证：`outputs/cookies.json`（workdir 优先，项目根兜底）；代理链 `cfg.proxy → env → 127.0.0.1:7897`。

### WebUI 端点
- `GET /api/bili/videos` → `list_my_videos()` 原样 JSON。
- `GET /api/bili/video/<bvid>` → `get_video_detail()`。
- `POST /api/bili/update {bvid, title}` → `update_video()`；缺参返回 400。
- `/api/bili/delete` 已移除（前端无调用方）。

### run_wan22_multishot 防御流程
1. `main()` 非 concat 分支先 `check_models()`：三个模型文件存在且 >1MB，否则 `sys.exit(1)` 并打印缺失清单。
2. 生成 done 后：`GET {URL}/view?filename=<fn>&subfolder=<sub>&type=output` 拉取字节 → 写 `outputs/shots/<fn>`（主副本）→ 尾帧提取基于项目副本。
3. `find_shot_file(prefix)`：先 glob `outputs/shots`，后 glob `OUT_DIR`，`_*.webm` 模式容忍计数漂移，多命中取最新。
4. `concat` 的输入来自 `find_shot_file`，不再硬编码 `_00001_`。

## 错误语义

- 门禁拦截：`{"ok": False, "error": "标题校验未通过，已阻断投稿/修改: <原因>（标题: <title>）"}`，**不产生任何副作用**。
- B 站业务失败：透传 `code/message`（如 340022、-799）。
- `/view` 拉取失败（非 200/空 body）：`run_shot` 返回 None，流程中止并打印 HTTP 状态。

## 兼容性

- `upload()` 新增可选参数 `film_title`，向后兼容（默认 None，此时仅做基础校验）。
- 旧调用方（publish_concept 等不传 episode/film_title）不受门禁集数规则影响。

## 测试约束

- `python -m py_compile` 全量通过；门禁 8 项自测（`_test_title_gate.py`）通过；`check_models` 在翻转窗口实测 False；`/view` 实测 200 且字节数与磁盘一致；grep 确认删除功能引用清零。
