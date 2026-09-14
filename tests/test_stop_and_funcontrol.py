"""P0 修复的回归测试：停止链路 / Fun Control 复现与前置校验。

不依赖 GPU / ComfyUI / Blender：
  - record.collect 的纯数据组装；
  - MMH3Engine._validate_control_video 的入参校验（构造引擎不打网络）；
  - WebUI 停止标志的读取链路，以及 run_script 对子进程的轮询终止。
"""
from __future__ import annotations

import os
import tempfile
import threading
import time


class _FakeFC:
    """带 Fun Control 属性的假引擎（模拟 MMH3Engine）。"""

    resolution = "768x448"
    num_frames = 56
    fps = 24
    steps = 4
    lora = "turbo.safetensors"
    negative = None
    two_pass = False
    block_cache = False
    fun_control_enable = True
    fc_control_kind = "depth"
    fc_fit_mode = "exact"
    fc_strength = 0.85
    fc_end_percent = 0.85


class _FakeLegacy:
    """不带 Fun Control 属性的旧引擎（LTX / SkyReels）。"""

    resolution = "512x512"
    num_frames = 25
    fps = 24
    steps = 30
    lora = None
    negative = None
    two_pass = False
    block_cache = False


# ---------- P0-② 复现：control_video / fun_control 必须落盘 ----------
def test_record_collect_persists_control_video_and_strength():
    from agent import record
    p = record.collect(_FakeFC(), prompt="a shot", seed=7, attempt=0,
                       ref_video="/x/anim/blocking.mp4",
                       control_video="/x/fc/fc.mp4", fc_strength=1.1)
    # control_video 只存 basename（与 ref_video / ref_images 同一约定）
    assert p["control_video"] == "fc.mp4"
    assert p["ref_video"] == "blocking.mp4"
    # 本次覆盖的 strength 必须写进 fun_control，而不是引擎的默认值
    assert p["fun_control"]["strength"] == 1.1
    assert p["fun_control"]["control_kind"] == "depth"
    assert p["fun_control"]["fit_mode"] == "exact"
    assert p["fun_control"]["enable"] is True


def test_record_collect_falls_back_to_engine_strength_and_omits_none():
    from agent import record
    p = record.collect(_FakeFC(), prompt="a shot", seed=1, attempt=0)
    # 未覆盖 -> 用引擎配置值
    assert p["fun_control"]["strength"] == 0.85
    # 无 control_video / ref_* 时不该出现这些键（保持文件紧凑）
    assert "control_video" not in p
    assert "ref_video" not in p
    assert "image" not in p


def test_record_collect_legacy_engine_has_no_fun_control_block():
    from agent import record
    p = record.collect(_FakeLegacy(), prompt="a shot", seed=1, attempt=0)
    assert "fun_control" not in p
    assert p["engine"] == "_FakeLegacy"


# ---------- P0-③ Fun Control 前置校验 ----------
def _engine():
    from agent.mmh3_engine import MMH3Engine
    # 构造只读配置，不做任何网络请求
    return MMH3Engine({"engine": {"comfyui_mmH3": {
        "num_frames": 56, "resolution": "768x448"}}})


def test_validate_control_video_rejects_missing_explicit_path():
    eng = _engine()
    assert eng.num_frames == 56
    try:
        eng._validate_control_video("Z:/definitely/not/here/fc.mp4", None)
        raise AssertionError("显式传入不可用的 control_video 应当报错，而不是静默忽略")
    except RuntimeError as e:
        assert "不可用" in str(e)


def test_validate_control_video_allows_absent_argument():
    _engine()._validate_control_video(None, None)   # 没传 -> 放行，不抛


def test_validate_control_video_strength_bounds():
    eng = _engine()
    d = tempfile.mkdtemp()
    fc = os.path.join(d, "fc.mp4")
    with open(fc, "wb") as f:
        f.write(b"0" * 64)
    # 非法强度 -> 报错
    for bad in (0, -1.0):
        try:
            eng._validate_control_video(fc, bad)
            raise AssertionError(f"strength={bad} 应当报错")
        except RuntimeError:
            pass
    # ≥1.5 只告警不抛（实测崩坏，但由使用者决定）
    eng._validate_control_video(fc, 1.6)


def test_probe_frame_count_tolerates_missing_ffprobe():
    from agent.mmh3_engine import MMH3Engine
    assert MMH3Engine._probe_frame_count("/no/such/file.mp4") is None


# ---------- P0-① 停止链路 ----------
def test_webui_stop_flag_is_readable():
    import webui
    assert hasattr(webui, "_stop_requested")
    with webui._lock:
        webui._state["stop"] = False
    assert webui._stop_requested() is False
    try:
        with webui._lock:
            webui._state["stop"] = True
        assert webui._stop_requested() is True      # 原先没有任何读取点
    finally:
        with webui._lock:
            webui._state["stop"] = False


def test_run_script_terminates_child_on_stop(tmp_path):
    """run_script 必须轮询停止标志；否则 30~60 分钟的任务只能杀进程。"""
    import webui
    with webui._lock:
        webui._state["stop"] = False
    script = tmp_path / "sleepy.py"
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")

    box: dict = {}

    def _run():
        try:
            webui.run_script(str(script), timeout=60)
        except Exception as e:          # noqa: BLE001
            box["err"] = str(e)

    started = time.time()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.8)
    with webui._lock:
        webui._state["stop"] = True
    t.join(timeout=25)
    elapsed = time.time() - started
    try:
        assert not t.is_alive(), "停止后 run_script 仍未返回"
        assert "已按请求停止" in box.get("err", ""), box
        assert elapsed < 20, f"停止耗时 {elapsed:.1f}s，轮询似乎没生效"
    finally:
        with webui._lock:
            webui._state["stop"] = False
