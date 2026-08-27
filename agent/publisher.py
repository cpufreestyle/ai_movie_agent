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

import os
import shutil
import subprocess


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
    def upload(self, video_path: str, episode: int | None = None,
               title: str | None = None, logline: str = "", desc: str | None = None,
               tags=None, dynamic: str | None = None, source: str | None = None,
               cover: str | None = None, submit: bool = False) -> dict:
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return {"ok": False, "error": f"视频不存在或为空: {video_path}"}
        if not self.is_ready():
            return {"ok": False, "error": f"未找到 biliup 可执行文件 '{self.binary}'。"
                                          "请先安装 biliup-rs 并在 config 设置 publish.binary。"}

        ep = episode if episode is not None else 1
        title_text = title or self._fill(
            self.cfg.get("title_template", "{title} · 第{n}集"),
            title="未命名", n=ep,
        )
        desc_text = desc or self._fill(
            self.cfg.get("desc_template", "由本地 AI 电影 Agent 自动生成。"),
            title=title or "", n=ep, logline=logline,
        )
        if tags is None:
            tags = self.cfg.get("tags", "AI影视,人工智能,AIGC")
        if isinstance(tags, (list, tuple)):
            tags = ",".join(str(t) for t in tags)
        tid = int(self.cfg.get("tid", 171))
        source = self.cfg.get("source", "") if source is None else source
        dynamic = self.cfg.get("dynamic", "") if dynamic is None else dynamic
        cover = self.cfg.get("cover", "") if cover is None else cover
        dtime = int(self.cfg.get("dtime", 0))

        cmd = [self._resolve_binary()]
        if self.account:
            cmd += ["-u", self.account]
        cmd += ["upload", video_path,
                "--title", title_text,
                "--desc", desc_text,
                "--tag", tags,
                "--tid", str(tid)]
        if source:
            cmd += ["--source", source]
        if dynamic:
            # B 站 dynamic 限 233 字，超长截断（保留末尾话题标签更友好）
            if len(dynamic) > 233:
                tail = dynamic[-120:]
                dynamic = dynamic[:233 - len(tail) - 1].rstrip() + "…" + tail
            cmd += ["--dynamic", dynamic]
        if cover and os.path.exists(cover):
            cmd += ["--cover", cover]
        if dtime:
            cmd += ["--dtime", str(dtime)]
        # biliup-rs 的 --submit 需取值(client/app/web)，显式指定即真正投稿
        if submit:
            cmd += ["--submit", "client"]

        print(f"  [publish] 投稿到 B 站: {title_text}")
        try:
            # 注意：biliup 输出为 UTF-8，Windows 默认 GBK 解码会崩，故捕获字节后手动解码
            proc = subprocess.run(cmd, cwd=self.workdir, capture_output=True)
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
        ep = agent_state.get("scene_count", 1)
        title = agent_state.get("title", "未命名")
        logline = agent_state.get("bible", {}).get("logline", "")
        return self.upload(final_movie, episode=ep, title=title, logline=logline)

    # ---------- 下架 / 删除已投稿 ----------
    def delete_video(self, bvid: str) -> dict:
        """biliup-rs 不支持删除已投稿视频。下架请前往 B 站网页端创作中心手动操作。"""
        return {"ok": False, "error":
                "biliup-rs 无删除子命令，请到 B 站创作中心 "
                "(https://member.bilibili.com) 手动下架/删除该视频。"}

    def upload_and_replace(self, video_path: str, old_bvid: str | None = None,
                           title: str | None = None, logline: str = "", desc: str | None = None,
                           tags=None, dynamic: str | None = None, source: str | None = None,
                           cover: str | None = None, submit: bool = True) -> dict:
        """上传新视频并替换旧视频（下架旧片需手动在 B 站创作中心操作）。"""
        if old_bvid:
            d = self.delete_video(old_bvid)
            print(f"  [warn] 旧视频需手动下架: {d.get('error')}")
        return self.upload(video_path, episode=1, title=title, logline=logline,
                           desc=desc, tags=tags, dynamic=dynamic, source=source,
                           cover=cover, submit=submit)
    # ---------- 便捷：把创意/规划渲染成视频 demo 并投稿 ----------
    def publish_concept(self, out_path: str | None = None,
                        title: str | None = None, desc: str | None = None,
                        tags: list[str] | None = None, submit: bool = False,
                        source: str = "local", xfade: float = 0.4,
                        bgm=None) -> str:
        """把 C 阶段创意/规划渲染成视频 demo，并在 biliup 就绪时投稿到 B 站。

        无 ffmpeg/imageio 时也能产出 PNG 序列作为投稿素材；真正投稿需先安装
        biliup 并 `biliup login`。
        """
        from agent.concept_video import render_concept_video

        state = self._load_state()
        concept = (state or {}).get("bible") or {}
        if not concept:
            concept = state or {}
        # 关键帧图（D 阶段产物，可选）
        kf_dir = os.path.join(self.workdir, "keyframes")
        keyframes = (sorted(
            os.path.join(kf_dir, f) for f in os.listdir(kf_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ) if os.path.isdir(kf_dir) else [])
        if not out_path:
            os.makedirs(os.path.join(self.workdir, "scenes"), exist_ok=True)
            out_path = os.path.join(self.workdir, "scenes", "concept_demo.mp4")

        # 白模分镜预视图（Blender 生成，可选）：优先用于分镜卡与视觉参考
        blocking_previs = (state or {}).get("blocking_previs") or None
        video = render_concept_video(concept, keyframes, out_path,
                                     xfade=xfade, bgm=bgm,
                                     blocking_images=blocking_previs)
        print(f"  [publish] 创意/规划视频已生成: {video}")
        if not self.is_ready():
            print("  [publish] 未检测到 biliup，跳过投稿。安装并 login 后重跑即可投稿。")
            return video

        # 竖版封面（B 站投稿用）
        cover_path = os.path.join(self.workdir, "scenes", "concept_cover.png")
        try:
            from agent.concept_video import render_cover
            render_cover(concept, keyframes, cover_path)
        except Exception as e:
            print(f"  [publish] 封面生成失败（忽略）: {e}")
            cover_path = ""

        if not title:
            title = (state or {}).get("title") or concept.get("logline") or "AI 电影创意"
        if not desc:
            desc = (
                f"片名《{title}》——一部由本地 AI 电影 Agent 自动企划、生成的概念短片 demo。\n\n"
                "这个 Agent 能做什么：\n"
                "· 持续创作 / 无限时长：基于 SkyReels-V2 的 Diffusion Forcing 续写，影片可一直生长；\n"
                "· 全链路自动化：素材采集 → 知识沉淀 → 概念企划 → 关键帧 → 剧本 → 去 AI 味润色 → 视频导演 → 自动投稿 B 站；\n"
                "· 本地 LLM 自动写剧本（无模型也能跑通模板兜底）。\n\n"
                "用 AI 提前看见未来——让 Agent 把一句话创意变成可投稿的影像。\n"
                "#魔搭社区 #Qoder #AI无限开发者创作大赛 #用AI提前看见未来 #AIGC"
            )
        if not tags:
            tags = ["AI电影Agent", "开源项目", "AIGC", "AI影视", "短片",
                    "打赏", concept.get("theme") or "AI电影", "创意策划"]
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
