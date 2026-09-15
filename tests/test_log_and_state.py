"""P2-⑫⑮：运行日志环缓冲（有界 + 增量拉取）与白模资产索引单一来源。

⑫ 原先 `_state["logs"]` 是无界 list，且 /api/logs 每次都把全部内容 `"".join()`
   返回：一轮 30~60 分钟的渲染既撑内存，又让每 1s 一次的轮询响应体长到几 MB。
⑮ 原先 blocking_control / blocking_anim / blocking_fc 既是实例属性、又单独写进
   self.state，于是重启后从 state.json 载入的是 state 那份、实例属性仍是 []，
   白模条件（控制图->ref_images / 灰模动画->ref_video / depth->Fun Control）
   会静默失效且日志上一片正常。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CFG = {
    "project": {"title": "测试片", "theme": "a test theme", "scene_frames": 97},
    "engine": {"fps": 24, "skyreels_repo": "./skyreels_v2"},
    "llm": {"disabled": True},
    "publish": {"enabled": False, "binary": "definitely_not_exist_biliup_xyz"},
    "collector": {"enabled": True, "method": "requests", "urls": []},
    "planner": {"enabled": True},
    "image_prompt": {"enabled": False},
    "blender": {"enabled": False},
}


@pytest.fixture(autouse=True)
def _fresh_logbuf():
    """日志缓冲是模块级单例，逐例隔离，避免相互污染。"""
    from webserver.state import logbuf
    logbuf.clear()
    yield
    logbuf.clear()


# ---------------------------------------------------------------- ⑫ 环缓冲
def test_log_buffer_is_bounded_by_chars():
    from webserver.state import LogBuffer
    buf = LogBuffer(max_chars=5000)
    for i in range(50):
        buf.append(f"line{i:03d}" + "x" * 500)
    assert buf.total_chars <= 5000, buf.total_chars
    assert len(buf) < 50                       # 老条目确实被淘汰了
    text, cursor = buf.read()["text"], buf.read()["cursor"]
    assert text.endswith("x" * 500)            # 最新的内容一定保留
    assert cursor == 51                        # 序号是单调递增的总条数 + 1


def test_log_buffer_incremental_read():
    from webserver.state import LogBuffer
    buf = LogBuffer()
    buf.append("a")
    buf.append("b")
    buf.append("c")
    assert buf.read()["text"] == "abc"                     # since=None 全量（老行为）
    assert buf.read(1)["text"] == "abc"
    assert buf.read(3)["text"] == "c"
    assert buf.read(4)["text"] == ""                       # 已读到最新
    assert buf.read(3)["cursor"] == 4
    assert buf.read(3)["reset"] is False


def test_log_buffer_flags_truncation_gap():
    """客户端游标落在已淘汰区间时要如实告知，否则前端会以为日志是连续的。"""
    from webserver.state import LogBuffer
    buf = LogBuffer(max_chars=10)
    buf.append("aaaaa")
    buf.append("bbbbb")
    buf.append("ccccc")                     # 触发淘汰 seq=1
    assert len(buf) == 2
    assert buf.read(1)["truncated"] is True
    assert buf.read(2)["truncated"] is False
    assert buf.read()["truncated"] is False  # 不带 since 不算截断


def test_log_buffer_clear_invalidates_stale_cursor():
    """新任务会清空缓冲；带着上一轮游标来问的客户端必须收到 reset 并重拉。"""
    from webserver.state import LogBuffer
    buf = LogBuffer()
    buf.append("old1")
    buf.append("old2")
    stale = buf.read()["cursor"]             # 3
    buf.clear()
    got = buf.read(stale)
    assert got["reset"] is True and got["text"] == ""
    buf.append("new1")
    assert buf.read()["text"] == "new1"      # 重拉拿到的是新任务的日志，无旧内容
    assert buf.read(0)["truncated"] is False


def test_log_buffer_ignores_empty_writes():
    from webserver.state import LogBuffer
    buf = LogBuffer()
    buf.append("")
    assert len(buf) == 0 and buf.read()["cursor"] == 1


def test_api_logs_since_is_incremental_and_keeps_backward_compat():
    """不传 since 的响应结构必须和拆分前一致（老前端/脚本仍在用）。"""
    import webui
    from webserver.state import logbuf

    client = webui.app.test_client()
    logbuf.append("第一段\n")
    logbuf.append("第二段\n")

    full = client.get("/api/logs").get_json()
    assert full["logs"] == "第一段\n第二段\n"
    assert full["running"] is False and full["result"] is None
    assert isinstance(full["cursor"], int)

    # 只取新增：从 cursor 之后追加的内容
    cur = full["cursor"]
    logbuf.append("第三段\n")
    delta = client.get(f"/api/logs?since={cur}").get_json()
    assert delta["logs"] == "第三段\n"
    assert delta["cursor"] > cur

    # 再问一次：没有新增就该是空串（前端 append 不会重复显示）
    again = client.get(f"/api/logs?since={delta['cursor']}").get_json()
    assert again["logs"] == ""


# ------------------------------------------------- ⑮ 白模资产索引单一来源
INDEX_KEYS = ("blocking_control", "blocking_anim", "blocking_fc",
              "image_prompts", "keyframe_images")


def test_asset_indexes_are_properties_not_instance_attrs():
    """必须保持 property：一旦退回实例属性，就又会和 self.state 分成两个真相。"""
    from agent.agent import MovieAgent
    for name in INDEX_KEYS:
        assert isinstance(MovieAgent.__dict__.get(name), property), \
            f"{name} 应是 property（单一来源=self.state）"


def test_asset_indexes_read_and_write_state():
    from agent.agent import MovieAgent
    m = MovieAgent.__new__(MovieAgent)
    m.state = {}
    assert m.blocking_control == [] and m.blocking_fc == []   # 缺键 -> 空列表，不是 None
    m.blocking_control = [{"depth": "d.png"}]
    m.blocking_fc = ["fc.mp4"]
    m.image_prompts = ["p1"]
    m.keyframe_images = ["k1"]
    assert m.state["blocking_control"] == [{"depth": "d.png"}]
    assert m.state["blocking_fc"] == ["fc.mp4"]
    assert m.state["image_prompts"] == ["p1"]
    assert m.state["keyframe_images"] == ["k1"]
    m.blocking_anim = None
    assert m.state["blocking_anim"] == []


def test_asset_indexes_survive_agent_restart():
    """核心回归：上一轮写进 state.json 的白模资产，重启后必须仍能被读到。

    修复前 self.blocking_control 在 __init__ 里被重置为 []，且 else 分支只回填
    image_prompts / keyframe_images —— 白模条件于是静默失效。
    """
    from agent.agent import MovieAgent

    work = tempfile.mkdtemp(prefix="asset_restart_")
    first = MovieAgent(CFG, work)
    first.blocking_control = [{"depth": "d.png", "normal": "n.png", "line": "l.png"}]
    first.blocking_anim = ["anim.mp4"]
    first.blocking_fc = ["fc.mp4"]
    first.keyframe_images = ["k0.png", "k1.png"]
    first._save_state(first.state)

    # 模拟进程重启：同一 workdir 重新构造
    second = MovieAgent(CFG, work)
    assert second.blocking_control == [{"depth": "d.png", "normal": "n.png",
                                        "line": "l.png"}]
    assert second.blocking_anim == ["anim.mp4"]
    assert second.blocking_fc == ["fc.mp4"]
    assert second.keyframe_images == ["k0.png", "k1.png"]
    # 也确认磁盘上确实是这些值
    on_disk = json.load(open(second.state_path, encoding="utf-8"))
    assert on_disk["blocking_fc"] == ["fc.mp4"]
    assert on_disk["keyframe_images"] == ["k0.png", "k1.png"]


def test_previs_merged_keyframes_are_persisted():
    """previs 替换后的关键帧原先只写实例属性，_save_state 存的是旧值。"""
    from agent.agent import MovieAgent
    m = MovieAgent.__new__(MovieAgent)
    m.state = {"keyframe_images": ["old.png"]}
    merged = list(m.keyframe_images)
    merged[0] = "previs.png"
    m.keyframe_images = merged                      # 与 agent.py 里的写法一致
    assert m.state["keyframe_images"] == ["previs.png"]


def test_safe_under_rejects_traversal_after_backslash_normalization():
    """反斜杠必须先归一化：同一份代码在 Linux(CI) 与 Windows 上要得出同一结论。

    不归一化时 `..\\config.yaml` 在 Windows 被 normpath 解成越界（拒绝），
    在 POSIX 上只是个含反斜杠的普通文件名（放行）—— 这正是 CI 抓到的那条。
    """
    from webserver.state import safe_under

    base = os.path.join(tempfile.gettempdir(), "anchor_demo")
    for evil in ("../x", "..\\x", "sub/../../x", "/etc/passwd", "../", ""):
        got = safe_under(base, evil)
        if evil == "":
            continue                    # 空串解析到 base 本身，由调用方的 isfile 兜住
        assert got == "", f"{evil!r} 未被拦截：{got}"

    ok = safe_under(base, "mira_front.png")
    assert os.path.basename(ok) == "mira_front.png"
    assert os.path.dirname(ok) == os.path.normpath(base)
    assert safe_under(base, "sub/mira_front.png") == \
        os.path.normpath(os.path.join(base, "sub", "mira_front.png"))

