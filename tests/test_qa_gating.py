"""P2-⑬：MovieAgent 出片链路的质检（QA）与投稿前体检（preflight）门控。

两条链路的关键约束都是「默认行为保持不变」：
- QA 用独立开关 `qa.agent_enabled`（默认关）。刻意不复用 `qa.enabled` ——
  那份是 run_series 批量出片的历史默认值（config.yaml 里就是 true），
  直接复用它会让 WebUI/cli run 的逐镜链路凭空多出打分开销与换 seed 重 roll。
- preflight 的拦截只在「配置开启自动投稿」时生效，且可关。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CFG = {
    "project": {"title": "测试片", "theme": "a test theme", "scene_frames": 97},
    "engine": {"fps": 24, "skyreels_repo": "./skyreels_v2"},
    "llm": {"disabled": True},
    "publish": {"enabled": False, "binary": "definitely_not_exist_biliup_xyz"},
    "collector": {"enabled": True, "method": "requests", "urls": []},
    "planner": {"enabled": True},
    "image_prompt": {"enabled": False},
    "blender": {"enabled": False},
}


class _Eng:
    """最小引擎桩：只记录 seed 并写出一个占位文件。"""

    two_pass = False
    CAPABILITIES = frozenset()

    def __init__(self):
        self.seeds: list = []

    def is_ready(self):
        return True

    def generate(self, prompt, out_path, prev_clip=None, seed=None, image=None,
                 two_pass=None, **kwargs):
        self.seeds.append(seed)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("stub")
        return out_path


def _agent(cfg_extra: dict):
    from agent.agent import MovieAgent
    work = tempfile.mkdtemp(prefix="qa_gate_")
    agent = MovieAgent({**CFG, **cfg_extra}, work)
    agent.engine = _Eng()
    return agent, work


# ------------------------------------------------------------ qa.write_report
def test_qa_write_report_shape_and_empty(tmp_path):
    from agent import qa
    assert qa.write_report(str(tmp_path), []) == ""        # 没内容就不落盘
    entries = [
        {"shot": "scene_001", "ok": True, "sharpness": 30.0, "motion": 2.0},
        {"shot": "scene_002", "ok": False, "sharpness": 1.0, "motion": 0.1,
         "reasons": ["画面偏糊"]},
    ]
    path = qa.write_report(str(tmp_path), entries)
    assert path.endswith("qa_report.json")
    data = json.load(open(path, encoding="utf-8"))
    assert data["total"] == 2 and data["failed"] == 1
    assert "summary" in data and "generated_at" in data
    assert [s["shot"] for s in data["shots"]] == ["scene_001", "scene_002"]


def test_run_series_report_delegates_to_qa_writer(tmp_path):
    """批量链路与逐镜链路必须写出同一种报告，否则没法放一起比对。"""
    import run_series
    from agent import qa
    entries = [{"shot": "ep1_shot1", "ok": True, "sharpness": 12.0}]
    old = run_series.WORK
    run_series.WORK = str(tmp_path)
    try:
        run_series._write_qa_report(entries)
    finally:
        run_series.WORK = old
    assert os.path.exists(tmp_path / "qa_report.json")
    assert json.load(open(tmp_path / "qa_report.json", encoding="utf-8"))["total"] == 1
    # 空列表：什么都不写（沿用原行为）
    old = run_series.WORK
    run_series.WORK = str(tmp_path / "sub")
    try:
        run_series._write_qa_report([])
    finally:
        run_series.WORK = old
    assert not (tmp_path / "sub").exists()
    del qa


# ------------------------------------------------------------- _qa_policy
def test_qa_is_off_for_agent_by_default():
    """关键：run_series 的 qa.enabled=true 不能顺手把 MovieAgent 也打开。"""
    a, _ = _agent({"qa": {"enabled": True, "max_rerolls": 2}})
    on, policy = a._qa_policy()
    assert on is False and policy == {}


def test_qa_agent_switch_enables_and_reuses_thresholds():
    a, _ = _agent({"qa": {"enabled": False, "agent_enabled": True,
                          "max_rerolls": 2, "min_sharpness": 9.0}})
    on, policy = a._qa_policy()
    assert on is True
    assert policy["max_rerolls"] == 2 and policy["min_sharpness"] == 9.0
    # 其余阈值来自 qa.DEFAULTS
    from agent.qa import DEFAULTS
    assert policy["min_motion"] == DEFAULTS["min_motion"]


# ------------------------------------------------------- 出片链路的默认行为
def test_generate_one_scene_unchanged_when_qa_off():
    a, work = _agent({})
    a.generate_one_scene()
    assert len(a.engine.seeds) == 1             # 不重 roll
    assert "qa" not in a.state                  # 不写质检摘要
    params = json.load(open(os.path.join(work, "gen_params.json"), encoding="utf-8"))
    assert "qa_score" not in params["scene_001"]
    assert "qa" not in params["scene_001"]      # 策略快照也不写，文件保持原样


def test_generate_one_scene_rerolls_with_deterministic_seeds():
    """不达标要换 seed 重出；seed 必须可复现（等差 7919），失败样本才追得回来。"""
    a, work = _agent({"qa": {"agent_enabled": True, "max_rerolls": 1}})
    seen: list = []

    def _score(path, n):
        seen.append(n)
        ok = len(seen) > 1                     # 第一次不达标，第二次达标
        return {"path": path, "sharpness": 20.0, "motion": 2.0, "brightness": 100.0,
                "ok": ok, "reasons": [] if ok else ["画面偏糊(sharpness=1.0<5.0)"]}

    a._score_shot = _score
    a.generate_one_scene()

    assert len(a.engine.seeds) == 2
    assert a.engine.seeds[1] - a.engine.seeds[0] == 7919
    assert a.state["qa"]["total"] == 1          # 只累积最终采用的那次
    assert a.state["qa"]["last_ok"] is True
    assert a.state["qa"]["last_attempts"] == 2
    assert a.state["qa"]["report"] == "qa_report.json"
    assert os.path.exists(os.path.join(work, "qa_report.json"))

    params = json.load(open(os.path.join(work, "gen_params.json"), encoding="utf-8"))["scene_001"]
    assert params["attempt"] == 1                       # 重 roll 次数落盘
    assert params["seed"] == a.engine.seeds[1]          # 落盘的 seed 是真正用上的那个
    assert params["qa_score"]["ok"] is True
    assert params["qa"]["max_rerolls"] == 1


def test_generate_one_scene_keeps_last_result_when_never_passing():
    """始终不达标也不能卡住出片：采用最后一次并如实记录。"""
    a, _ = _agent({"qa": {"agent_enabled": True, "max_rerolls": 2}})
    a._score_shot = lambda path, n: {
        "path": path, "sharpness": 0.5, "motion": 0.0, "brightness": 0.0,
        "ok": False, "reasons": ["画面偏糊", "近乎静帧/卡死"]}
    a.generate_one_scene()
    assert len(a.engine.seeds) == 3                      # 1 + 2 次重 roll
    assert a.state["qa"]["last_ok"] is False
    assert a.state["qa"]["failed"] == 1
    assert a.film and os.path.exists(a.film)             # 成片照常落盘


# --------------------------------------------------------- preflight 门禁
def test_preflight_can_be_switched_off():
    a, work = _agent({"preflight": {"enabled": False}})
    allow, pf = a.publish_guard(os.path.join(work, "nope.mp4"))
    assert allow is True and pf == {}


def test_publish_guard_blocks_on_failed_preflight():
    a, work = _agent({"preflight": {"enabled": True, "block_publish": True}})
    allow, pf = a.publish_guard(os.path.join(work, "nope.mp4"))
    assert allow is False
    assert pf["ok"] is False and pf["errors"]      # 文件不存在 -> 阻断级错误


def test_publish_guard_can_be_set_to_warn_only():
    a, work = _agent({"preflight": {"enabled": True, "block_publish": False}})
    allow, pf = a.publish_guard(os.path.join(work, "nope.mp4"))
    assert allow is True and pf["ok"] is False     # 体检仍执行，只是不拦


def test_finalize_blocks_auto_publish_when_preflight_fails():
    import shutil

    a, work = _agent({"publish": {"enabled": True,
                                 "binary": "definitely_not_exist_biliup_xyz"},
                      "preflight": {"enabled": True, "block_publish": True}})
    assert a.publisher.enabled is True            # 前置条件：自动投稿开关命中
    with open(a.film, "wb") as f:
        f.write(b"x")
    a.editor.finalize = lambda src, out: shutil.copy(src, out)
    published: list = []
    a.publisher.publish_latest = lambda state, out: (published.append(out)
                                                     or {"ok": True, "title": "t"})
    a._preflight = lambda video: {"ok": False, "errors": ["标题缺少集数标记"],
                                  "warnings": []}

    out = a.finalize()
    assert out and os.path.exists(out)      # 成片保留
    assert published == []                  # 但自动投稿被拦下


def test_finalize_publishes_when_preflight_passes():
    import shutil

    a, work = _agent({"publish": {"enabled": True,
                                 "binary": "definitely_not_exist_biliup_xyz"},
                      "preflight": {"enabled": True, "block_publish": True}})
    with open(a.film, "wb") as f:
        f.write(b"x")
    a.editor.finalize = lambda src, out: shutil.copy(src, out)
    published: list = []
    a.publisher.publish_latest = lambda state, out: (published.append(out)
                                                     or {"ok": True, "title": "t"})
    a._preflight = lambda video: {"ok": True, "errors": [], "warnings": ["封面非建议尺寸"]}

    a.finalize()
    assert len(published) == 1


def test_character_anchor_resolves_relative_to_repo_root():
    a, _ = _agent({"series": {"character_anchor": "outputs/anchor/mira_anchor.png"}})
    p = a._character_anchor()
    assert os.path.isabs(p) and p.endswith(os.path.join("outputs", "anchor",
                                                       "mira_anchor.png"))
    assert p == os.path.join(ROOT, "outputs", "anchor", "mira_anchor.png")


@pytest.mark.parametrize("cfg_extra", [{}, {"series": {}}])
def test_character_anchor_has_default(cfg_extra):
    a, _ = _agent(cfg_extra)
    assert a._character_anchor().endswith("mira_anchor.png")
