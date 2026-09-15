"""load_config() 的 mtime 缓存：行为锁。

原先 `load_config()` 每次调用都 `open()` + `yaml.safe_load()` + `apply_env_overrides()`，
而一次请求里会调好几次、前端还在 1s 轮询 `/api/logs`，等于白白重复解析 13KB YAML
（实测 safe_load ~10.8ms，deepcopy ~0.10ms）。改成按 `(mtime_ns, size)` 缓存后，
最容易踩的坑有两个，这里各锁一条：

  1. **返回共享引用** —— MovieAgent / Publisher 会长期持有 config 并往里写，
     若两次调用拿到同一个 dict，一处改动会渗到另一处（且会污染缓存本体）。
  2. **改了 config.yaml 却不失效** —— 缓存 key 只看 mtime+size，写回文件后
     必须能观察到新值；`invalidate_config_cache()` 是 save 路径的兜底。
"""
from __future__ import annotations

import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webserver import state


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """把 CONFIG_PATH 指到临时文件，并逐例重置缓存，避免污染其它用例。"""
    state.invalidate_config_cache()
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"project": {"title": "v1"}}), encoding="utf-8")
    monkeypatch.setattr(state, "CONFIG_PATH", str(path))
    yield path
    state.invalidate_config_cache()


def test_load_config_returns_independent_copies(cfg_file):
    a = state.load_config()
    b = state.load_config()
    assert a == b
    assert a is not b
    # 改 A 不能影响 B / 不能影响缓存本体（否则下一次调用会拿到被改脏的值）
    a["project"]["title"] = "mutated"
    assert state.load_config()["project"]["title"] == "v1"


def test_cache_hit_avoids_reparsing(cfg_file, monkeypatch):
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(stream):
        calls["n"] += 1
        return real(stream)

    monkeypatch.setattr(state.yaml, "safe_load", counting)

    state.load_config()
    state.load_config()
    state.load_config()
    assert calls["n"] == 1, "同指纹下应命中缓存，只解析一次"


def test_file_change_invalidates_cache(cfg_file, monkeypatch):
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(stream):
        calls["n"] += 1
        return real(stream)

    monkeypatch.setattr(state.yaml, "safe_load", counting)

    assert state.load_config()["project"]["title"] == "v1"

    cfg_file.write_text(yaml.safe_dump({"project": {"title": "v2"}}), encoding="utf-8")
    # 某些文件系统 mtime 精度只有 1s，显式推一下，保证指纹变化
    st = os.stat(cfg_file)
    os.utime(cfg_file, (st.st_atime, st.st_mtime + 2))

    assert state.load_config()["project"]["title"] == "v2"
    assert calls["n"] == 2


def test_invalidate_config_cache_forces_reload(cfg_file, monkeypatch):
    calls = {"n": 0}
    real = yaml.safe_load

    def counting(stream):
        calls["n"] += 1
        return real(stream)

    monkeypatch.setattr(state.yaml, "safe_load", counting)

    state.load_config()
    state.invalidate_config_cache()
    state.load_config()
    assert calls["n"] == 2


def test_missing_config_file_falls_back_to_example(monkeypatch):
    """config.yaml 不入库 —— 缺失时应回退读 config.example.yaml 而不是让接口 500。"""
    state.invalidate_config_cache()
    monkeypatch.setattr(state, "CONFIG_PATH", os.path.join(ROOT, "__no_such_config__.yaml"))
    data = state.load_config()
    assert isinstance(data, dict) and data, "应回退到模板并读到内容"
    assert state._cfg_cache["data"] is not None


def test_signature_follows_fallback_when_config_missing(monkeypatch):
    """config.yaml 缺失但模板在 → 指纹取自模板，模板变化时仍能失效缓存。"""
    monkeypatch.setattr(state, "CONFIG_PATH", os.path.join(ROOT, "__no_such_config__.yaml"))
    sig = state._config_signature()
    assert sig != () and len(sig) == 2


def test_config_and_example_both_missing(monkeypatch, tmp_path):
    """连模板都不在（极端情况）：不抛异常，用空配置起服务，指纹为空。"""
    state.invalidate_config_cache()
    monkeypatch.setattr(state, "CONFIG_PATH", str(tmp_path / "__no__.yaml"))
    monkeypatch.setattr(state, "CONFIG_EXAMPLE", str(tmp_path / "__no_example__.yaml"))
    assert state._config_signature() == ()
    assert state.load_config() == {}


def test_nested_structure_survives_deepcopy(cfg_file):
    """deepcopy 不能把嵌套容器拍平成别的东西（保持原语义）。"""
    cfg_file.write_text(
        yaml.safe_dump({"a": {"b": [1, 2, {"c": 3}]}, "n": 1.5, "flag": True}),
        encoding="utf-8",
    )
    state.invalidate_config_cache()
    data = state.load_config()
    assert data["a"]["b"][2] == {"c": 3}
    assert data["n"] == 1.5 and data["flag"] is True
