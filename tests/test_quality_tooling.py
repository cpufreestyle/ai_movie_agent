"""画质工具链行为锁：模型清单选择 + 客观度量（全部离线，不依赖 ComfyUI/GPU）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import comfy_models as cm  # noqa: E402
import quality_probe as qp  # noqa: E402


# ---------- ComfyUI COMBO 两种形态 ----------
def test_combo_form_a_options_dict():
    info = {"N": {"input": {"required": {"m": ["COMBO", {"options": ["a.pth"]}]}}}}
    assert cm._combo_options(info, "N", "m") == ["a.pth"]


def test_combo_form_b_plain_list():
    """部分插件（如 RIFE VFI）直接给 [[...], {...}]，只认形态 A 会静默返回空。"""
    info = {"RIFE VFI": {"input": {"required": {"ckpt_name": [["a.pth", "b.pth"],
                                                              {"default": "a.pth"}]}}}}
    assert cm._combo_options(info, "RIFE VFI", "ckpt_name") == ["a.pth", "b.pth"]


def test_combo_missing_returns_empty():
    assert cm._combo_options({}, "N", "m") == []


# ---------- RIFE 版本排序 ----------
def test_rife_rank_ordering():
    assert cm._rife_rank("rife49.pth") < cm._rife_rank("rife417.pth") < cm._rife_rank("rife426.pth")


def test_rife_rank_nonstandard_zero():
    assert cm._rife_rank("sudo_rife4_269_testV1_scale1.pth") == 0


def test_pick_rife_prefers_newest():
    avail = ["rife47.pth", "rife49.pth", "rife417.pth", "rife426.pth"]
    ckpt, note = cm.pick_rife_ckpt(available=avail)
    assert ckpt == "rife426.pth" and "更新版" in note


def test_pick_rife_explicit_wins():
    assert cm.pick_rife_ckpt(explicit="rife47.pth", available=["rife49.pth"]) == ("rife47.pth", "")


def test_pick_rife_offline_falls_back():
    ckpt, note = cm.pick_rife_ckpt(available=[])
    assert ckpt == cm.DEFAULT_RIFE and note == ""


# ---------- 超分模型回退（不再「纸面模型」） ----------
def test_pick_sr_falls_back_when_missing():
    """动漫推荐模型本机没有时，必须回退到真实存在的，而不是提交一个不存在的权重。"""
    model, note = cm.pick_sr_model("anime", available=["4x-UltraSharp.pth"])
    assert model == "4x-UltraSharp.pth" and "缺失" in note


def test_pick_sr_keeps_wanted_when_present():
    model, note = cm.pick_sr_model("anime", available=["RealESRGAN_x4plus_anime_6B.pth"])
    assert model == "RealESRGAN_x4plus_anime_6B.pth" and note == ""


def test_pick_sr_explicit_wins_over_kind():
    model, _ = cm.pick_sr_model("anime", explicit="x.pth", available=["x.pth", "y.pth"])
    assert model == "x.pth"


def test_pick_sr_no_list_keeps_plan():
    """查不到清单（ComfyUI 离线）时不该乱改，也不该谎报。"""
    model, note = cm.pick_sr_model("anime", available=[])
    assert model and note == ""


# ---------- 客观度量 ----------
def test_as_float_inf():
    assert qp._as_float("inf") == float("inf")
    assert qp._as_float("42.5") == 42.5


def test_verdict_thresholds():
    assert qp.verdict("vmaf", 98) == "接近无损"
    assert qp.verdict("vmaf", 95) == "良好"
    assert qp.verdict("vmaf", 85) == "可接受"
    assert qp.verdict("vmaf", 60) == "明显劣化"
    assert qp.verdict("psnr", 45) == "很好"
    assert qp.verdict("ssim", 0.99) == "很好"


def test_verdict_inf_and_none():
    assert qp.verdict("psnr", float("inf")) == "完全相同（无损）"
    assert qp.verdict("vmaf", None) == "测不出"


def test_filter_targets_right_metric():
    assert "psnr" in qp._filter("psnr", 768, 448)
    assert "ssim" in qp._filter("ssim", 768, 448)
    assert "libvmaf" in qp._filter("vmaf", 768, 448, "log.json")


def test_filter_normalizes_both_sides():
    """两侧必须统一分辨率/像素格式，否则 libvmaf/psnr 直接报错。"""
    f = qp._filter("psnr", 768, 448)
    assert f.count("scale=768:448") == 2 and f.count("yuv420p") == 2


def test_measure_missing_file_is_reported_not_crash():
    res = qp.measure("nope_a.mp4", "nope_b.mp4", "psnr")
    assert res["score"] is None and res["note"]
