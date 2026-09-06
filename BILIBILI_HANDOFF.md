# B 站发布 / 标题问题 交接文档（2026-09-04）

> 目的：把当前卡住的问题、已验证可用的接口、卡点排查路线写清楚，让下一个 agent 能直接接着干。

---

## 0. 一句话目标
1. **删除** B 站上那个漏写「第二集」的重复误传视频 `BV13PtB6AEyg`（标题为 `《看见未来之前》`，时长 01:02，与正确的「第二集」同内容、仅早发布 150 秒）。
2. **修复根因**：发布管线在 `publisher.py` 把集数丢失了，导致视频投出去标题没带「第 N 集」。
3. **落实用户规则「以后标题要准确才发」**：发布前加一道标题准确性校验门禁，不达标就阻断投稿。

---

## 1. 当前 B 站账号实际状态（已用 API 核对，2026-09-04）
账号 uid = `108682014`（cookie 里 `DedeUserID`）。《看见未来之前》系列共 4 个投稿：

| bvid | 标题 | 时长 | 处理 |
|---|---|---|---|
| BV1Ndti6UETo | 《看见未来之前》 · 第一集 | 01:02 | ✅ 正确，保留 |
| **BV13PtB6AEyg** | 《看见未来之前》（无集数） | 01:02 | ❌ **待删除（重复误传）** |
| BV1vdtq6qEQj | 《看见未来之前》 · 第二集 | 01:02 | ✅ 正确，保留 |
| BV1Ef896tEcx | 《看见未来之前》 | 00:27 | ⚠️ 早期 27s 测试短片，疑似多余；**用户尚未确认要删**，先保留 |

> ⚠️ 删除不可恢复。本次只确认删 `BV13PtB6AEyg`。`BV1Ef896tEcx` 删除与否需再问用户。

---

## 2. 已验证可用的接口 & 调用模式（可直接复用）

### 网络前提
- 外网必须走代理 `http://127.0.0.1:7897`（HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 都设）。直连 B 站会超时/TLS EOF。
- `cookies.json` 位置：`c:\Users\michael\CodeBuddy\ai_movie_agent\outputs\cookies.json`
  - 结构：`cookie_info.cookies[]` 含 `name`/`value`；需要的字段：`bili_jct`（CSRF 令牌）、`SESSDATA`、`DedeUserID`。
  - 取 CSRF：`csrf = next(c['value'] for c in cookies if c['name']=='bili_jct')`
  - 拼 Cookie 头：`"; ".join(f"{c['name']}={c['value']}" for c in cookies)`
- 所有请求都带 `Referer: https://member.bilibili.com/` 和 `Origin: https://member.bilibili.com` 与 `Cookie`。

### WBI 签名（拉列表/需鉴权读取时要用）
```python
MIXIN_TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,
14,39,12,38,41,13,37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,
62,11,36,20,34,44,52]
def wbi_keys(ch):  # GET https://api.bilibili.com/x/web-interface/nav
    nav=json.loads(get("https://api.bilibili.com/x/web-interface/nav", ch))
    wbi=nav["data"]["wbi_img"]
    return (wbi["img_url"].rsplit("/",1)[1].split(".")[0],
            wbi["sub_url"].rsplit("/",1)[1].split(".")[0])
def mixin_key(img,sub): return "".join((img+sub)[i] for i in MIXIN_TAB)[:32]
def sign(params,img,sub):
    params=dict(params); params["wts"]=int(time.time())
    q=urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"]=hashlib.md5((q+mixin_key(img,sub)).encode()).hexdigest()
    return params
```

### ✅ 拉单视频完整信息（含标题/简介/标签/封面/cid）
`GET https://api.bilibili.com/x/web-interface/view?bvid={bvid}` — 无需 WBI。`data.title / data.desc / data.tag / data.pic / data.pages[0].cid`

### ✅ 编辑已投稿标题/简介/标签（这个能用！）
`POST https://member.bilibili.com/x/vu/web/edit?csrf={csrf}`
- `Content-Type: application/json`
- body（核心字段必填，缺则报 `21001`）：
```python
form = {
  "aid": d["aid"], "bvid": bvid, "title": NEW_TITLE,
  "copyright": d.get("copyright",1), "tid": d.get("tid",171),
  "source": "", "desc": d.get("desc","") or "", "cover": d.get("pic","") or "",
  "tag": d.get("tag") or "AI影视,人工智能,短片,AIGC,打赏,AI电影,开源项目",
  "dynamic": "AI 自动生成的实验短片", "dtime": 0, "mission_id": 0,
  "videos": [{"filename": SERVER_FILENAME, "title": "movie_final", "cid": cid}],
}
```
- `SERVER_FILENAME` 从 `biliup show {bvid}`（在 `outputs/` 目录下运行 `tools/biliup/biliupR-v0.2.4-x86_64-windows/biliup.exe`）拿到，形如 `n260904tx23q6gtl6z85ta3u8qjiznoe`。
- **成功返回 `{"code":0,...}`**。已用它把 第一集/第二集 标题从「第2集/无集数」改成中文「第二集/第一集」，验证可用。
- 注意：`view` 接口有数秒缓存，改完要等几秒再查。

### ✅ 列出自己全部投稿（带 WBI 签名）
`GET https://api.bilibili.com/x/space/arc/search?mid={uid}&ps=30&pn={pn}&order=pubdate` + WBI 签名参数 `w_rid`/`wts`，分页直到 `vlist` 为空。已验证可拉到全部 60 个投稿。

---

## 3. ❌ 卡点：删除已投稿视频的接口找不到（待下一个 agent 解决）

