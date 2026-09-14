"""引擎统一契约（VideoEngine / CAPABILITIES / 配置段别名）与白模对齐的回归测试。

覆盖三类历史坑：

1. **反射探测签名**（P1-⑤）：agent.py 原先用 `inspect.signature` 猜引擎支不支持
   ref_video / control_video —— 补全与静态分析失效，新增能力要改调用点。
   现在改成引擎显式声明 `CAPABILITIES`，本文件锁住"声明与签名一致"。
2. **配置段别名不一致**（P2-⑪）：`comfyui_mmH3` / `minimax_h3` / `comfyui_h3`
   三个名字散在各模块各写一遍，blocking.py 只认第一个，别名配置下白模控制视频
   帧数会静默错位。现在统一走 pick_engine_section。
3. **17n+5 吸附重复实现**（P2-⑩）：blocking 与引擎各写一遍，漂移会让控制视频与
   出片帧数错位、FunControlApply 静默截断走位。现在统一调 MMH3Engine.snap_length。
"""
from __future__ import annotations

import inspect
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---------------------------------------------------------------- 统一签名
ENGINE_PATHS = {
    "SkyReelsEngine": "agent.engine",
    "LTXEngine": "agent.ltx_engine",
    "MMH3Engine": "agent.mmh3_engine",
    "SolH3Engine": "agent.sol_h3_engine",
}


def _load(name: str, module: str):
    import importlib
    return getattr(importlib.import_module(module), name)


def test_all_engines_share_one_generate_signature():
    """"各引擎接口一致"必须是机器可校验的，不能只靠注释。"""
    from agent.video_engine import VideoEngine
    base = inspect.signature(VideoEngine.generate)
    for name, module in ENGINE_PATHS.items():
        cls = _load(name, module)
        assert issubclass(cls, VideoEngine), f"{name} 未接入 VideoEngine 契约"
        got = inspect.signature(cls.generate)
        assert list(got.parameters) == list(base.parameters), (
            f"{name}.generate 签名与契约不一致：{list(got.parameters)}")


def test_engines_declare_capabilities_they_actually_accept():
    """能力名必须都能作为关键字传进 generate，否则声明就是假的。"""
    for name, module in ENGINE_PATHS.items():
        cls = _load(name, module)
        params = inspect.signature(cls.generate).parameters
        for cap in cls.CAPABILITIES:
            assert cap in params, f"{name} 声明支持 {cap} 但签名里没有"


def test_capability_matrix_is_as_documented():
    from agent.engine import SkyReelsEngine
    from agent.ltx_engine import LTXEngine
    from agent.mmh3_engine import MMH3Engine
    from agent.sol_h3_engine import SolH3Engine
    full = {"ref_images", "ref_video", "control_video", "fc_strength"}
    assert MMH3Engine.CAPABILITIES == full
    assert SolH3Engine.CAPABILITIES == {"ref_images", "ref_video"}
    assert LTXEngine.CAPABILITIES == frozenset()
    assert SkyReelsEngine.CAPABILITIES == frozenset()


# ---------------------------------------------------------------- 能力过滤
def test_filter_engine_kwargs_splits_by_capability():
    from agent.mmh3_engine import MMH3Engine
    from agent.sol_h3_engine import SolH3Engine
    from agent.video_engine import filter_engine_kwargs
    got = {"ref_images": ["a.png"], "ref_video": "r.mp4",
           "control_video": "fc.mp4", "fc_strength": 1.2}
    keep, drop = filter_engine_kwargs(MMH3Engine.__new__(MMH3Engine), **got)
    assert keep == got and drop == {}
    # Sol-H3 无 Fun Control 能力：必须报成「被忽略」，不能假装生效
    keep2, drop2 = filter_engine_kwargs(SolH3Engine.__new__(SolH3Engine), **got)
    assert set(keep2) == {"ref_images", "ref_video"}
    assert set(drop2) == {"control_video", "fc_strength"}


def test_filter_engine_kwargs_drops_none():
    from agent.mmh3_engine import MMH3Engine
    from agent.video_engine import filter_engine_kwargs
    keep, drop = filter_engine_kwargs(
        MMH3Engine.__new__(MMH3Engine),
        ref_images=None, ref_video=None, control_video=None, fc_strength=None)
    assert keep == {} and drop == {}


def test_filter_engine_kwargs_is_conservative_for_unknown_engines():
    """未声明 CAPABILITIES 的引擎：不传任何条件，也不会假装条件已生效。"""
    from agent.video_engine import filter_engine_kwargs

    class Legacy:
        def generate(self, prompt, out_path):
            return out_path

    keep, drop = filter_engine_kwargs(Legacy(), control_video="fc.mp4")
    assert keep == {} and drop == {"control_video": "fc.mp4"}


