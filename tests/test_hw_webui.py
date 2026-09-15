"""WebUI「设置页 · 硬件档位下拉框」的数据源：`webserver/services/hw.py` + `GET /api/hw`。

为什么值得单独锁一组断言：**界面上漏一个档位是静默的** —— 新增 `HW_TIER_PROFILES` 档位却
忘记在 `TIER_LABELS` 补中文说明时，下拉框照样渲染（只是显示英文键名），没人会报错。
所以这里强制「档位集合 == 标签集合」，两边都多一个少一个都要失败。

另外锁 `current()` 的两个边界，都是「静默出错」的高发区：
  - config 里写的是**别名**（`395` / `strix-halo`）→ 必须归一后让下拉框选中同一档，
    否则用户看到的是「默认 · 不改动」，以为没配。
  - config 里写的是**无法识别的值** → 必须原样暴露（`unknown=True` + `raw`），
    而不是被当成空值静默丢掉。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config_env as ce  # noqa: E402
from webserver.services import hw  # noqa: E402


# ---------------- 标签表 ↔ 档位表（防漂移） ----------------
def test_every_tier_has_a_label():
    missing = [t for t in ce.HW_TIER_PROFILES if t not in hw.TIER_LABELS]
    assert not missing, f"这些档位没有中文标签，下拉框会显示英文键名：{missing}"


def test_no_label_for_removed_tier():
    extra = [t for t in hw.TIER_LABELS if t not in ce.HW_TIER_PROFILES]
    assert not extra, f"标签表里有已不存在的档位（删档位时忘了删标签）：{extra}"


# ---------------- 选项 ----------------
def test_tier_options_first_item_is_no_tier():
    opts = hw.tier_options()
    assert opts[0]["value"] == ""
    assert opts[0]["label"] == hw.NO_TIER_LABEL
    assert all(o["label"] for o in opts), "每一项都必须有可读标签"


def test_tier_options_cover_all_profiles_in_order():
    values = [o["value"] for o in hw.tier_options()[1:]]
    assert values == list(ce.HW_TIER_PROFILES)
    assert ce.AMD395_TIER in values


def test_values_are_unique():
    vals = [o["value"] for o in hw.tier_options()]
    assert len(vals) == len(set(vals))


def test_profiles_returns_copies_not_references():
    p = hw.profiles()
    p["high"]["engine.offload"] = "tampered"
    p["high"]["__injected__"] = 1
    assert ce.HW_TIER_PROFILES["high"]["engine.offload"] is False
    assert "__injected__" not in ce.HW_TIER_PROFILES["high"]


# ---------------- current()：读 config.yaml ----------------
@pytest.fixture()
def cfg(monkeypatch):
    """把 hw 模块里的 load_config 换成可控桩（按名调用，故打补丁到 hw 模块上）。"""
    box: dict = {"cfg": {}}
    monkeypatch.setattr(hw, "load_config", lambda: box["cfg"])
    return box


def test_current_reads_saved_tier(cfg):
    cfg["cfg"] = {"hw_tier": "amd395-128g", "auto_hardware": True}
    assert hw.current() == {"hw_tier": "amd395-128g", "raw": "amd395-128g",
                            "unknown": False, "auto_hardware": True}


def test_current_normalizes_alias(cfg):
    cfg["cfg"] = {"hw_tier": "  395  "}
    c = hw.current()
    assert c["hw_tier"] == ce.AMD395_TIER, "别名必须归一，否则下拉框选不中同一档"
    assert c["unknown"] is False


def test_current_exposes_unknown_value(cfg):
    cfg["cfg"] = {"hw_tier": "nope"}
    c = hw.current()
    assert c["hw_tier"] == ""
    assert c["raw"] == "nope"
    assert c["unknown"] is True, "无法识别的原值要暴露给前端，不能静默当默认"


@pytest.mark.parametrize("bad", [
    {}, {"hw_tier": None}, {"hw_tier": 42}, {"hw_tier": "   "}, {"hw_tier": []},
])
def test_current_handles_missing_or_non_string(cfg, bad):
    cfg["cfg"] = bad
    c = hw.current()
    assert c["hw_tier"] == "" and c["unknown"] is False and c["raw"] == ""


@pytest.mark.parametrize("val,expected", [
    (True, True), ("true", False), (1, False), (None, False), ("yes", False),
])
def test_current_auto_hardware_only_true_bool(cfg, val, expected):
    """只有真的布尔 True 才算开启（字符串 'true' / 整数 1 都不算，与 config_env 一致）。"""
    cfg["cfg"] = {"auto_hardware": val}
    assert hw.current()["auto_hardware"] is expected


# ---------------- detect() ----------------
def test_detect_maps_amd395(monkeypatch):
    monkeypatch.setattr(ce, "detect_hardware", lambda: {
        "vendor": "AMD", "gpu_name": "AMD Radeon 8060S", "vram_gb": 0.5, "ram_gb": 125.5})
    d = hw.detect()
    assert d["vendor"] == "AMD" and d["tier"] == ce.AMD395_TIER
    assert d["vram_gb"] == 0.5 and d["ram_gb"] == 125.5


def test_detect_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no powershell")

    monkeypatch.setattr(ce, "detect_hardware", boom)
    d = hw.detect()
    assert "error" in d and "no powershell" in d["error"]


# ---------------- payload() ----------------
def test_payload_shape_without_detect(cfg):
    j = hw.payload()
    assert j["ok"] is True
    assert set(j) == {"ok", "tiers", "aliases", "current", "profiles", "amd395_tier"}
    assert "detected" not in j, "默认不该探测（Windows 下调 PowerShell 约 1~2 秒）"
    assert j["amd395_tier"] == ce.AMD395_TIER
    assert j["aliases"]["395"] == ce.AMD395_TIER


def test_payload_with_detect(cfg, monkeypatch):
    monkeypatch.setattr(ce, "detect_hardware", lambda: {
        "vendor": "NVIDIA", "gpu_name": "RTX 4090", "vram_gb": 24.0, "ram_gb": 64.0})
    j = hw.payload(with_detect=True)
    assert j["detected"]["tier"] == "high"


# ---------------- 路由 ----------------
@pytest.fixture()
def client():
    import webui

    webui.app.config["TESTING"] = True
    return webui.app.test_client()


def test_api_hw_returns_options(client):
    r = client.get("/api/hw")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert [t["value"] for t in j["tiers"]][1:] == list(ce.HW_TIER_PROFILES)
    assert "detected" not in j


def test_api_hw_detect_flag(client, monkeypatch):
    monkeypatch.setattr(ce, "detect_hardware", lambda: {
        "vendor": "AMD", "gpu_name": "AMD Ryzen AI Max+ 395",
        "vram_gb": 96.0, "ram_gb": 128.0})
    j = client.get("/api/hw?detect=1").get_json()
    assert j["detected"]["tier"] == ce.AMD395_TIER


@pytest.mark.parametrize("q", ["detect=0", "detect=", "detect=maybe", ""])
def test_api_hw_non_truthy_detect_does_not_probe(client, monkeypatch, q):
    def boom():
        raise AssertionError("不该被调用：非真值不该触发探测")

    monkeypatch.setattr(ce, "detect_hardware", boom)
    r = client.get("/api/hw" + ("?" + q if q else ""))
    assert r.status_code == 200
    assert "detected" not in r.get_json()
