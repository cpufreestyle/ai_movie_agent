"""WebUI 共享状态与基础设施。

拆分自原 webui.py 的「工具」段：路径常量、后台任务/日志捕获、agent 惰性构造。
本模块是这些状态的**单一来源**：视图层与服务层都从这里取，不再各自持有副本。

注意 `HERE` 指向**仓库根**（本文件在 webserver/ 下，需上跳一级），
`webui/` 模板目录与 `outputs/` 都相对它定位。
"""
from __future__ import annotations

import contextlib
import glob
import io
import json
import os
import subprocess
import threading

import yaml
from flask import Response

from config_env import apply_env_overrides

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(HERE, "config.yaml")
WORKDIR = os.path.join(HERE, "outputs")

# 媒体文件白名单（仅允许预览这些，避免任意路径遍历）
MEDIA = {
    "concept_demo": os.path.join(WORKDIR, "scenes", "concept_demo.mp4"),
    "concept_cover": os.path.join(WORKDIR, "scenes", "concept_cover.png"),
    "film": os.path.join(WORKDIR, "film.mp4"),
    "movie_final": os.path.join(WORKDIR, "movie_final.mp4"),
    # 第一集成片（LTX-2.5 链路）：英文配音+中英双语字幕
    "ep1_vo": os.path.join(WORKDIR, "ep1_vo.mp4"),
}

# 白模续集自包含播放页（EP4/EP5），由 _mk_player.py 生成；/white/<ep> 直接回传
WHITE_PAGES = {
    "ep4": os.path.join(WORKDIR, "ep4_white_model", "ep4_film.html"),
    "ep5": os.path.join(WORKDIR, "ep5", "ep5_film.html"),
}

# WebUI 编辑分镜/解说后的保存位置；生成脚本检测到它就覆盖内置分镜
STORYBOARD_PATH = os.path.join(WORKDIR, "storyboard.json")

# 时间轴编辑（WebUI 时间轴页 + cli timeline 共用）：镜头顺序/启停/裁剪
TIMELINE_PATH = os.path.join(WORKDIR, "timeline.json")

# 三集剧集标题（series_script.json 缺失时的兜底）
EP_TITLES = {1: "进城", 2: "觉醒", 3: "对抗"}

_state = {
    "agent": None,
    "agent_error": None,
    "thread": None,
    "running": False,
    "stop": False,
    "logs": [],
    "result": None,
}
_lock = threading.Lock()
STAGE_NAMES = {
    "A": "资料采集",
    "B": "知识沉淀",
    "C": "概念企划",
    "D": "关键帧",
    "E": "剧本分镜",
    "F": "文本润色",
    "G": "视频导演",
    "H": "封装发布",
}


# ---------------- 工具 ----------------
def json_resp(data, status=200):
    # 显式声明 charset：不写的话部分客户端(如 PowerShell/ConvertFrom-Json)会按本地编码解，中文变乱码
    return Response(json.dumps(data, ensure_ascii=False),
                    mimetype="application/json; charset=utf-8", status=status)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return apply_env_overrides(yaml.safe_load(f) or {})


def get_agent():
    """惰性构造并重用 MovieAgent（构造失败也只影响相关接口，不拖垮服务）。"""
    if _state["agent"] is not None or _state["agent_error"] is not None:
        if _state["agent_error"]:
            raise RuntimeError(_state["agent_error"])
        return _state["agent"]
    try:
        from agent.agent import MovieAgent
        _state["agent"] = MovieAgent(load_config(), WORKDIR)
    except Exception as e:  # 例如缺少 torch / SkyReels 依赖
        _state["agent_error"] = (f"Agent 初始化失败（依赖或环境缺失，"
                                  f"不影响配置/创意策划等接口）：{e}")
        raise RuntimeError(_state["agent_error"])
    return _state["agent"]


class _LogSink(io.TextIOBase):
    def write(self, s: str) -> int:
        with _lock:
            _state["logs"].append(s)
        return len(s)

    def flush(self):
        pass


def _stop_requested() -> bool:
    """当前是否有停止请求（供 agent 循环与子进程泵轮询）。

    原先 _state["stop"] 只被写入、**没有任何读取点** → 「停止」按钮完全不生效。
    现在由 thread 安全地读取，交给 agent 的 should_stop 回调与 run_script 的轮询。
    """
    with _lock:
        return bool(_state["stop"])


