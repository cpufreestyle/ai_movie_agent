"""H 阶段补充 · 创意/规划视频 demo。

把 C 阶段的概念企划（加上 D 阶段的关键帧图，若有）渲染成一个可被 B 站
投稿的短视频 demo：

- 封面卡：片名 + 一句话梗概(logline)；
- 设定卡：场景 / 主角 / 基调 / 视觉母题；
- 分镜卡：每个 outline 一项，文字 + 对应关键帧图(若有)；
- 结尾卡：世界观参考来源提示。

纯 PIL 实现，不依赖 ffmpeg；用 imageio-ffmpeg（或 imageio）编码为 mp4，
因此可在无 ffmpeg 的环境直接产出视频。若两者都不可用，退化输出带时间轴的
PNG 序列（仍可作为投稿素材）。

投稿由 agent/publisher.py 经 biliup 完成（需另行安装并 login）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

# ---------- 字体 ----------
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",          # 微软雅黑（中文，Win）
        "C:/Windows/Fonts/simhei.ttf",        # 黑体
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ---------- 文字工具 ----------
def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in (text or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            test = cur + ch
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def _wrap_all(draw, items: list[str], font, max_w: int) -> list[str]:
    out: list[str] = []
    for it in items:
        out += _wrap(draw, it, font, max_w)
    return out


def _card(width: int, height: int, title: str, lines: list[str],
          image: str | None = None, bg=(18, 18, 24), fg=(235, 235, 235),
          accent=(120, 200, 255)) -> Image.Image:
    """一张文字卡。正文会自动缩小字号以适配卡片高度，避免文字被截断。"""
    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)
    pad = int(width * 0.06)
    max_w = width - 2 * pad
    top = int(height * 0.08)
    bottom = int(height * 0.05)
    avail = height - top - bottom

    title_size = int(height * 0.05)
    tf = _font(title_size, bold=True)
    t_lines = _wrap_all(d, [title] if title else [], tf, max_w)
    t_h = len(t_lines) * int(tf.size * 1.25) + (int(height * 0.02) if t_lines else 0)

    # 若有配图，文字只占左半，图片放右半
    thumb = None
    if image and os.path.exists(image):
        try:
            thumb = Image.open(image).convert("RGB")
        except Exception:
            thumb = None
    text_max_w = max_w - (int(width * 0.46) if thumb else 0)

    # 自适应缩小正文字号，确保整体不超出卡片高度（不被截断）
    body_size = int(height * 0.032)
    bf = None
    b_lines = []
    for bs in range(body_size, 12, -1):
        bf = _font(bs)
        b_lines = _wrap_all(d, lines, bf, text_max_w)
        b_h = len(b_lines) * int(bf.size * 1.4) + len(lines) * int(bf.size * 0.4)
        if t_h + b_h <= avail:
            break
    else:
        bf = _font(12)
        b_lines = _wrap_all(d, lines, bf, text_max_w)

    # 绘制
    y = top
    for ln in t_lines:
        d.text((pad, y), ln, font=tf, fill=accent)
        y += int(tf.size * 1.25)
    y += int(height * 0.02) if t_lines else 0

    if thumb:
        tgt_h = int(height * 0.6)
        ratio = tgt_h / thumb.height
        tgt_w = int(thumb.width * ratio)
        tgt_w = min(tgt_w, int(width * 0.42))
        tgt_h = int(thumb.height * (tgt_w / thumb.width))
        thumb = thumb.resize((tgt_w, tgt_h))
        img.paste(thumb, (width - pad - tgt_w, int(height * 0.18)))

    for ln in b_lines:
        d.text((pad, y), ln, font=bf, fill=fg)
        y += int(bf.size * 1.4)
    return img


def _ffmpeg_exe():
    """定位 ffmpeg：优先系统 PATH，其次 imageio-ffmpeg 自带的二进制。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _make_ambient_wav(path: str, duration_sec: float, sr: int = 44100) -> bool:
    """生成一段轻柔的环境垫乐（多正弦泛音 + 慢颤音 + 淡入淡出）。

    仅在调用方要求 BGM 且无现成音频文件时使用；失败返回 False。
    """
    try:
        import numpy as np
    except Exception:
        return False
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    # 小调感基频叠泛音（A2=110 + 五度/八度）
    freqs = [110.0, 164.81, 220.0, 329.63]
    mix = np.zeros_like(t)
    for i, f in enumerate(freqs):
        env = 0.6 + 0.4 * np.sin(2 * np.pi * 0.05 * t + i)  # 慢颤音
        mix += (0.25 / (i + 1)) * env * np.sin(2 * np.pi * f * t)
    fade = int(sr * 2)
    if fade < len(mix):
        mix[:fade] *= np.linspace(0, 1, fade)
        mix[-fade:] *= np.linspace(1, 0, fade)
    peak = float(np.max(np.abs(mix))) + 1e-9
    mix = (mix / peak) * 0.22  # 控制整体音量
    pcm = (mix * 32767).astype(np.int16)
    try:
        import wave
        with wave.open(path, "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        return True
    except Exception:
        return False


def _mux_bgm(video: str, audio: str, out: str) -> bool:
    """用 ffmpeg 把音频混入视频（视频流拷贝，音频转 aac）。"""
    exe = _ffmpeg_exe()
    if not exe:
        return False
    try:
        r = subprocess.run([exe, "-y", "-i", video, "-i", audio,
                            "-c:v", "copy", "-c:a", "aac", "-shortest", out],
                           capture_output=True)
        return os.path.exists(out) and os.path.getsize(out) > 0
    except Exception:
        return False


def _encode_frames(seq: list[Image.Image], out_path: str, fps: int) -> str:
    """把帧序列编码为 mp4（OpenCV → imageio → PNG 序列 退化）。返回路径。"""
    # 1) OpenCV
    try:
        import cv2  # type: ignore
        import numpy as np
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        h, w = seq[0].height, seq[0].width
        vw = cv2.VideoWriter(out_path, fourcc, fps, (w, h))
        for f in seq:
            vw.write(cv2.cvtColor(np.array(f), cv2.COLOR_RGB2BGR))
        vw.release()
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
    except Exception as e:
        print(f"  [concept-video] OpenCV 编码失败，尝试其它方式: {e}")
    # 2) imageio
    try:
        import imageio.v2 as imageio  # imageio>=2.9
    except Exception:
        try:
            import imageio
        except Exception:
            imageio = None
    if imageio is not None:
        try:
            writer = imageio.get_writer(out_path, fps=fps, macro_block_size=None,
                                         codec="libx264", quality=7)
            for f in seq:
                writer.append_data(f)
            writer.close()
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return out_path
        except Exception as e:
            print(f"  [concept-video] imageio 编码失败，退化 PNG 序列: {e}")
    # 3) 退化：PNG 序列
    base = os.path.splitext(out_path)[0]
    outdir = base + "_frames"
    os.makedirs(outdir, exist_ok=True)
    for i, f in enumerate(seq):
        f.save(os.path.join(outdir, f"frame_{i:04d}.png"))
    return outdir


def _write_mp4(frames: list[Image.Image], out_path: str, fps: int,
               hold: float = 3.0, xfade: float = 0.4, bgm=None) -> str:
    """编码为 mp4。卡片间交叉淡入转场；可选混入 BGM（需 ffmpeg，否则跳过）。

    不依赖系统 ffmpeg 即可产出视频；仅 BGM 混流需要 ffmpeg（缺失则只做转场）。
    """
    n_hold = max(1, int(fps * hold))
    n_x = max(0, int(fps * xfade))
    seq = [f.convert("RGB") for f in frames]

    # 组装含转场的帧序列
    if n_x > 0 and len(seq) > 1:
        final: list[Image.Image] = [seq[0].copy() for _ in range(n_hold)]
        for card in seq[1:]:
            cf = [card.copy() for _ in range(n_hold)]
            if len(final) >= n_x:
                for k in range(n_x):
                    idx = len(final) - n_x + k
                    alpha = (k + 1) / (n_x + 1)
                    final[idx] = Image.blend(final[idx], cf[k], alpha)
                final.extend(cf[n_x:])
            else:
                final.extend(cf)
    else:
        final = [f.copy() for f in seq for _ in range(n_hold)]

    result = _encode_frames(final, out_path, fps)

    # BGM：仅当成功产出 mp4 且有 ffmpeg 时混入
    if bgm and result == out_path and os.path.exists(out_path):
        try:
            if isinstance(bgm, str) and os.path.exists(bgm):
                audio = bgm
            else:
                audio = out_path + ".wav"
                audio = audio if _make_ambient_wav(audio, len(final) / fps) else None
            if audio:
                if _ffmpeg_exe():
                    tmp = out_path + ".bgm.mp4"
                    if _mux_bgm(out_path, audio, tmp):
                        try:
                            os.remove(out_path)
                        except Exception:
                            pass
                        os.replace(tmp, out_path)
                        print("  [concept-video] 已混入 BGM")
                    else:
                        print("  [concept-video] ffmpeg 不可用，跳过 BGM（仅转场）")
                else:
                    print("  [concept-video] 无 ffmpeg，跳过 BGM（仅转场）")
        except Exception as e:
            print(f"  [concept-video] BGM 处理跳过: {e}")
    return result


def _build_structure(logline: str, outline: list) -> list[str]:
    """派生三幕结构文案：有分镜用分镜，否则基于梗概给出规划骨架。"""
    if outline:
        return [b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)
                for b in outline]
    base = logline or "一句话创意"
    return [
        f"第一幕 · 起：引入世界与主角，抛出核心悬念（{base}）",
        "第二幕 · 承：冲突升级，主角被迫行动，关系与世界观层层展开",
        "第三幕 · 转：高潮对峙，主角做出关键抉择，主题随之浮现",
        "尾声 · 合：新平衡与余韵，回应开篇抛出的命题",
    ]


