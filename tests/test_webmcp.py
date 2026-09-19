"""webmcp 的单测：覆盖纯逻辑工具（不依赖 mcp SDK，可在任意解释器跑）。

被测对象 `webmcp.py` 刻意不 import `agent`/重依赖，因此这里直接调用各 handler。
需要真实执行的部分（enhance/quality_probe/generate_metadata）走「缺失输入 / 离线」
分支，验证它们**不会崩**、返回结构稳定。
"""
from __future__ import annotations

import json

import webmcp as wm

EXPECTED_TOOLS = {
    "list_comfy_models", "quality_probe", "enhance_video",
    "generate_clip", "agent_status", "generate_metadata", "get_job_status",
}


def test_tool_registry_complete():
    names = {s["name"] for s in wm.TOOL_SPECS}
    assert names == EXPECTED_TOOLS
    for spec in wm.TOOL_SPECS:
        assert callable(spec["handler"])
        assert spec["description"]


def test_combo_options_empty_and_forms():
    # 空 / 形态异常都安全返回 []
    assert wm._combo_options({}, "X", "y") == []
    assert wm._combo_options({"X": {"input": {"required": {"y": "notalist"}}}}, "X", "y") == []
    # 形态 A：['COMBO', {'options': [...]}]
    a = {"X": {"input": {"required": {"y": ["COMBO", {"options": ["m.pth", "n.pth"]}]}}}}
    assert wm._combo_options(a, "X", "y") == ["m.pth", "n.pth"]
    # 形态 B：[['a.pth'], {'default': 'a.pth'}]
    b = {"X": {"input": {"required": {"y": [["a.pth", "b.pth"], {"default": "a.pth"}]}}}}
    assert wm._combo_options(b, "X", "y") == ["a.pth", "b.pth"]


def test_list_comfy_models_offline():
    # 连一个不可能在听的端口，应快速返回 ok=False 且不抛异常
    res = wm.list_comfy_models(api="http://127.0.0.1:9/")
    assert res["ok"] is False
    assert "sr_models" in res and "rife_ckpts" in res
    assert res["sr_models"] == [] and res["rife_ckpts"] == []


def test_quality_probe_missing_files():
    res = wm.quality_probe("__no_such_ref__.mp4", "__no_such_dist__.mp4", mode="vmaf")
    assert isinstance(res, dict)
    assert res["ok"] is False
    assert "note" in res


def test_quality_probe_bad_mode_json_shape():
    # 即使比对自身不存在的文件，返回也应带 mode/score 字段（measure 的产物）
    res = wm.quality_probe("missing_a.mp4", "missing_b.mp4", mode="psnr")
    assert res.get("mode") == "psnr"
    assert res.get("score") is None


def test_enhance_video_missing_src():
    res = wm.enhance_video("__no_such__.mp4", kind="anime")
    assert res["ok"] is False
    assert "不存在" in res["note"]


def test_generate_clip_bad_engine():
    res = wm.generate_clip("foobar", "一个赛博都市远景")
    assert res["ok"] is False
    assert "mmh3" in res["note"]


def test_agent_status_missing_state():
    res = wm.agent_status(workdir="__no_such_workdir__")
    assert res["ok"] is False
    assert "未找到" in res["note"]


def test_generate_metadata_returns_dict():
    # 离线规则法：即便 series_script 缺失也只返回 ok=False 的 dict，不崩
    res = wm.generate_metadata(ep=999, no_llm=True)
    assert isinstance(res, dict)
    assert "ok" in res


def test_as_text_tool_returns_json_string():
    wrapped = wm._as_text_tool(wm.list_comfy_models)
    out = wrapped(api="http://127.0.0.1:9/")
    assert isinstance(out, str)
    parsed = json.loads(out)  # 必须是合法 JSON（MCP 工具返回文本）
    assert parsed["ok"] is False


def test_build_server_registers_tools():
    # 仅在装了 mcp 的解释器上验证装配不报错（agent venv 没装 mcp 会被 skip）
    pytest_importorskip_mcp()
    server = wm.build_server()
    assert server is not None
    # FastMCP 内部工具管理器应含全部注册工具
    names = set(server._tool_manager._tools.keys()) if hasattr(server, "_tool_manager") else None
    if names is not None:
        assert EXPECTED_TOOLS.issubset(names)


def pytest_importorskip_mcp():
    import pytest
    return pytest.importorskip("mcp")
