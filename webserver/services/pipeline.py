"""成片链路业务逻辑：分镜桥接 / 子进程执行 / 成片列表与探测 / 剧集元数据。

原 webui.py 里内联在路由中的逻辑（storyboard_from_bible 77 行、run_script 45 行等）
集中到这里，路由只负责参数校验与响应包装。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

from ..state import (
    HERE,
    STORYBOARD_PATH,
    WORKDIR,
    _kill_tree,
    _stop_requested,
    get_agent,
    load_config,
    load_material,
)


def film_candidates() -> list[dict]:
    """outputs 下的成片 mp4，按修改时间倒序。"""
    items = []
    for path in glob.glob(os.path.join(WORKDIR, "*.mp4")):
        items.append({
            "name": os.path.basename(path),
            "size_mb": round(os.path.getsize(path) / 2 ** 20, 2),
            "mtime": os.path.getmtime(path),
        })
    # 白模续集（EP4/EP5）成片在子目录，单独纳入 picker（name 用相对路径）
    for rel in ("ep5/ep5_film.mp4", "ep4_white_model/ep4_film.mp4"):
        p = os.path.join(WORKDIR, rel)
        if os.path.isfile(p):
            items.append({
                "name": rel,
                "size_mb": round(os.path.getsize(p) / 2 ** 20, 2),
                "mtime": os.path.getmtime(p),
            })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def load_storyboard() -> dict:
    """优先用 outputs/storyboard.json（WebUI 改过的），否则读脚本内置默认分镜。"""
    overridden = os.path.exists(STORYBOARD_PATH)
    if overridden:
        with open(STORYBOARD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        shots = data.get("shots") or []
        narration = data.get("narration") or []
    else:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import importlib
        shots = list(importlib.import_module("shots").SHOTS)
        narration = list(importlib.import_module("make_narration").LINES)
    return {"shots": shots, "narration": narration, "overridden": overridden}


def _zh_ratio(s: str) -> float:
    """字符串里中文字符的占比，用于校验解说是否被 LLM 写成了英文。"""
    if not s:
        return 0.0
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff") / len(s)


_STORYBOARD_SYSTEM = (
    "你是资深科幻短片分镜编剧。任务：根据给定概念，产出可直接喂给图生视频模型"
    "(LTX-2.5，使用英文提示词)的镜头列表，以及一段配套的中文第一人称内心独白解说"
    "(用于配音+字幕)。\n"
    "要求：\n"
    "- shots：恰好 18 条英文镜头描述，每条一句，含 主体+动作+场景+光影/镜头运动+风格，"
    "默认日式动漫风格（anime style, cel-shaded, clean line art, vibrant colors），"
    "画面连续可拼接成约 60 秒短片，紧扣主题。\n"
    "- narration：恰好 9 条中文解说，第一人称内心独白，口语化、有情绪递进；"
    "关键：每句必须短（12-18 字），一口气能说完（约 4-5 秒），不要写复合长句或并列句，"
    "否则配音会被加速显得机械。\n"
    "- 只输出 JSON，形如：{\"shots\":[...18...],\"narration\":[...9...]}，不要多余文字。"
)


def _storyboard_prompts(theme: str, bible: dict, material: list) -> tuple:
    """构造分镜扩写的 system / user 提示词。"""
    refs = "\n".join(f"- {m['text'][:600]}" for m in material[:3])
    user = (
        f"主题概念：{theme}\n\n概念企划：\n{json.dumps(bible, ensure_ascii=False)[:1500]}\n\n"
        f"参考素材（节选）：\n{refs}\n\n请产出 18 镜英文分镜与 9 段中文解说。"
    )
    return _STORYBOARD_SYSTEM, user


def _require_str_list(value, name: str) -> list:
    """要求是字符串数组（不校验是否非空）；否则抛 RuntimeError。"""
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise RuntimeError(f"{name} 不是字符串数组")
    return value


def _pad_or_trim(items: list, n: int, empty_fill: str = "") -> tuple:
    """数量对齐：不足补齐（复制末项，空则用 empty_fill），超出截断。

    返回 (新列表, 是否发生了补齐)。
    """
    if len(items) < n:
        fill = items[-1] if items else empty_fill
        return items + [fill] * (n - len(items)), True
    if len(items) > n:
        return items[:n], False
    return items, False


def _normalize_storyboard(data: dict) -> tuple:
    """校验并归一化 LLM 产出的 shots / narration，返回 (shots, narration)。"""
    shots = _require_str_list(data.get("shots"), "shots")
    if not shots:
        raise RuntimeError("shots 不是非空字符串数组")
    narration = _require_str_list(data.get("narration"), "narration")
    shots = [s.strip() for s in shots if s.strip()]
    narration = [s.strip() for s in narration if s.strip()]
    shots, padded = _pad_or_trim(shots, 18)
    if padded:
        print("[storyboard] shots 不足 18，已补至 18")
    narration, _ = _pad_or_trim(narration, 9, empty_fill="……")
    # 校验解说语言：prompt 已明确要求中文，但小模型经常忽略指令直接吐英文。
    # 若静默写入，后续 TTS 会用中文语音念英文、字幕也是英文，成片报废且不易察觉，
    # 因此宁可在这里中断任务，也不产出英文解说。
    bad = [n for n in narration if _zh_ratio(n) < 0.3]
    if bad:
        raise RuntimeError(
            "解说必须是中文，但 LLM 产出了英文（示例："
            + " / ".join(x[:45] for x in bad[:2])
            + "）。请重试，或换更听话的模型（config.llm.model，推荐 gemma4:e2b）。")
    return shots, narration


def storyboard_from_bible() -> dict:
    """C→分镜桥接：用当前企划(bible)+素材，让 LLM 扩写成 18 镜英文分镜 + 9 段中文解说。

    写入 outputs/storyboard.json。抽成函数以便「一键全自动」链路复用。
    """
    from agent.llmutil import make_client, chat, extract_json
    cfg = load_config()
    llm = cfg.get("llm", {}) or {}
    if llm.get("disabled"):
        raise RuntimeError("LLM 已禁用（config.llm.disabled=true）")
    client = make_client(cfg)
    if client is None:
        raise RuntimeError("无法创建 LLM 客户端（缺 openai 包或配置错误）")
    agent = get_agent()
    bible = agent.state.get("bible") or {}
    theme = cfg.get("project", {}).get("theme") or bible.get("logline") or ""
    material = load_material()
    system, user = _storyboard_prompts(theme, bible, material)
    print("[storyboard] 调用 LLM 生成分镜 ...")
    out = chat(client, system, user, max_tokens=3000, temperature=0.85,
               model=llm.get("model"),
               extra_body={"enable_thinking": False})
    if not out:
        raise RuntimeError("LLM 返回为空（可能模型是推理模型且 max_tokens 不足，或模型未加载）")
    try:
        data = json.loads(extract_json(out))
    except Exception as e:
        raise RuntimeError(f"LLM 返回无法解析为 JSON：{e}\n原始：{out[:500]}")
    shots, narration = _normalize_storyboard(data)
    with open(STORYBOARD_PATH, "w", encoding="utf-8") as f:
        json.dump({"shots": shots, "narration": narration}, f,
                  ensure_ascii=False, indent=2)
    print(f"[storyboard] 已写入，{len(shots)} 镜 / {len(narration)} 段解说")
    return {"ok": True, "shots": len(shots), "narration": len(narration)}


def run_script(script: str, timeout: int = 7200, args: list | None = None) -> None:
    """在后台作业里执行本项目脚本；失败即抛异常中断整条链。

    渲染 18 镜约需数十分钟，timeout 默认给到 2 小时。
    args: 传给脚本的额外命令行参数（如 make_narration 的 --film/--auto-dur）。
    """
    print(f"\n=== 运行脚本：{script} {' '.join(args or [])} ===")
    # 子进程默认按本地代码页(中文 Windows=GBK)输出 stdout；这里按 UTF-8 解码，
    # 必须强制子进程用 UTF-8 输出，否则日志里的中文全是乱码。
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    # 输出必须**边跑边转发**：原先 subprocess.run(capture_output=True) 会把子进程
    # 输出憋到进程结束才一次性 print，WebUI 日志在长达几十分钟里一片空白，
    # 用户无法区分"正在跑"还是"已经卡死"。改成开线程边读边写 _state['logs']。
    proc = subprocess.Popen([sys.executable, "-u", script, *(args or [])], cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env, encoding="utf-8", errors="replace")

    def _pump():
        # 后台线程逐行转发；print 已被 redirect 到线程安全的 _LogSink
        for line in proc.stdout:
            print(line, end="")

    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    # 边等边响应「停止」/超时：原先直接 proc.wait(timeout)，等待期间点停止完全无效，
    # 只能等渲染自然跑完（数十分钟）。改为 1s 轮询。
    deadline = time.time() + timeout
    while True:
        try:
            proc.wait(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            if _stop_requested():
                _kill_tree(proc)
                t.join(timeout=10)
                raise RuntimeError(
                    f"{script} 已按请求停止（子进程已终止，当前镜头产物可能不完整）")
            if time.time() >= deadline:
                _kill_tree(proc)
                t.join(timeout=10)
                raise RuntimeError(f"{script} 执行超时（>{timeout}s）")
    t.join(timeout=30)               # 等泵把剩余输出读完
    if proc.returncode != 0:
        raise RuntimeError(f"{script} 执行失败（code={proc.returncode}）")


# ---------------- 看板：成片规格探测 ----------------
_probe_cache: dict = {}


def _parse_video_line(line: str, info: dict) -> None:
    """从 ffmpeg 的 ` Video: ` 行解析分辨率 / 帧率 / 视频编码。"""
    mm = re.search(r"(\d{2,5})x(\d{2,5})", line)
    if mm:
        info["width"], info["height"] = int(mm.group(1)), int(mm.group(2))
    fm = re.search(r"([\d.]+) fps", line)
    if fm:
        info["fps"] = round(float(fm.group(1)), 2)
    cm = re.search(r"Video: (\w+)", line)
    if cm:
        info["vcodec"] = cm.group(1)


def _parse_audio_line(line: str, info: dict) -> None:
    """从 ffmpeg 的 ` Audio: ` 行解析音频编码 / 采样率 / 声道。"""
    cm = re.search(r"Audio: (\w+)", line)
    if cm:
        info["acodec"] = cm.group(1)
    sm = re.search(r"(\d+) Hz", line)
    if sm:
        info["sample_rate"] = int(sm.group(1))
    chm = re.search(r"(mono|stereo|5\.1)", line)
    if chm:
        info["channels"] = chm.group(1)


def _parse_probe_streams(err: str, info: dict) -> None:
    """解析 ffmpeg -i 的 stderr 输出，填充时长 / 分辨率 / 帧率 / 编码。"""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration_sec"] = round(
            int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2)
    for line in err.splitlines():
        if " Video: " in line and "width" not in info:
            _parse_video_line(line, info)
        if " Audio: " in line and "acodec" not in info:
            _parse_audio_line(line, info)


def probe_media(path: str) -> dict:
    """探视频规格（时长/分辨率/帧率/编码）。

    按 (path, mtime, size) 缓存：ffmpeg 探一次要 fork 进程，看板每刷新一次
    就要探 3~6 个文件，不缓存会明显卡顿；mtime/size 变了自动失效。
    """
    try:
        st = os.stat(path)
        key = (path, int(st.st_mtime), st.st_size)
    except OSError:
        return {}
    if key in _probe_cache:
        return _probe_cache[key]
    info: dict = {}
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([exe, "-i", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        _parse_probe_streams(r.stderr or "", info)
    except Exception as e:  # noqa: BLE001  探测失败只降级显示，不能拖垮看板
        info["probe_error"] = str(e)
    _probe_cache[key] = info
    return info


def series_script() -> dict:
    path = os.path.join(WORKDIR, "series_script.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


__all__ = [
    "film_candidates",
    "load_storyboard",
    "probe_media",
    "run_script",
    "series_script",
    "storyboard_from_bible",
]