def _contact_sheet(width: int, height: int, images: list[str],
                   title: str, note: str = "") -> Image.Image:
    """关键帧九宫格（最多 4 张，2x2）。无图时显示说明文字。"""
    img = Image.new("RGB", (width, height), (18, 18, 24))
    d = ImageDraw.Draw(img)
    pad = int(width * 0.06)
    top = int(height * 0.08)
    tf = _font(int(height * 0.05), bold=True)
    y = top
    for ln in _wrap(d, title, tf, width - 2 * pad):
        d.text((pad, y), ln, font=tf, fill=(120, 200, 255))
        y += int(tf.size * 1.25)
    y += int(height * 0.02)

    if images:
        n = min(len(images), 4)
        cols = 2 if n > 1 else 1
        rows = (n + cols - 1) // cols
        gx0, gy0 = pad, y
        gw = width - 2 * pad
        gh = height - gy0 - int(height * 0.06)
        cw = (gw - (cols - 1) * int(width * 0.03)) // cols
        ch = (gh - (rows - 1) * int(height * 0.03)) // rows
        for i, im in enumerate(images[:4]):
            try:
                thumb = Image.open(im).convert("RGB").resize((cw, ch))
            except Exception:
                continue
            cx = gx0 + (i % cols) * (cw + int(width * 0.03))
            cy = gy0 + (i // cols) * (ch + int(height * 0.03))
            img.paste(thumb, (cx, cy))
            d.rectangle([cx, cy, cx + cw, cy + ch], outline=(120, 200, 255), width=2)
    else:
        nf = _font(int(height * 0.032))
        for ln in _wrap(d, note, nf, width - 2 * pad):
            d.text((pad, y), ln, font=nf, fill=(210, 225, 245))
            y += int(nf.size * 1.4)
    return img


def render_concept_video(concept: dict, keyframes: list[str], out_path: str,
                         fps: int = 24, hold: float = 3.0, xfade: float = 0.4,
                         bgm=None, width: int = 1280, height: int = 720,
                         blocking_images: list[str] | None = None) -> str:
    """把概念企划渲染成更详细的视频 demo（或 PNG 序列），返回输出路径。

    卡组：封面 → 项目介绍 → 世界观设定 → 人物小传(C+真实数据) → 叙事结构
    → 分镜(逐镜/概念规划) → 视觉参考(关键帧九宫格) → 制作链路 → 结尾。
    卡片间交叉淡入转场；可选 BGM（需 ffmpeg）。字段缺失以派生文案兜底。
    """
    bib = concept if isinstance(concept, dict) else {}
    title = bib.get("title") or bib.get("logline") or "未命名短片"
    logline = bib.get("logline", "")
    outline = bib.get("outline", []) or []
    setting = bib.get("setting", "")
    protagonist = bib.get("protagonist", "")
    tone = bib.get("tone", "")
    motif = bib.get("visual_motif", "")
    theme = bib.get("theme", "")
    visual_style = bib.get("visual_style", "")
    three_act = bib.get("three_act") or []
    characters = bib.get("characters") or []
    refs = bib.get("world_refs") or []

    cards: list[Image.Image] = []

    # 1) 封面卡（片名 + 一句话梗概 + 主角提示：这是 Agent 的作品）
    cover_lines = [logline] if logline else []
    if tone:
        cover_lines.append(f"基调：{tone}")
    cover_lines.append("由本地 AI 电影 Agent 自动企划 · 全链路生成")
    cards.append(_card(width, height, title, cover_lines, accent=(120, 200, 255)))

    # 2) ★重点：我做的 AI 电影 Agent 是什么（前置，作为视频主角）
    agent_what = [
        "我做了一个本地运行的「AI 电影 Agent」——",
        "给它一句话创意，它就能自动写出世界观、人物、分镜，",
        "再生成画面、续写成片，最后一键投稿。",
        "核心是两个能力：",
        "① 持续创作：基于 SkyReels-V2 的 Diffusion Forcing 续写，",
        "   影片能不断在结尾追加新镜头，理论上「无限时长」；",
        "② 全自动流水线：从选题到投稿一条龙，几乎不用人动手。",
        "本届目标：用 AI 提前看见未来——让 Agent 把创意变成影像。",
    ]
    cards.append(_card(width, height, "我做的 AI 电影 Agent · 是什么", agent_what,
                       accent=(255, 196, 92)))

    # 3) ★重点：全链路 A→H 流水线（逐段展开，这是 Agent 的「肌肉」）
    pipeline_stages = [
        "A 资料采集：Crawl4AI 抓取素材，沉淀灵感；",
        "B 知识沉淀：RAGFlow 本地知识库，检索与企划回填；",
        "C 概念企划：自动产出世界观 / 人物 / 三幕结构；",
        "D 关键帧：ComfyUI + SDXL 按分镜绘制视觉参考图；",
        "E 剧本分镜：本地 LLM 写剧本（无模型也能模板兜底）；",
        "F 去 AI 味：润色方法论，让台词更自然；",
        "G 视频导演：SkyReels / DF 做 I2V 运动生成与续写；",
        "H 自动发布：biliup-rs 一键投稿 B 站。",
    ]
    cards.append(_card(width, height, "全链路 A→H · Agent 自动跑完", pipeline_stages,
                       accent=(120, 200, 255)))

    # 4) 世界观设定卡（作品本身，作为 Agent 的产出示例）
    setting_lines: list[str] = []
    if setting:
        setting_lines.append(f"场景 / 时代：{setting}")
    if protagonist:
        setting_lines.append(f"主角：{protagonist}")
    if tone:
        setting_lines.append(f"基调：{tone}")
    if motif:
        setting_lines.append(f"视觉母题：{motif}")
    if theme:
        setting_lines.append(f"主题：{theme}")
    if visual_style:
        setting_lines.append(f"美术风格：{visual_style}")
    elif motif or tone:
        style = "美术风格：" + (motif if motif else f"{tone} 化的视觉语言")
        setting_lines.append(style)
    if tone:
        setting_lines.append(f"声音基调：以「{tone}」为底，配乐留白与电子质感交织。")
    if not setting_lines:
        setting_lines.append("（由 Agent 基于创意自动派生，此处为占位。）")
    cards.append(_card(width, height, f"Agent 的产出 · 世界观设定", setting_lines))

    # 5) 人物小传卡（C+ 真实数据，最多 3 个）
    for ci, ch in enumerate(characters[:3]):
        if isinstance(ch, dict):
            cname = ch.get("name", f"人物 {ci + 1}")
            clines = [f"角色：{ch['role']}" for _ in [0] if ch.get("role")]
            clines += [f"性格：{ch.get('personality', '')}",
                       f"动机：{ch.get('motivation', '')}",
                       f"弧光：{ch.get('arc', '')}"]
        else:
            cname = f"人物 {ci + 1}"
            clines = [str(ch)]
        clines = [c for c in clines if c]
        cards.append(_card(width, height, f"人物小传 · {cname}", clines))

    # 6) 叙事结构卡（三幕）：优先用 enrich 产出的 three_act
    if three_act:
        structure = [t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)
                     for t in three_act]
    else:
        structure = _build_structure(logline, outline)
    cards.append(_card(width, height, "叙事结构 · 三幕", structure))

    # 7) 分镜卡：有 outline 逐镜展示；否则给出概念阶段规划
    #    分镜卡配图优先用 Blender 白模预视图（blocking_images），其次关键帧
    if outline:
        for i, beat in enumerate(outline):
            beat_text = beat if isinstance(beat, str) else json.dumps(beat, ensure_ascii=False)
            kf = None
            if blocking_images and i < len(blocking_images) and blocking_images[i]:
                kf = blocking_images[i]
            elif keyframes and i < len(keyframes):
                kf = keyframes[i]
            cards.append(_card(width, height, f"分镜 {i + 1} / {len(outline)}",
                               [beat_text], image=kf if kf else None))
    else:
        cards.append(_card(width, height, "分镜规划（概念阶段）", structure))

    # 8) 视觉参考卡：优先用白模分镜预视图，其次关键帧九宫格
    visual_refs = blocking_images if (blocking_images and any(blocking_images)) else keyframes
    ref_title = "视觉参考 · 白模分镜" if blocking_images else "视觉参考 · 关键帧"
    ref_note = ("白模(Blender)分镜预视：锁定机位 / 景别 / 角色站位，供 AI 出图与视频参考。"
                if blocking_images else
                "关键帧将在 D 阶段由 ComfyUI / SDXL 生成，此处为占位。")
    cards.append(_contact_sheet(width, height, visual_refs, ref_title, note=ref_note))

    # 9) 技术栈卡（开源工具链，呼应流水线）
    stack = [
        "技术栈（全本地 / 开源）：",
        "Crawl4AI + RAGFlow  ——  素材与知识；",
        "MetaGPT 式多角色  ——  概念企划；",
        "ComfyUI + SDXL  ——  关键帧出图；",
        "SkyReels-V2 / DF  ——  视频续写引擎；",
        "OpenCV / PIL  ——  demo 渲染编码（无 ffmpeg 依赖）；",
        "biliup-rs  ——  一键投稿 B 站；",
        f"规格：{width}x{height} · {fps}fps · 约 {int(len(cards) * hold)}s demo。",
    ]
    cards.append(_card(width, height, "技术栈 · 开源工具链", stack))

    # 10) 结尾卡（回到 Agent + 求三连/打赏）
    end_lines = ["本片创意 / 规划由本地 AI 电影 Agent 自动生成。"]
    if refs:
        end_lines.append(f"知识库参考 {len(refs)} 条（RAGFlow / 本地）。")
    end_lines.append("用 AI 提前看见未来——一个 Agent 就能拍电影。")
    end_lines.append("欢迎三连 / 关注 / 打赏，看 Agent 把规划变成成片。")
    cards.append(_card(width, height, "— 规划 demo 完 —", end_lines, accent=(255, 196, 92)))

    result = _write_mp4(cards, out_path, fps=fps, hold=hold, xfade=xfade, bgm=bgm)
    print(f"  [concept-video] 已生成视频素材: {result}")
    return result