def test_warn_ignored_logs_and_returns_drop_list(capsys):
    """静默丢弃是最坏选项：必须留下一条日志。"""
    from agent.ltx_engine import LTXEngine
    eng = LTXEngine.__new__(LTXEngine)
    dropped = eng._warn_ignored(ref_images=["a.png"], ref_video=None)
    assert dropped == {"ref_images": ["a.png"]}
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "[ltx]" in text and "ref_images" in text


# ---------------------------------------------------------------- 配置段别名
def test_pick_engine_section_prefers_first_alias():
    from agent.video_engine import H3_SECTION_ALIASES, pick_engine_section
    cfg = {"engine": {"comfyui_mmH3": {"num_frames": 56},
                      "minimax_h3": {"num_frames": 39}}}
    assert pick_engine_section(cfg, *H3_SECTION_ALIASES)["num_frames"] == 56
    # 只留旧别名 → 仍然能读到（旧行为：or 链）
    assert pick_engine_section(
        {"engine": {"minimax_h3": {"num_frames": 39}}},
        *H3_SECTION_ALIASES)["num_frames"] == 39


def test_pick_engine_section_treats_empty_as_absent():
    """空段视为未配置（与旧 `a or b or c` 一致），否则会把有内容的别名挡掉。"""
    from agent.video_engine import H3_SECTION_ALIASES, pick_engine_section
    cfg = {"engine": {"comfyui_mmH3": {}, "minimax_h3": {"num_frames": 39}}}
    assert pick_engine_section(cfg, *H3_SECTION_ALIASES)["num_frames"] == 39
    assert pick_engine_section({}, *H3_SECTION_ALIASES) == {}
    assert pick_engine_section({"engine": None}, *H3_SECTION_ALIASES) == {}


def test_mmh3_reads_alias_sections():
    from agent.mmh3_engine import MMH3Engine
    for section in ("comfyui_mmH3", "minimax_h3", "comfyui_h3"):
        eng = MMH3Engine({"engine": {section: {"num_frames": 39}}})
        assert eng.num_frames == 39, section


# ---------------------------------------------------------------- 白模帧数/分辨率对齐
def _fake_blocking(config: dict):
    """BlockingGenerator 的 __init__ 只要 config 就能跑（BlenderMCP 是惰性连接），
    但这里仍用轻量 stub 直接调未绑定方法，避免无谓依赖。"""
    return types.SimpleNamespace(config=config)


def test_blocking_fc_frames_matches_engine_snap_length():
    """两处 17n+5 必须完全一致（原先各写一遍，漂移会导致走位被静默截断）。"""
    from agent.blocking import BlockingGenerator
    from agent.mmh3_engine import MMH3Engine
    stub = _fake_blocking({"engine": {"comfyui_mmH3": {"num_frames": 56}}})
    for n in (1, 5, 6, 21, 22, 23, 38, 39, 100, 123, 200):
        stub.config = {"engine": {"comfyui_mmH3": {"num_frames": n}}}
        got = BlockingGenerator._resolve_fc_frames(stub)
        assert got == MMH3Engine.snap_length(n), n
        assert got >= n                      # 只会向上吸附
        assert (got - 5) % 17 == 0


def test_blocking_fc_frames_honors_config_aliases():
    """别名配置下也必须读到真实出片帧数（原先只认 comfyui_mmH3）。"""
    from agent.blocking import BlockingGenerator
    stub = _fake_blocking({"engine": {"minimax_h3": {"num_frames": 39}}})
    assert BlockingGenerator._resolve_fc_frames(stub) == 39
    stub.config = {"engine": {"comfyui_h3": {"num_frames": 73}}}
    assert BlockingGenerator._resolve_fc_frames(stub) == 73


def test_blocking_fc_size_uses_engine_snap_resolution():
    """控制视频必须与出片同分辨率：出片会被吸附到 32 倍数，这里也要同样吸附。"""
    from agent.blocking import BlockingGenerator
    from agent.mmh3_engine import MMH3Engine
    stub = _fake_blocking({"engine": {"comfyui_mmH3": {"resolution": "810x448"}}})
    got = BlockingGenerator._resolve_fc_size(stub)
    assert got == (800, 448)                       # 810 -> 800（向下取 32 倍数）
    assert MMH3Engine.snap_resolution("810x448") == "800x448"
    stub.config = {"engine": {"minimax_h3": {"resolution": "768x448"}}}
    assert BlockingGenerator._resolve_fc_size(stub) == (768, 448)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
