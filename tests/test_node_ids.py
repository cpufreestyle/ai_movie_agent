"""节点 ID 分配（NodeAllocator）+ H3 工作流结构黄金样本回归。

两层保护：

1. **单元层** —— NodeAllocator 的名字登记 / 冲突断言 / 区间越界 / 自动分配 / 审计；
2. **黄金层** —— 覆盖 turbo·no_turbo·block_cache·two_pass·post·fun_control·配置别名
   共 7 种配置 × 8 种输入组合（56 个 case）的 `_build_workflow` 输出指纹。

黄金样本记录每个 case 的**节点 ID → class_type 映射**与**整份工作流的 sha256**，
因此任何节点 ID 漂移、依赖重连、参数改动都会被逐字节拦下（ID 变了就等于已出片的
复现性变了）。

样本来源：引入 NodeAllocator **之前**的旧实现 —— 先用同一矩阵跑旧版引擎快照，
再跑重构版逐 case 比对，结果 56/56 逐字节一致；样本即由那份旧快照生成，
所以它同时是「重构无副作用」的证明。

维护：改 `NodeAllocator.PLAN` 或工作流结构后，人工确认变更符合预期，再执行
    python tests/test_node_ids.py --update
重新生成样本。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "mmh3_wf_fingerprint.json")

# 临时目录每次运行都不同 → 归一化后再算指纹，否则样本不可复现
TMP_RE = re.compile(r"golden_[0-9A-Za-z_]{6,}")


def _norm(o):
    if isinstance(o, dict):
        return {k: _norm(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_norm(v) for v in o]
    if isinstance(o, str):
        return TMP_RE.sub("golden_TMP", o)
    return o


# ---------------------------------------------------------------- 黄金样本
def _configs(cv: str) -> dict:
    """配置 × 输入矩阵，与 `_golden_wf.py`（样本来源）逐字对齐。"""
    return {
        "turbo": {"comfyui_mmH3": {}},
        "no_turbo": {"comfyui_mmH3": {"lora": ""}},
        "block_cache": {"comfyui_mmH3": {"block_cache": {"enable": True}}},
        "two_pass": {"comfyui_mmH3": {"two_pass": {"enable": True}}},
        "post": {"comfyui_mmH3": {"post": {"upscale_model": "4x.pth",
                                           "sharpen": 0.5}}},
        "fc_cfg": {"comfyui_mmH3": {"fun_control": {"enable": True, "video": cv,
                                                    "strength": 0.9}}},
        "alias_legacy": {"minimax_h3": {"num_frames": 39}},
    }


def _touch(dirpath: str, name: str, size: int = 256) -> str:
    p = os.path.join(dirpath, name)
    with open(p, "wb") as f:
        f.write(b"x" * size)
    return p


def _inputs(tmp: str) -> dict:
    """输入矩阵（含不存在的 control_video，覆盖「显式传入但不可用」分支）。"""
    img = _touch(tmp, "first.png")
    rv = _touch(tmp, "ref.mp4")
    cv = _touch(tmp, "fc.mp4")
    ri = [_touch(tmp, "r0.png"), _touch(tmp, "r1.png")]
    return {
        "t2va": {},
        "i2va": {"image": img},
        "ref2va_video": {"ref_video": rv},
        "ref2va_images": {"ref_images": ri},
        "hybrid": {"image": img, "ref_images": ri},
        "fc_explicit": {"image": img, "control_video": cv, "fc_strength": 1.15},
        "everything": {"image": img, "ref_video": rv, "ref_images": ri,
                       "control_video": cv, "fc_strength": 0.95},
        "missing_fc": {"control_video": os.path.join(tmp, "nope.mp4")},
    }, cv


def _build(cfg_engine: dict, kwargs: dict) -> dict:
    from agent.mmh3_engine import MMH3Engine
    eng = MMH3Engine({"engine": cfg_engine})
    # 桩：不联网 / 结果确定
    eng.client.upload_image = lambda p: {"name": "up_" + os.path.basename(p)}
    eng.client.is_ready = lambda: True
    # 黄金样本只关心工作流结构，跳过 Fun Control 前置校验
    eng._validate_control_video = lambda *a, **k: None
    kw = dict(kwargs)
    img = kw.pop("image", None)      # image 是位置参数，缺省必须显式给 None
    return eng._build_workflow(prompt="a prompt", seed=1234, image=img, **kw)


def _fingerprint(wf: dict) -> dict:
    nw = _norm(wf)
    canon = json.dumps(nw, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return {
        "sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
        "classes": {k: (v or {}).get("class_type")
                    for k, v in sorted(nw.items(), key=lambda kv: int(kv[0]))},
    }


def _collect() -> dict:
    """跑完整矩阵，返回 {case: 指纹}。"""
    out: dict = {}
    tmp = tempfile.mkdtemp(prefix="golden_")
    ins, cv = _inputs(tmp)
    for cname, engine_cfg in _configs(cv).items():
        for iname, kwargs in ins.items():
            out[f"{cname}__{iname}"] = _fingerprint(_build(engine_cfg, kwargs))
    return out


def test_mmh3_workflow_golden_fingerprint():
    if not os.path.isfile(FIXTURE):
        pytest.skip(f"缺少黄金样本: {FIXTURE}")
    with open(FIXTURE, encoding="utf-8") as f:
        expected = json.load(f)
    got = _collect()
    assert sorted(got) == sorted(expected), (
        f"case 集合不一致：新增 {set(got) - set(expected)}，"
        f"缺失 {set(expected) - set(got)}")
    diffs = [k for k in expected if got[k] != expected[k]]
    assert not diffs, (
        "工作流结构已漂移（节点 ID / 依赖 / 参数变化都会导致已出片不可复现）："
        + "; ".join(
            f"{k}: sha {expected[k]['sha256'][:12]} -> {got[k]['sha256'][:12]}, "
            f"classes_diff="
            f"{ {n: (expected[k]['classes'].get(n), got[k]['classes'].get(n)) for n in set(expected[k]['classes']) | set(got[k]['classes']) if expected[k]['classes'].get(n) != got[k]['classes'].get(n)} }"
            for k in diffs[:5]))


# ---------------------------------------------------------------- 单元层
def test_allocator_plan_matches_legacy_ids():
    """分配器 PLAN 必须与历史工作流分段一致（改这里 = 已出片复现性变化）。"""
    from agent.node_ids import NodeAllocator
    a = NodeAllocator()
    assert a.alloc("unet") == "1"
    assert a.alloc("cond") == "6"
    assert a.alloc("decode") == "11"
    assert a.alloc("ref_video") == "14"
    assert a.alloc("upscale_loader") == "30"
    assert a.alloc("sharpen") == "32"
    assert a.alloc("block_cache") == "40"
    assert a.alloc("fc_apply") == "43"
    assert a.alloc("tp_parity") == "57"
    assert a.alloc_range("ref_image", 0) == "20"
    assert a.alloc_range("ref_image", 8) == "28"


def test_allocator_rejects_duplicate_name():
    from agent.node_ids import NodeAllocator
    a = NodeAllocator()
    a.alloc("unet")
    with pytest.raises(AssertionError, match="重复分配"):
        a.alloc("unet")


def test_allocator_rejects_id_collision():
    """两个名字指向同一 ID → 当场报错（历史事故：ref_images 与后处理撞号）。"""
    from agent.node_ids import NodeAllocator
    a = NodeAllocator(plan={"a": "30", "b": "30"})
    a.alloc("a")
    with pytest.raises(AssertionError, match="ID 冲突"):
        a.alloc("b")


def test_allocator_range_bounds():
    from agent.node_ids import NodeAllocator
    a = NodeAllocator()
    with pytest.raises(AssertionError, match="越界"):
        a.alloc_range("ref_image", 9)      # 容量 9 → 合法索引 0..8
    with pytest.raises(AssertionError, match="越界"):
        a.alloc_range("ref_image", -1)
    with pytest.raises(KeyError, match="未定义 ID 区间"):
        a.alloc_range("nope", 0)


def test_allocator_auto_skips_reserved():
    """PLAN 之外的语义名自动取空闲号，且不得占用规划 / 预留区间的号。"""
    from agent.node_ids import NodeAllocator
    a = NodeAllocator()
    nid = a.alloc("brand_new_node")
    assert nid not in NodeAllocator.PLAN.values()
    assert not (20 <= int(nid) <= 28)
    assert a.alloc("another_new_node") != nid


def test_allocator_audit_catches_unregistered_and_unused():
    from agent.node_ids import NodeAllocator
    a = NodeAllocator()
    nid = a.alloc("unet")
    # 硬编码塞一个节点 → 必须被审计拦下
    with pytest.raises(AssertionError, match="未经分配器登记"):
        a.audit({nid: {"class_type": "UNETLoader"}, "999": {"class_type": "X"}})
    # 分配了却没用上 → 也要报（分配与写入不成对）
    with pytest.raises(AssertionError, match="未出现在工作流"):
        a.audit({})


def _update_fixture() -> None:
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    data = _collect()
    with open(FIXTURE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"cases={len(data)} -> {FIXTURE}")


if __name__ == "__main__":
    if "--update" in sys.argv:
        _update_fixture()
    else:
        print(__doc__)
