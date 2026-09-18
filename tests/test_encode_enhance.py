"""画质增强：编码档位 + 超分模型推荐 + run_series 接入的行为锁。

这些是「开源参照落地」的收口：编码参数必须集中在一处且带 faststart，
动漫内容必须走动漫 tune / 动漫超分模型，增强失败不得毁掉已出的成片。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import encode as enc  # noqa: E402


def test_profile_list_stable():
    assert set(enc.profiles()) == {"draft", "standard", "high", "anime"}


def test_unknown_profile_falls_back():
    """配置写错档位名不能让管线崩，回退 standard。"""
    assert enc.resolve("nope") == enc.resolve("standard")
    assert enc.resolve("") == enc.resolve("standard")


def test_quality_args_have_faststart_and_aac():
    args = enc.quality_args("standard", fps=24)
    assert "-movflags" in args and "+faststart" in args
    assert "-pix_fmt" in args and "yuv420p" in args
    assert "-c:a" in args and "aac" in args


def test_anime_profile_uses_animation_tune():
    args = enc.quality_args("anime")
    i = args.index("-tune")
    assert args[i + 1] == "animation"


def test_draft_has_no_tune():
    """draft 不设 tune（快速预览不需要，也避免误伤真人素材）。"""
    assert "-tune" not in enc.quality_args("draft")


def test_gop_derived_from_fps():
    args = enc.quality_args("standard", fps=24)
    assert args[args.index("-g") + 1] == "48"
    assert "-g" not in enc.quality_args("standard", fps=0)


def test_level_widens_for_4k():
    """1080p 用 4.2（兼容性），超过 2160 才放宽到 5.1。"""
    def lvl(w, h):
        a = enc.quality_args("standard", width=w, height=h)
        return a[a.index("-level:v") + 1]

    assert lvl(1920, 1080) == "4.2"
    assert lvl(768, 448) == "4.2"
    assert lvl(3840, 2160) == "5.1"


def test_sr_model_pick():
    assert "anime" in enc.sr_model_for("anime").lower()
    assert enc.sr_model_for("real") == enc.SR_MODELS["real"][0]
    assert enc.sr_model_for("anime", "custom.pth") == "custom.pth"


def test_build_encode_cmd_denoise():
    cmd = enc.build_encode_cmd("ff", "in.mp4", "out.mp4", "anime",
                               fps=24, denoise=True)
    assert "-vf" in cmd and "hqdn3d" in cmd[cmd.index("-vf") + 1]
    assert cmd[0] == "ff" and cmd[-1] == "out.mp4"


def test_enhance_disabled_returns_original():
    """--enhance 未开启时 _enhance_film 必须原样返回，绝不能动成片。"""
    import importlib

    rs = importlib.import_module("run_series")
    rs.ENHANCE_PROFILE = ""
    assert rs._enhance_film("outputs/ep1.mp4") == "outputs/ep1.mp4"


def test_enhance_cli_registered():
    from cli import COMMANDS
    assert "enhance" in COMMANDS
