"""白模 4-pass 渲染的引擎分工（P2-⑨）。

背景（真机 Blender 5.2.1 实测，详见 agent/blocking.py 的 _ctrl_setup / _line_setup）：
- 原模板只在 line pass 之前设一次 `engine="CYCLES"`，紧随其后的 depth / normal
  没有任何回切，于是两个纯 emission 材质的控制图也白跑了 CYCLES（每镜约 0.63s ×2）。
- line pass 必须保留能出 freestyle 的引擎：EEVEE 在 5.2 也能出线（掩膜 IoU 72.9%），
  但默认取 CYCLES 以逐像素对齐历史基线；Workbench 会忽略材质节点，直接排除。

这里锁定**生成的 bpy 源码结构**：引擎切换必须出现在正确的段落、且顺序正确。
真实产物正确性由真机验证脚本确认（见 commit 说明里的逐像素比对数据）。
"""
import json
import os

import pytest

TMP = os.path.abspath("outputs/_test_blocking_engine")
SPEC = {"characters": 1, "props": [], "shot": "medium",
        "camera": "static", "height": "eye"}


def _gen(**blender_cfg):
    from agent.blocking import BlockingGenerator
    cfg = {"blender": {"enabled": True, "engine": "eevee", "samples": 16,
                       "reuse_scene": False, **blender_cfg}}
    bg = BlockingGenerator(cfg, TMP)
    code = bg._build_block_code(SPEC, TMP, "block", "eevee")
    return bg, code


def _segments(code):
    """按 4 个输出路径把代码切成 [头部, previs→line, line→depth, depth→normal]。

    定位必须用 `scn.render.filepath=<json 路径>`：模板注释里也出现了 "line.png"
    这类字面量，直接按文件名找会把分段切错。
    """
    idx = []
    for n in ("previs", "line", "depth", "normal"):
        key = "scn.render.filepath=" + json.dumps(os.path.join(TMP, f"{n}.png"))
        assert key in code, f"模板未包含输出语句 {key}"
        idx.append(code.index(key))
    assert idx == sorted(idx), "4 个输出路径顺序异常（previs→line→depth→normal）"
    return [code[:idx[0]], code[idx[0]:idx[1]],
            code[idx[1]:idx[2]], code[idx[2]:idx[3]]]


CYC_TOP = 'try:\n    scn.render.engine="CYCLES"\n    scn.cycles.samples='
EE_TOP = 'try:\n    scn.render.engine="BLENDER_EEVEE"\n'


def test_line_pass_defaults_to_cycles():
    """默认 line 用 CYCLES：与历史基线逐像素一致（freestyle 出线是硬需求）。"""
    _, code = _gen()
    seg_previs, seg_line, _, _ = _segments(code)
    assert CYC_TOP in seg_line, "line pass 未切到 CYCLES（freestyle 会失效）"
    assert EE_TOP not in seg_line, "line pass 不该用 EEVEE（除非显式配置）"


def test_depth_normal_switch_back_to_fast_engine():
    """核心修复：depth/normal 段落必须出现回切快速引擎，否则静默继承 CYCLES。"""
    _, code = _gen()
    _, _, seg_depth, seg_normal = _segments(code)
    assert EE_TOP in seg_depth, "depth pass 未回切快速引擎（会白跑 CYCLES）"
    assert CYC_TOP not in seg_depth, "depth pass 不该再出现 CYCLES 切换"
    # normal 不开新引擎（沿用 depth 段落设好的），不该再有切换
    assert CYC_TOP not in seg_normal
    assert EE_TOP not in seg_normal


def test_fast_control_passes_off_restores_legacy_behavior():
    """关掉开关 = 完全回到旧行为（depth/normal 继承 CYCLES）。"""
    _, code = _gen(fast_control_passes=False)
    _, _, seg_depth, _ = _segments(code)
    assert EE_TOP not in seg_depth, "开关关闭后仍插入了快速引擎切换"
    assert CYC_TOP not in seg_depth, "开关关闭后不该额外插 CYCLES 切换"


def test_line_engine_eevee_removes_all_cycles_switches():
    """line_engine=eevee：整段不该再有任何 CYCLES 切换（含 depth 段落）。"""
    bg, code = _gen(line_engine="eevee")
    assert bg.line_engine == "eevee"
    _, seg_line, seg_depth, _ = _segments(code)
    assert EE_TOP in seg_line, "line_engine=eevee 时 line pass 未用 EEVEE"
    assert CYC_TOP not in code, "line_engine=eevee 时不该再出现 CYCLES 顶层切换"
    assert EE_TOP in seg_depth


def test_line_engine_invalid_falls_back_to_cycles():
    bg, code = _gen(line_engine="workbench")
    assert bg.line_engine == "cycles", "非法 line_engine 未回退 cycles"
    _, seg_line, _, _ = _segments(code)
    assert CYC_TOP in seg_line


def test_workbench_never_selected_by_accident():
    """Workbench 会忽略材质节点（depth/normal 材质法失效），任何配置下都不该被选中。"""
    for cfg in ({}, {"line_engine": "eevee"}, {"fast_control_passes": False}):
        _, code = _gen(**cfg)
        assert "BLENDER_WORKBENCH" not in code


def test_no_leftover_format_placeholders():
    """模板占位符必须全部填充（漏填会以 {XXX} 字面量送进 Blender 直接 SyntaxError）。"""
    _, code = _gen()
    for ph in ("{LINE_SETUP}", "{CTRL_SETUP}", "{ENGINE}", "{PREVIS}", "{LINE}",
               "{DEPTH}", "{NORMAL}", "{LS}"):
        assert ph not in code, f"占位符 {ph} 未被替换"
    compile(code, "<blocking>", "exec")           # 生成的必须是合法 Python


def test_ctrl_setup_empty_when_disabled():
    bg, _ = _gen(fast_control_passes=False)
    assert bg._ctrl_setup() == ""


def test_ctrl_and_line_setup_are_valid_python_fragments():
    """片段本身要能被 exec（缩进/换行错位会让整段注入代码崩）。"""
    for cfg in ({}, {"line_engine": "eevee"}):
        bg, _ = _gen(**cfg)
        for frag in (bg._ctrl_setup(), bg._line_setup()):
            if frag:
                compile("scn = None\n" + frag, "<frag>", "exec")


def test_samples_clamped_for_control_passes():
    """控制图不需要高采样：片段里的采样数应被夹到 <=16，避免误配拖慢。"""
    bg, code = _gen(samples=256)
    ctrl = bg._ctrl_setup()
    line = bg._line_setup()
    assert "taa_render_samples=16" in ctrl
    assert "samples=16" in line
    del code


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
