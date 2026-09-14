"""核心纯函数回归（P1-⑧）：帧数/分辨率吸附、任务类型判定、场景签名。

这三处是最容易静默回归的地方：

* `snap_length` / `snap_resolution`：H3 条件节点对长度的硬约束（17n+5）与分辨率
  32 整除。错了不是报错，而是画面错位/尺寸不符 —— 甚至要等 ComfyUI 端才发现。
* `resolve_task`：Hybrid / Ref2VA / I2VA / T2VA 四选一。判错会直接抛错
  （I2VA 禁止携带参考媒体），或让白模参考白喂。
* `_scene_sig`：Blender IDProperty 是 C int(32 位有符号)，超范围会 OverflowError
  （真机实测踩到过），所以必须恒定落在 31 位内且跨进程稳定。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.mmh3_engine import MMH3Engine  # noqa: E402

LEN_BASE, LEN_STEP = MMH3Engine.LEN_BASE, MMH3Engine.LEN_STEP


# ---------------------------------------------------------------- 帧数吸附
def test_snap_length_is_upward_on_grid():
    """合法值原样返回；非法值向上吸附到最近网格点（绝不向下，否则时长变短）。"""
    assert MMH3Engine.snap_length(5) == 5
    assert MMH3Engine.snap_length(22) == 22
    assert MMH3Engine.snap_length(39) == 39
    assert MMH3Engine.snap_length(56) == 56
    assert MMH3Engine.snap_length(1) == 5          # 下限 LEN_BASE
    assert MMH3Engine.snap_length(0) == 5
    assert MMH3Engine.snap_length(-10) == 5
    assert MMH3Engine.snap_length(6) == 22
    assert MMH3Engine.snap_length(21) == 22
    assert MMH3Engine.snap_length(23) == 39
    assert MMH3Engine.snap_length(57) == 73


def test_snap_length_invariants():
    """对一大段输入：必落 17n+5 网格、单调不减、且 ≥ 输入。"""
    prev = 0
    for n in range(1, 400):
        got = MMH3Engine.snap_length(n)
        assert (got - LEN_BASE) % LEN_STEP == 0, n
        assert got >= max(n, LEN_BASE), n
        assert got >= prev, n
        prev = got


def test_snap_length_accepts_numeric_string():
    """配置里写成字符串也要能用（config 是 JSON/YAML，人写 `"num_frames": "56"`）。"""
    assert MMH3Engine.snap_length("56") == 56
    assert MMH3Engine.snap_length("57") == 73


# ---------------------------------------------------------------- 分辨率吸附
@pytest.mark.parametrize("res,expect", [
    ("768x448", "768x448"),
    ("1024x576", "1024x576"),
    ("810x448", "800x448"),        # 810 → 800（向下取 32 倍数）
    ("1000x1000", "992x992"),
    ("33x33", "32x32"),
    ("31x31", "32x32"),            # 下限 32
    ("0x0", "32x32"),
    ("-64x-64", "32x32"),
    ("garbage", "32x32"),          # 解析失败不崩
    ("", "32x32"),
    ("768X448", "768x448"),        # 大写 X
    (" 768x448 ", "768x448"),
])
def test_snap_resolution(res, expect):
    assert MMH3Engine.snap_resolution(res) == expect


def test_snap_resolution_divisible_by_align():
    for w in (32, 100, 448, 810, 1024, 4096):
        for h in (32, 111, 448, 999):
            got = MMH3Engine.snap_resolution(f"{w}x{h}")
            gw, gh = (int(x) for x in got.split("x"))
            assert gw % MMH3Engine.ALIGN == 0 and gh % MMH3Engine.ALIGN == 0
            assert gw >= MMH3Engine.ALIGN and gh >= MMH3Engine.ALIGN
            assert gw <= w and gh <= h          # 只向下吸附（不超过用户请求）


# ---------------------------------------------------------------- 任务类型
@pytest.mark.parametrize("has_img,has_ref,expect", [
    (False, False, "T2VA"),
    (True, False, "I2VA"),
    (False, True, "Ref2VA"),
    (True, True, "Hybrid"),
])
def test_resolve_task_matrix(has_img, has_ref, expect):
    assert MMH3Engine.resolve_task(has_img, has_ref) == expect


def test_build_workflow_task_type_matches_inputs(tmp_path):
    """工作流里写进 conditioning 的 task_type 必须与输入一致。"""
    img = tmp_path / "first.png"
    img.write_bytes(b"x" * 16)
    rv = tmp_path / "ref.mp4"
    rv.write_bytes(b"x" * 16)
    eng = MMH3Engine({"engine": {"comfyui_mmH3": {}}})
    eng.client.upload_image = lambda p: {"name": "up.png"}

    def task_of(**kw):
        wf = eng._build_workflow("p", 1, kw.pop("image", None), **kw)
        cond = wf[[k for k, v in wf.items()
                   if v.get("class_type") == "MiniMaxH3AudioConditioningT8"][0]]
        return cond["inputs"]["task_type"]

    assert task_of() == "T2VA"
    assert task_of(image=str(img)) == "I2VA"
    assert task_of(ref_video=str(rv)) == "Ref2VA"
    assert task_of(image=str(img), ref_video=str(rv)) == "Hybrid"


def test_ref_images_counts_as_reference_media(tmp_path):
    """只给首帧 + 多参考图也必须判 Hybrid：I2VA 禁止携带参考媒体，会直接抛错。"""
    img = tmp_path / "first.png"
    img.write_bytes(b"x" * 16)
    ri = tmp_path / "r0.png"
    ri.write_bytes(b"x" * 16)
    eng = MMH3Engine({"engine": {"comfyui_mmH3": {}}})
    eng.client.upload_image = lambda p: {"name": "up.png"}
    wf = eng._build_workflow("p", 1, str(img), ref_images=[str(ri)])
    cond = wf[[k for k, v in wf.items()
               if v.get("class_type") == "MiniMaxH3AudioConditioningT8"][0]]
    assert cond["inputs"]["task_type"] == "Hybrid"


# ---------------------------------------------------------------- 场景签名
def test_scene_sig_stable_and_in_c_int_range():
    """必须落在 C int(32 位有符号) 内，且跨调用稳定（同一集多次调用要能命中复用）。"""
    from agent.blocking import BlockingGenerator
    specs = [{"characters": 1, "props": []},
             {"characters": 3, "props": ["桌", "椅"]},
             {"characters": 6, "props": ["x" * 80]}]
    for spec in specs:
        sig = BlockingGenerator._scene_sig(spec)
        assert isinstance(sig, int) and 0 <= sig <= 0x7FFFFFFF
        assert BlockingGenerator._scene_sig(dict(spec)) == sig


def test_scene_sig_distinguishes_geometry():
    from agent.blocking import BlockingGenerator
    a = BlockingGenerator._scene_sig({"characters": 1, "props": []})
    b = BlockingGenerator._scene_sig({"characters": 2, "props": []})
    c = BlockingGenerator._scene_sig({"characters": 1, "props": ["桌"]})
    assert len({a, b, c}) == 3
    # props 的**顺序**参与签名（json.dumps 只排键不排列表）：换个书写顺序 =
    # 换一个签名 = 重建场景。当前行为如此，属已知取舍（重建不影响正确性）。
    d = BlockingGenerator._scene_sig({"characters": 1, "props": ["桌", "椅"]})
    e = BlockingGenerator._scene_sig({"characters": 1, "props": ["椅", "桌"]})
    assert d != e


def test_scene_sig_tolerates_missing_or_bad_fields():
    from agent.blocking import BlockingGenerator
    assert isinstance(BlockingGenerator._scene_sig({}), int)
    assert isinstance(BlockingGenerator._scene_sig({"characters": 0}), int)
    assert (BlockingGenerator._scene_sig({"characters": 0})
            == BlockingGenerator._scene_sig({"characters": 1}))
    assert isinstance(BlockingGenerator._scene_sig({"props": None}), int)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
