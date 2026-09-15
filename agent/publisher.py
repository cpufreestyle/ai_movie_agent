"""发布器：把生成的影片自动投稿到 B 站。

封装开源工具 [biliup-rs](https://github.com/biliup/biliup-rs)（Rust 实现的 B 站
命令行投稿器）。首次使用前需手动 `biliup login` 一次（扫码/密码，信息存 cookies.json）。

CLI 用法（已核对）：
    biliup [-u cookies.json] upload <video> \
        --title "标题" --desc "简介" --tag "a,b,c" \
        --tid 171 --source "" --cover "x.jpg" --dynamic "动态" --dtime 0

注意：标签参数是单数 `--tag`，多个用逗号分隔；`--dtime` 为 10 位时间戳且需晚于
提交时间 4 小时以上，0 表示立即发布。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import urllib.parse

from .llmutil import log

#: 中文数字 -> 阿拉伯数字（用于从旧标题「第X集」反推集数）
_CN_EPISODE_MAP = dict(zip("一二三四五六七八九", range(1, 10)))


def _truncate_dynamic(dynamic: str) -> str:
    """B 站 dynamic 限 233 字，超长截断（保留末尾话题标签更友好）。"""
    if len(dynamic) <= 233:
        return dynamic
    tail = dynamic[-120:]
    return dynamic[:233 - len(tail) - 1].rstrip() + "…" + tail


def _infer_episode(old_title: str) -> int | None:
    """从旧标题里的「第X集」反推集数（支持阿拉伯与中文数字），失败返回 None。"""
    m = re.search(r"第([0-9一二三四五六七八九十百]+)集", old_title)
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token)
    if token in _CN_EPISODE_MAP:
        return _CN_EPISODE_MAP[token]
    if token.startswith("十"):
        rest = token[1:]
        return 10 + (_CN_EPISODE_MAP.get(rest, 0) if rest else 0)
    if "十" in token:
        tens, _, unit = token.partition("十")
        return _CN_EPISODE_MAP.get(tens, 0) * 10 + (_CN_EPISODE_MAP.get(unit, 0) if unit else 0)
    return None


class Publisher:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("publish", {}) or {}
        self.workdir = workdir
        self.enabled = bool(self.cfg.get("enabled", False))
        self.binary = self.cfg.get("binary") or "biliup"
        self.account = self.cfg.get("account") or ""  # 多账号时的 cookie 文件名(-u)

    def _resolve_binary(self) -> str:
        """Return the binary path, resolving relative paths against the project root."""
        if os.path.isabs(self.binary):
            return self.binary
        # Resolve relative to the agent package's parent (project root)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rel = os.path.join(project_root, self.binary)
        return rel if os.path.exists(rel) else self.binary

    def _abs_project(self, p: str) -> str:
        """把相对路径解析为项目根下的绝对路径，避免 biliup 在 cwd=outputs/ 下找不到文件。"""
        if os.path.isabs(p):
            return p
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.abspath(os.path.join(project_root, p))

    # ---------- 工具 ----------
    def is_ready(self) -> bool:
        return shutil.which(self._resolve_binary()) is not None

    @staticmethod
    def login_guide() -> str:
        return (
            "未检测到 biliup 登录态。请先执行一次交互式登录：\n"
            "    biliup login          # 按提示扫码/输密码，信息写入 cookies.json\n"
            "登录后 Cookie 约 1~3 个月有效，过期再 login 一次即可。"
        )

    def _fill(self, template: str, **kw) -> str:
        """安全占位符替换（避免 str.format 因模板里的其它花括号报错）。"""
        s = template
        for k, v in kw.items():
            s = s.replace("{" + k + "}", str(v))
        return s

    # ---------- 核心：上传 ----------
    def _check_upload_inputs(self, video_path: str) -> dict | None:
        """投稿前的基础校验：视频存在/非空 + biliup 就绪。通过返回 None。"""
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return {"ok": False, "error": f"视频不存在或为空: {video_path}"}
        if not self.is_ready():
            return {"ok": False, "error": f"未找到 biliup 可执行文件 '{self.binary}'。"
                                          "请先安装 biliup-rs 并在 config 设置 publish.binary。"}
        return None

    def _resolve_title(self, title: str | None, ep: int) -> str:
        """标题：显式值优先，否则按 title_template 生成。"""
        return title or self._fill(
            self.cfg.get("title_template", "{title} · 第{n}集"), title="未命名", n=ep)

    def _resolve_desc(self, desc: str | None, title: str | None, ep: int,
                      logline: str) -> str:
        """简介：显式值优先，否则按 desc_template 生成。"""
        return desc or self._fill(
            self.cfg.get("desc_template", "由本地 AI 电影 Agent 自动生成。"),
            title=title or "", n=ep, logline=logline)

    def _resolve_tags(self, tags) -> str:
        """标签：None 取 config 默认，list/tuple 收敛为逗号分隔字符串。"""
        if tags is None:
            tags = self.cfg.get("tags", "AI影视,人工智能,AIGC")
        if isinstance(tags, (list, tuple)):
            tags = ",".join(str(t) for t in tags)
        return tags

    def _cfg_or(self, key: str, value):
        """显式值优先，否则取 config.publish 里的默认值。"""
        return self.cfg.get(key, "") if value is None else value

    def _publish_env(self) -> dict:
        """biliup 的运行环境变量（显式塞代理）。

        biliup-rs 的 Rust reqwest 在 Windows 上不会自动读 WinHTTP 系统代理（7897），
        直连会被网络拦截、表现为上传前 oauth2/info 调用 TLS handshake eof。
        因此显式塞 HTTP(S)_PROXY：优先 config.publish.proxy，其次环境变量，
        最后回退 127.0.0.1:7897。
        """
        env = os.environ.copy()
        proxy = (self.cfg.get("proxy") or os.environ.get("HTTPS_PROXY")
                 or os.environ.get("HTTP_PROXY") or "http://127.0.0.1:7897")
        env["HTTP_PROXY"] = env["HTTPS_PROXY"] = env["ALL_PROXY"] = proxy
        return env

    def _build_upload_cmd(self, video_path: str, title_text: str, desc_text: str,
                          tags: str, source: str = "", dynamic: str = "",
                          cover: str = "", dtime: int = 0,
                          submit: bool = False) -> list:
        """组装 biliup upload 命令行。"""
        cmd = [self._resolve_binary()]
        if self.account:
            cmd += ["-u", self.account]
        cmd += ["upload", video_path,
                "--title", title_text,
                "--desc", desc_text,
                "--tag", tags,
                "--tid", str(int(self.cfg.get("tid", 171)))]
        if source:
            cmd += ["--source", source]
        if dynamic:
            cmd += ["--dynamic", _truncate_dynamic(dynamic)]
        if cover and os.path.exists(cover):
            cmd += ["--cover", cover]
        if dtime:
            cmd += ["--dtime", str(dtime)]
        # biliup-rs 的 --submit 需取值(client/app/web)，显式指定即真正投稿
        if submit:
            cmd += ["--submit", "client"]
        return cmd

    def upload(self, video_path: str, episode: int | None = None,
               title: str | None = None, logline: str = "", desc: str | None = None,
               tags=None, dynamic: str | None = None, source: str | None = None,
               cover: str | None = None, submit: bool = False,
               film_title: str | None = None) -> dict:
        bad = self._check_upload_inputs(video_path)
        if bad:
            return bad

        ep = episode if episode is not None else 1
        title_text = self._resolve_title(title, ep)
        # 标题准确性门禁（2026-09-04 规则：标题要准确才发）
        gate_err = self.validate_title(title_text, episode=episode, film_title=film_title)
        if gate_err:
            return {"ok": False,
                    "error": f"标题校验未通过，已阻断投稿: {gate_err}（标题: {title_text}）"}
        desc_text = self._resolve_desc(desc, title, ep, logline)
        tags = self._resolve_tags(tags)

        cmd = self._build_upload_cmd(
            video_path, title_text, desc_text, tags,
            source=self._cfg_or("source", source),
            dynamic=self._cfg_or("dynamic", dynamic),
            cover=self._cfg_or("cover", cover),
            dtime=int(self.cfg.get("dtime", 0)), submit=submit)

        log(f"  [publish] 投稿到 B 站: {title_text}")
        try:
            # 注意：biliup 输出为 UTF-8，Windows 默认 GBK 解码会崩，故捕获字节后手动解码
            proc = subprocess.run(cmd, cwd=self.workdir, capture_output=True,
                                  env=self._publish_env())
        except Exception as e:
            return {"ok": False, "error": str(e)}

        out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            msg = (err or out or "").strip() or f"exit={proc.returncode}"
            return {"ok": False, "error": msg}
        return {"ok": True, "title": title_text, "raw": out}

    # ---------- 便捷：发布最新成片 ----------
    def publish_latest(self, agent_state: dict, final_movie: str) -> dict:
        ep = agent_state.get("scene_count", 1) or 1
        film_title = agent_state.get("title", "未命名")
        logline = agent_state.get("bible", {}).get("logline", "")
        # 必须带集数：2026-09-04 前这里直传纯片名，压制了 title_template，
        # 导致出现无集数的重复投稿（BV13PtB6AEyg 事故）。模板 + 中文数字集数。
        template = self.cfg.get("title_template", "{title} · 第{n}集")
        title = self._fill(template, title=film_title, n=ep)
        title = title.replace(f"第{ep}集", f"第{self._cn_episode(ep)}集")
        return self.upload(final_movie, episode=ep, title=title,
                           film_title=film_title, logline=logline)

    # ---------- 标题校验 ----------
    _CN_DIGITS = "零一二三四五六七八九"

    @classmethod
    def _cn_episode(cls, n: int) -> str:
        """1 -> 一, 12 -> 十二, 30 -> 三十；>=100 回退阿拉伯数字。"""
        if n < 0:
            return str(n)
        if n < 10:
            return cls._CN_DIGITS[n]
        if n < 20:
            return "十" + (cls._CN_DIGITS[n % 10] if n % 10 else "")
        if n < 100:
            tens, unit = divmod(n, 10)
            return cls._CN_DIGITS[tens] + "十" + (cls._CN_DIGITS[unit] if unit else "")
        return str(n)

    @staticmethod
    def validate_title(title: str, episode: int | None = None,
                       film_title: str | None = None) -> str | None:
        """标题准确性门禁：返回 None 表示通过，否则返回不通过原因。"""
        import re
        if not title or not title.strip():
            return "标题为空"
        if len(title) > 80:
            return f"标题超长（{len(title)} > 80 字）"
        if film_title and film_title not in title:
            return f"标题缺少片名「{film_title}」"
        if episode is not None and not re.search(r"第[0-9一二三四五六七八九十百]+集", title):
            return "标题缺少集数标记（第N集）"
        return None

    # ---------- 下架 / 删除已投稿 ----------
    def _load_bili_credentials(self) -> dict | None:
        """从 outputs/cookies.json 读取 B 站登录态，返回 {csrf, cookie_header} 或 None。"""
        import json as _json
        candidates = [os.path.join(self.workdir, "cookies.json"),
                      os.path.join(self._abs_project("outputs"), "cookies.json")]
        for path in candidates:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = _json.load(f)
            except Exception:
                continue
            cookies = (data.get("cookie_info") or {}).get("cookies") or data.get("cookies") or []
            csrf = next((c.get("value") for c in cookies
                         if c.get("name") == "bili_jct"), None)
            if csrf:
                header = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies)
                return {"csrf": csrf, "cookie_header": header}
        return None

    def delete_video(self, bvid: str) -> dict:
        """删除已投稿视频：不支持自动删除（2026-09-05 用户决定放弃）。

        B 站对该操作强制人机验证：真实端点为
        POST https://member.bilibili.com/x/web/archive/delete（form: bvid+csrf），
        但无验证码令牌时返回 code=340022（验证码错误），滑块/短信无法脚本化。
        下架请到创作中心 (https://member.bilibili.com) 手动操作。
        """
        return {"ok": False,
                "error": "不支持自动删除：B 站风控要求人机验证（code=340022），"
                         "请到创作中心 (https://member.bilibili.com) 手动下架该稿件。"}

    # ---------- 稿件管理：列表 / 详情 / 编辑（WebUI 稿件管理用） ----------
    _WBI_MIXIN_TAB = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
        33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
        26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
        20, 34, 44, 52]

    def _bili_request_ctx(self) -> dict | None:
        """cookies/proxy 上下文：{headers, csrf, mid, proxies}；无登录态返回 None。"""
        cred = self._load_bili_credentials()
        if not cred:
            return None
        mid = None
        for pair in cred["cookie_header"].split("; "):
            if pair.startswith("DedeUserID="):
                mid = pair.split("=", 1)[1]
        proxy = self.cfg.get("proxy") or os.environ.get("HTTPS_PROXY") \
            or os.environ.get("HTTP_PROXY") or "http://127.0.0.1:7897"
        return {
            "headers": {
                "Cookie": cred["cookie_header"],
                "Referer": "https://member.bilibili.com/",
                "Origin": "https://member.bilibili.com",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
            },
            "csrf": cred["csrf"],
            "mid": mid,
            "proxies": {"http": proxy, "https": proxy} if proxy else None,
        }

    @staticmethod
    def _wbi_sign(params: dict, img_key: str, sub_key: str) -> dict:
        """B 站 WBI 参数签名（算法与 mixin 表来自 bilibili-API-collect）。"""
        mixin = "".join((img_key + sub_key)[i] for i in Publisher._WBI_MIXIN_TAB)[:32]
        signed = dict(params)
        signed["wts"] = int(time.time())
        query = urllib.parse.urlencode(sorted(signed.items()))
        signed["w_rid"] = hashlib.md5((query + mixin).encode()).hexdigest()
        return signed

    def list_my_videos(self, page_size: int = 30, max_pages: int = 5) -> dict:
        """拉取本账号全部投稿（WBI 签名，分页），返回 [{bvid, title, created, length}]。"""
        import requests
        ctx = self._bili_request_ctx()
        if not ctx:
            return {"ok": False, "error": "未找到 B 站登录态（outputs/cookies.json）"}
        if not ctx.get("mid"):
            return {"ok": False, "error": "cookies 中缺少 DedeUserID，无法定位账号"}
        try:
            nav = requests.get("https://api.bilibili.com/x/web-interface/nav",
                               headers=ctx["headers"], proxies=ctx["proxies"],
                               timeout=15).json()
            wbi = (nav.get("data") or {}).get("wbi_img") or {}
            img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
            sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
        except Exception as e:
            return {"ok": False, "error": f"获取 WBI key 失败: {e}"}

        items, pn = [], 1
        try:
            while pn <= max_pages:
                params = self._wbi_sign(
                    {"mid": ctx["mid"], "ps": page_size, "pn": pn,
                     "order": "pubdate"}, img_key, sub_key)
                r = requests.get("https://api.bilibili.com/x/space/arc/search",
                                 params=params, headers=ctx["headers"],
                                 proxies=ctx["proxies"], timeout=20)
                body = r.json()
                if body.get("code") != 0:
                    return {"ok": False,
                            "error": f"列表接口 code={body.get('code')} "
                                     f"message={body.get('message', '')}"}
                vlist = ((body.get("data") or {}).get("list") or {}).get("vlist") or []
                for v in vlist:
                    items.append({
                        "bvid": v.get("bvid", ""),
                        "title": v.get("title", ""),
                        "created": v.get("created", ""),
                        "length": v.get("length", ""),
                        "play": v.get("play", 0),
                    })
                if not vlist:
                    break
                pn += 1
        except Exception as e:
            return {"ok": False, "error": f"拉取列表失败: {e}"}
        return {"ok": True, "videos": items}

    def get_video_detail(self, bvid: str) -> dict:
        """拉单视频完整信息（无需 WBI）：title/desc/tag/pic/aid/cid 等。"""
        import requests
        ctx = self._bili_request_ctx()
        try:
            r = requests.get(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                             headers=ctx["headers"] if ctx else None,
                             proxies=ctx["proxies"] if ctx else None, timeout=20)
            return r.json()
        except Exception as e:
            return {"code": -1, "message": f"请求失败: {e}"}

    def _bili_server_filename(self, bvid: str) -> str | None:
        """biliup show {bvid} 拿服务端文件名（edit 接口 videos[] 必填）。"""
        try:
            proc = subprocess.run(
                [self._resolve_binary(), "show", bvid],
                cwd=self.workdir, capture_output=True, timeout=60,
                env={**os.environ,
                     "HTTP_PROXY": self.cfg.get("proxy") or "http://127.0.0.1:7897",
                     "HTTPS_PROXY": self.cfg.get("proxy") or "http://127.0.0.1:7897"},
            )
        except Exception as e:
            log(f"  [publish] biliup show 失败: {e}")
            return None
        text = (proc.stdout or b"").decode("utf-8", "replace") + \
               (proc.stderr or b"").decode("utf-8", "replace")
        m = re.search(r"\b([a-z0-9]{20,40})\b", text)
        return m.group(1) if m else None

    def _fetch_edit_context(self, bvid: str) -> tuple:
        """拉取稿件详情 + cid + 服务端文件名；失败返回 (err_dict, None)。"""
        detail = self.get_video_detail(bvid)
        if detail.get("code") != 0:
            return {"ok": False,
                    "error": f"拉取稿件信息失败: {detail.get('message', '')}"}, None
        d = detail.get("data") or {}
        pages = d.get("pages") or []
        cid = pages[0].get("cid") if pages else None
        server_fn = self._bili_server_filename(bvid)
        if not cid or not server_fn:
            return {"ok": False,
                    "error": "缺少 cid 或服务端文件名（biliup show），无法编辑"}, None
        return None, (d, cid, server_fn)

    def _post_edit(self, ctx: dict, form: dict) -> dict:
        """提交创作中心 edit 请求，返回 {ok, body} 或 {ok:False, error}。"""
        import requests
        try:
            r = requests.post(
                f"https://member.bilibili.com/x/vu/web/edit?csrf={ctx['csrf']}",
                json=form, headers=ctx["headers"], proxies=ctx["proxies"], timeout=30)
        except Exception as e:
            return {"ok": False, "error": f"编辑请求失败: {e}"}
        try:
            return {"ok": True, "body": r.json()}
        except Exception:
            return {"ok": False,
                    "error": f"编辑接口返回非 JSON（HTTP {r.status_code}）: {r.text[:200]}"}

    def update_video(self, bvid: str, title: str | None = None,
                     desc: str | None = None, tag: str | None = None) -> dict:
        """编辑已投稿的标题/简介/标签（创作中心 edit 接口，2026-09-04 验证可用）。

        标题走 validate_title 门禁（episode 由现有标题推断）。
        """
        ctx = self._bili_request_ctx()
        if not ctx:
            return {"ok": False, "error": "未找到 B 站登录态（outputs/cookies.json）"}
        err, fetched = self._fetch_edit_context(bvid)
        if err:
            return err
        d, cid, server_fn = fetched
        old_title = d.get("title", "")

        new_title = title if title is not None else old_title
        # 从旧标题推断集数，交给门禁校验（改标题也不允许把集数改丢）
        gate_err = self.validate_title(new_title, episode=_infer_episode(old_title),
                                       film_title=self._series_title(old_title))
        if gate_err:
            return {"ok": False,
                    "error": f"标题校验未通过，已阻断修改: {gate_err}（标题: {new_title}）"}

        form = {
            "aid": d.get("aid"), "bvid": bvid, "title": new_title,
            "copyright": d.get("copyright", 1), "tid": d.get("tid", 171),
            "source": d.get("source", "") or "", "desc": d.get("desc", "") or "",
            "cover": d.get("pic", "") or "",
            "tag": tag if tag is not None else (d.get("tag") or ""),
            "dynamic": d.get("dynamic", "") or "AI 自动生成的实验短片",
            "dtime": 0, "mission_id": 0,
            "videos": [{"filename": server_fn, "title": "movie_final", "cid": cid}],
        }
        res = self._post_edit(ctx, form)
        if not res.get("ok"):
            return res
        body = res["body"]
        if body.get("code") == 0:
            log(f"  [publish] 已更新稿件标题: {new_title}")
            return {"ok": True, "bvid": bvid, "title": new_title, "raw": body}
        return {"ok": False,
                "error": f"编辑失败 code={body.get('code')} "
                         f"message={body.get('message', '')}"}

    @staticmethod
    def _series_title(old_title: str) -> str | None:
        """从「《看见未来之前》 · 第二集」这类标题里提取纯片名部分。"""
        m = re.match(r"^(《[^》]+》)", old_title.strip())
        if m:
            return m.group(1)
        if " · " in old_title:
            return old_title.split(" · ")[0].strip() or None
        return None

    def check_delete_risk(self, bvid: str) -> dict:
        """删除前风控预检：data=true 表示需要人机验证（滑块/短信）。"""
        import requests
        ctx = self._bili_request_ctx()
        if not ctx:
            return {"ok": False, "error": "未找到 B 站登录态"}
        try:
            r = requests.get("https://member.bilibili.com/x/risk/archive/del",
                             params={"platform": "web", "bvid": bvid},
                             headers=ctx["headers"], proxies=ctx["proxies"],
                             timeout=20)
            body = r.json()
            return {"ok": body.get("code") == 0, "need_captcha": body.get("data") is True,
                    "raw": body}
        except Exception as e:
            return {"ok": False, "error": f"风控预检失败: {e}"}

    def upload_and_replace(self, video_path: str, old_bvid: str | None = None,
                           title: str | None = None, logline: str = "", desc: str | None = None,
                           tags=None, dynamic: str | None = None, source: str | None = None,
                           cover: str | None = None, submit: bool = True) -> dict:
        """上传新视频替换旧内容（旧稿下架需手动在 B 站创作中心操作）。"""
        if old_bvid:
            log(f"  [warn] 旧视频 {old_bvid} 需手动下架：请到 B 站创作中心操作"
                "（自动删除已放弃：风控需人机验证）。")
        return self.upload(video_path, episode=1, title=title, logline=logline,
                           desc=desc, tags=tags, dynamic=dynamic, source=source,
                           cover=cover, submit=submit)
    # ---------- 便捷：把创意/规划渲染成视频 demo 并投稿 ----------
    def _concept_bible(self, state) -> dict:
        """取概念企划：优先 state.bible，退化为整个 state。"""
        concept = (state or {}).get("bible") or {}
        return concept or (state or {})

    def _load_keyframes(self) -> list:
        """D 阶段关键帧列表（目录不存在时返回 []）。"""
        kf_dir = os.path.join(self.workdir, "keyframes")
        if not os.path.isdir(kf_dir):
            return []
        return sorted(os.path.join(kf_dir, f) for f in os.listdir(kf_dir)
                      if f.lower().endswith((".png", ".jpg", ".jpeg")))

    def _select_bgm(self, bgm, auto_bgm: bool):
        """自动配乐（#7）：未显式给 bgm 时自动选曲（ffmpeg/BGM 缺失则跳过）。"""
        if not (auto_bgm and bgm is None):
            return bgm
        try:
            from agent import audio_mix as _am
            if _am.is_ready():
                sel = _am.select_bgm(self.workdir)
                if sel:
                    log(f"  [publish] 自动配乐：{os.path.basename(sel)}")
                    return sel
        except Exception as e:
            log(f"  [publish] 自动配乐跳过: {e}")
        return bgm

    def _render_concept_cover(self, concept: dict, keyframes: list,
                              cover_path: str) -> str:
        """生成竖版封面（B 站投稿用）；失败返回 ""。"""
        try:
            from agent.concept_video import render_cover
            render_cover(concept, keyframes, cover_path)
            return cover_path
        except Exception as e:
            log(f"  [publish] 封面生成失败（忽略）: {e}")
            return ""

    @staticmethod
    def _concept_title(title: str | None, state, concept: dict) -> str:
        """概念视频标题：显式 > state.title > 概念 logline > 兜底。"""
        return (title or (state or {}).get("title") or concept.get("logline")
                or "AI 电影创意")

    @staticmethod
    def _concept_default_desc(title: str) -> str:
        """概念视频的默认简介（含标签话题）。"""
        return (
            f"片名《{title}》——一部由本地 AI 电影 Agent 自动企划、生成的概念短片 demo。\n\n"
            "这个 Agent 能做什么：\n"
            "· 持续创作 / 无限时长：基于 SkyReels-V2 的 Diffusion Forcing 续写，影片可一直生长；\n"
            "· 全链路自动化：素材采集 → 知识沉淀 → 概念企划 → 关键帧 → 剧本 → 去 AI 味润色 → 视频导演 → 自动投稿 B 站；\n"
            "· 本地 LLM 自动写剧本（无模型也能跑通模板兜底）。\n\n"
            "用 AI 提前看见未来——让 Agent 把一句话创意变成可投稿的影像。\n"
            "#魔搭社区 #Qoder #AI无限开发者创作大赛 #用AI提前看见未来 #AIGC"
        )

    @staticmethod
    def _concept_default_tags(concept: dict) -> list:
        """概念视频的默认标签。"""
        return ["AI电影Agent", "开源项目", "AIGC", "AI影视", "短片",
                "打赏", concept.get("theme") or "AI电影", "创意策划"]

    def publish_concept(self, out_path: str | None = None,
                        title: str | None = None, desc: str | None = None,
                        tags: list[str] | None = None, submit: bool = False,
                        source: str = "local", xfade: float = 0.4,
                        auto_bgm: bool = True, bgm=None) -> str:
        """把 C 阶段创意/规划渲染成视频 demo，并在 biliup 就绪时投稿到 B 站。

        无 ffmpeg/imageio 时也能产出 PNG 序列作为投稿素材；真正投稿需先安装
        biliup 并 `biliup login`。
        """
        from agent.concept_video import render_concept_video

        state = self._load_state()
        concept = self._concept_bible(state)
        keyframes = self._load_keyframes()
        if not out_path:
            os.makedirs(os.path.join(self.workdir, "scenes"), exist_ok=True)
            out_path = os.path.join(self.workdir, "scenes", "concept_demo.mp4")

        # 白模分镜预视图（Blender 生成，可选）：优先用于分镜卡与视觉参考
        blocking_previs = (state or {}).get("blocking_previs") or None
        bgm = self._select_bgm(bgm, auto_bgm)
        video = render_concept_video(concept, keyframes, out_path, xfade=xfade, bgm=bgm,
                                     blocking_images=blocking_previs)
        log(f"  [publish] 创意/规划视频已生成: {video}")
        if not self.is_ready():
            log("  [publish] 未检测到 biliup，跳过投稿。安装并 login 后重跑即可投稿。")
            return video

        # 竖版封面（B 站投稿用）
        cover_path = self._render_concept_cover(
            concept, keyframes,
            os.path.join(self.workdir, "scenes", "concept_cover.png"))

        title = self._concept_title(title, state, concept)
        desc = desc or self._concept_default_desc(title)
        tags = tags or self._concept_default_tags(concept)
        return self.upload(video, title=title, desc=desc, tags=tags,
                           source=source, dynamic=desc, cover=cover_path,
                           submit=submit)

    def _load_state(self) -> dict:
        import json as _json
        path = os.path.join(self.workdir, "state.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return _json.load(f)
            except Exception:
                return {}
        return {}