def _kill_tree(proc) -> None:
    """终止子进程及其整棵进程树。

    Windows 上 proc.kill() 只杀直接子进程，渲染脚本拉起的 ffmpeg / python 孙进程
    会变成孤儿继续跑（还占着 GPU 与输出文件句柄）→ 优先用 taskkill /T 连树一起杀。
    失败（如沙箱拦截 taskkill）时退回 proc.kill()。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=20)
            if proc.poll() is None:
                proc.kill()
        else:
            proc.kill()
    except Exception:                 # noqa: BLE001 - 兜底再杀一次，不让停止逻辑抛错
        try:
            proc.kill()
        except Exception:             # noqa: BLE001
            pass


def run_in_background(fn):
    """在后台线程跑 fn，捕获 stdout/stderr 到 _state['logs']。"""
    def _wrapped():
        with _lock:
            _state["running"] = True
            _state["stop"] = False          # 新任务开始，清掉上一轮遗留的停止请求
            _state["logs"] = []
            _state["result"] = None
        sink = _LogSink()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                _state["result"] = fn()
        except Exception as e:  # noqa: BLE001
            with _lock:
                _state["logs"].append(f"[webui] 任务异常: {e}\n")
            _state["result"] = {"error": str(e)}
        finally:
            with _lock:
                _state["running"] = False
    t = threading.Thread(target=_wrapped, daemon=True)
    with _lock:
        _state["thread"] = t
    t.start()


def start_stage(stage: str, fn):
    if _state["running"]:
        return json_resp({"ok": False, "error": "已有任务在运行"}, status=409)

    def _job():
        print(f"\n=== {stage} 阶段：{STAGE_NAMES[stage]} ===")
        result = fn()
        print(f"=== {stage} 阶段完成 ===")
        return result

    run_in_background(_job)
    return json_resp({"ok": True, "msg": f"{stage} 阶段已启动"})


def load_material() -> list[dict]:
    items = []
    for path in sorted(glob.glob(os.path.join(WORKDIR, "material", "*.md"))):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        heading, _, content = text.partition("\n")
        items.append({"url": heading.lstrip("# ").strip(), "text": content.strip()})
    return items


def save_agent_state(agent):
    agent._save_state(agent.state)


def pipeline_snapshot():
    agent = get_agent()
    state = agent.state
    material = load_material()
    kb_path = os.path.join(WORKDIR, "kb", "chunks.jsonl")
    keyframes = sorted(glob.glob(os.path.join(WORKDIR, "keyframes", "*.*")))
    stages = {
        "A": {"done": bool(material), "count": len(material)},
        "B": {"done": os.path.exists(kb_path), "count": sum(1 for _ in open(kb_path, encoding="utf-8")) if os.path.exists(kb_path) else 0},
        "C": {"done": bool(state.get("bible", {}).get("outline")), "count": len(state.get("bible", {}).get("outline", []))},
        "D": {"done": bool(state.get("image_prompts")), "count": len(keyframes), "ready": agent.keyframe_gen.is_ready()},
        "E": {"done": bool(state.get("draft_beat") or state.get("beats")), "count": len(state.get("beats", []))},
        "F": {"done": bool(state.get("draft_beat", {}).get("polished")), "count": 1 if state.get("draft_beat", {}).get("polished") else 0},
        "G": {"done": bool(state.get("draft_prompt") or state.get("scene_count")), "count": state.get("scene_count", 0), "ready": agent.engine.is_ready()},
        "H": {"done": os.path.exists(MEDIA["movie_final"]), "count": 1 if os.path.exists(MEDIA["movie_final"]) else 0},
    }
    return {
        "stages": stages,
        "material": [{"url": x["url"], "text": x["text"][:1200]} for x in material],
        "bible": state.get("bible", {}),
        "image_prompts": state.get("image_prompts", []),
        "keyframes": [os.path.basename(x) for x in keyframes],
        "draft_beat": state.get("draft_beat"),
        "draft_prompt": state.get("draft_prompt", ""),
        "beats": state.get("beats", []),
        "engine_ready": agent.engine.is_ready(),
    }
