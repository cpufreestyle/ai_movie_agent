"""长片出片的显存（VRAM）防护：`--free-every` 相关行为锁。

背景：FunControl int8（约 2.3GB）+ H3 主模型的驻留会**累积**，连续出若干镜后
把 ComfyUI 拖崩（实测连出两版 A/B 后崩）。修法是每隔 N 镜通知 ComfyUI 放一次
显存；代价是下一镜要重新加载模型（变慢），所以**默认关闭**、按需开启。

这里锁住四件不必跑真机就能验的事：
  - `ComfyUIClient.free_memory` 真往 `/free` 发正确载荷；
  - 它失败时**不抛**（清理不该中断出片）；
  - `run_series._free_vram` 在没有 client / client 抛异常时都是安全 no-op；
  - CLI 默认 `--free-every 0`（既有出片行为不变）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import run_series  # noqa: E402  （已确认：仅 import 不会建目录 / 探 ffmpeg）
from tools.comfyui_client import ComfyUIClient  # noqa: E402


class _Post:
    """`_post` 的替身：记录调用，可选抛异常。"""

    def __init__(self, raises: bool = False):
        self.calls = []
        self.raises = raises

    def __call__(self, path, **kw):
        self.calls.append((path, kw))
        if self.raises:
            raise RuntimeError("connection reset")
        return object()


def _client(monkeypatch, raises: bool = False):
    c = ComfyUIClient("http://127.0.0.1:8188")
    spy = _Post(raises=raises)
    monkeypatch.setattr(c, "_post", spy)
    return c, spy


# ---------------- ComfyUIClient.free_memory ----------------
def test_free_memory_posts_correct_payload(monkeypatch):
    c, spy = _client(monkeypatch)
    assert c.free_memory() is True
    path, kw = spy.calls[0]
    assert path == "/free"
    assert kw["json"] == {"free_memory": True, "unload_models": True}


def test_free_memory_can_keep_models_loaded(monkeypatch):
    """只清显存、不卸载模型：重出同风格镜头时省一次加载。"""
    c, spy = _client(monkeypatch)
    c.free_memory(unload_models=False)
    assert spy.calls[0][1]["json"]["unload_models"] is False


def test_free_memory_never_raises(monkeypatch):
    """服务不支持 /free 或网络抖动都不能中断出片 —— 返回 False 即可。"""
    c, _ = _client(monkeypatch, raises=True)
    assert c.free_memory() is False


# ---------------- run_series._free_vram ----------------
def test_free_vram_is_noop_without_client():
    """引擎没暴露 client、或 client 没有该方法时静默跳过，不能炸。"""
    run_series._free_vram(object())                                  # 无 client 属性
    run_series._free_vram(type("E", (), {"client": object()})())     # client 无 free_memory


def test_free_vram_swallows_exceptions(capsys):
    class _C:
        def free_memory(self):
            raise RuntimeError("boom")

    class _E:
        client = _C()

    run_series._free_vram(_E())                                      # 不应抛出
    assert "已忽略" in capsys.readouterr().out


# ---------------- CLI 默认值 ----------------
def test_free_every_defaults_to_off(monkeypatch):
    """默认 0 = 不启用：放显存会让下一镜重新加载模型，不能悄悄改变既有行为。"""
    monkeypatch.setattr(sys, "argv", ["run_series.py"])
    assert run_series._parse_args().free_every == 0


def test_free_every_accepts_n(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_series.py", "--free-every", "4"])
    assert run_series._parse_args().free_every == 4