`agent/publisher.py` 的 `delete_video()`（152 行）是**空壳**，注释明说「biliup-rs 不支持删除」。因此必须走网页接口。

### 已试过、全部返回 HTML 404（路径真的不存在）的端点
（同一份代码、同一代理、同一 cookie，`edit` 能成，说明代理/鉴权没问题，是路径失效）
- `POST https://member.bilibili.com/x/vu/web/del?csrf=...`  form/json {bvid} / {bvids} / {aid} → 404
- `POST .../x/vu/web/del/v2?csrf=...` → 404
- `POST .../x/vu/web/recall?csrf=...` → 404
- `POST .../x2/creative/web/archive/del?csrf=...` form {bvid}/{aid} → 404
- `POST .../x2/creative/web/archive/delete?csrf=...` → 404

> 结论：删除接口在新版创作中心已迁移，上面这些老路径都死了。

### 给下一个 agent 的排查路线（按优先级）
1. **看现网前端 JS（最可靠）**：用「浏览器自动化」技能，把 `cookies.json` 注入登录 `https://member.bilibili.com`，进「创作中心 → 稿件管理」，对 `BV13PtB6AEyg` 点「删除」，在 DevTools Network 里抓真正的 XHR URL + 请求体。B 站删除按钮实际调用的接口就在那里。
2. **查 `bilibili-api` 官方 Python 库**：`nemo2011/bilibili-api`（仓库已改名 `bilibili-api-python`，默认分支 `main`）。其 `Video.delete()` / `async def delete()` 里用的就是当前正确端点。直接读它的 `bilibili_api/video.py` 或 `data/api/video.json`。
3. **查 `biliup-rs` Rust 源码** (`biliup/biliup` `crates/biliup/src/uploader/bilibili.rs`)：若上游已实现删稿，照搬端点与参数。
4. **开放平台兜底**（不推荐，需 app 凭证）：`https://open.bilibili.com` 的「视频稿件删除」接口（需 access_token，不匹配当前 cookie 登录态）。

### 删除成功后要做
- 再次用 `view` 接口确认 `BV13PtB6AEyg` 返回「稿件不存在/-404」。
- 用 `x/space/arc/search` 列表确认已从 60 个变成 59 个。

---

## 4. 根因 & 修复：发布管线丢集数（必须改 `publisher.py`）

### 根因
`publish_latest()`（145–149 行）写成：
```python
title = agent_state.get("title", "未命名")          # = "《看见未来之前》"（纯片名，无集数）
return self.upload(final_movie, episode=ep, title=title, ...)
```
而 `upload()`（78–81 行）是：
```python
title_text = title or self._fill(self.cfg.get("title_template", "{title} · 第{n}集"), title="未命名", n=ep)
```
因为 `title` 已非空，`or` 右侧的 `title_template` **永远不会生效** → 第二集被投成「《看见未来之前》」（即 `BV13PtB6AEyg`，漏写「第二集」）。这就是为什么线上出现了无集数的重复片。

### 修复建议（实现「以后标题要准确才发」）
1. **永远带集数**：`publish_latest` 不要再直接传纯片名。改成拼上集数，例如
   `title = f"{film_title} · 第{ep}集"`（或中文数字「第一集/第二集」，与用户偏好一致）。
2. **发布前校验门禁**（用户原话「以后标题要准确才发」）：在 `upload()` 真正执行 `subprocess` 投稿**之前**，加 `validate_title(title_text, episode, film_title)`：
   - 必须包含片名（如 `《看见未来之前》`）；
   - 必须包含集数标记（`第\d+集` 或 中文数字集数）；
   - 长度 / 违禁词基础检查；
   - 不达标 → 返回 `{"ok":False,"error":"标题不准确，已阻断投稿"}`，**不调用 biliup**。
3. **删除接口落地**：把上面第 3 节找到的真实删除端点实现到 `delete_video(bvid)`，替换空壳；并让 `upload_and_replace` 的「下架旧片」真正生效。

### 相关配置
- `config.yaml` 的 `publish:` 段：`binary`（biliup-rs 路径）、`title_template`、`desc_template`、`tags`、`tid`、`proxy`、`dynamic`、`cover`、`dtime`。
- 默认 `title_template = "{title} · 第{n}集"`（用阿拉伯数字 n）；用户偏好中文数字（第一集/第二集），可改模板或在校验里统一。

---

## 5. 关键文件 / 凭证速查
- `agent/publisher.py` — 发布器（upload / publish_latest / delete_video / upload_and_replace / publish_concept）
- `cli.py` — 命令行入口（含 `feishu poll` 等）
- `config.yaml` 的 `publish:` 段 — 投稿参数
- `outputs/cookies.json` — B 站登录态（含 bili_jct CSRF）
- `tools/biliup/biliupR-v0.2.4-x86_64-windows/biliup.exe` — biliup-rs 二进制（`show`/`upload` 可用，**无 delete**）

## 6. 复现 / 验证命令（Windows，Python 311）
```powershell
$env:HTTP_PROXY="http://127.0.0.1:7897"; $env:HTTPS_PROXY="http://127.0.0.1:7897"; $env:ALL_PROXY="http://127.0.0.1:7897"
# 列出全部投稿标题（带 WBI，需先写好脚本）
python _list_subs.py
# 编辑标题（已验证可用，参考本文件第 2 节）
python _fix_xxx.py
# 删除（端点待定，见第 3 节）
```
> 注：shell 是 GBK，**中文不要走 `-c` inline**，一律写 `.py` 文件用 `utf-8` 读写。