def render_cover(concept: dict, keyframes: list[str], out_path: str,
                 width: int = 1080, height: int = 1440) -> str:
    """生成 B 站投稿用的竖版封面/海报（默认 1080x1440）。

    有起始关键帧时以其作背景并加底部渐变遮罩保证文字可读；无则用深色底。
    """
    bib = concept if isinstance(concept, dict) else {}
    title = bib.get("title") or bib.get("logline") or "未命名短片"
    logline = bib.get("logline", "")

    img: Image.Image = Image.new("RGB", (width, height), (18, 18, 24))
    if keyframes and os.path.exists(keyframes[0]):
        try:
            bg = Image.open(keyframes[0]).convert("RGB").resize((width, height))
            img = bg
        except Exception:
            pass

    # 底部渐变遮罩（提升文字可读性）
    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top = int(height * 0.45)
    for y in range(top, height):
        alpha = int(190 * (y - top) / max(1, (height - top)))
        gd.line([(0, y), (width, y)], fill=(8, 8, 14, alpha))
    img = img.convert("RGBA")
    img.alpha_composite(grad)
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)

    pad = int(width * 0.06)
    # 顶部角标
    d.text((pad, int(height * 0.04)), "AI 电影 · 创意规划",
           font=_font(int(height * 0.025)), fill=(120, 200, 255))
    # 标题 + 梗概（底部，梗概字号自适应避免溢出）
    tf = _font(int(height * 0.06), bold=True)
    y0 = int(height * 0.55)
    t_lines = _wrap(d, title, tf, width - 2 * pad)
    used = len(t_lines) * int(tf.size * 1.2) + int(height * 0.02)
    avail = height - int(height * 0.04) - used - int(height * 0.05)
    lf_size = int(height * 0.03)
    lf = None
    l_lines = []
    for ls in range(lf_size, 12, -1):
        lf = _font(ls)
        l_lines = _wrap(d, logline, lf, width - 2 * pad)
        if len(l_lines) * int(lf.size * 1.4) <= avail:
            break
    else:
        lf = _font(12)
        l_lines = _wrap(d, logline, lf, width - 2 * pad)
    y = y0
    for ln in t_lines:
        d.text((pad, y), ln, font=tf, fill=(255, 255, 255))
        y += int(tf.size * 1.2)
    y += int(height * 0.02)
    for ln in l_lines:
        d.text((pad, y), ln, font=lf, fill=(210, 225, 245))
        y += int(lf.size * 1.4)

    img.save(out_path)
    print(f"  [concept-video] 已生成封面: {out_path}")
    return out_path
