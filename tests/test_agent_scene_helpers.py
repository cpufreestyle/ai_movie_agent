"""generate_one_scene / run 拆分后的行为锁（C901 26→<10、18→<10）。

重构只做了「按职责切函数」，语义必须逐条等价。这里锁的是**最容易在搬运中悄悄
变掉**的几点：

  * 白模三类条件（控制图 / 灰模动画 / 走位序列）的取值与过滤顺序；
  * 灰模动画低于 2s 下限要跳过（不是照传，也不是报错）；
  * 长片备份 + 新片段上位的顺序；
  * 生成参数落盘必须带上 control_video（少它这一镜无法复现）。
"""
from __future__ import annotations

import json
import os
import sys

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


class _FakeEngine:
    """声明全能力，记录每次 generate 的入参。"""
    TAG = "fake"
    CAPABILITIES = frozenset({"ref_images", "ref_video", "control_video", "fc_strength"})
    two_pass = False
    seed = 1
    num_frames = 97
    fps = 24
    resolution = "768x448"

    def __init__(self, ready=True):
        self.ready = ready
        self.calls: list[dict] = []

    def is_ready(self) -> bool:
        return self.ready

    def generate(self, prompt, out_path, **kw):
        self.calls.append({"prompt": prompt, "out_path": out_path, **kw})
        with open(out_path, "wb") as f:
            f.write(b"fake-clip")
        return out_path


@pytest.fixture()
def agent(tmp_path):
    from agent.agent import MovieAgent
    ag = MovieAgent(json.loads(json.dumps(CFG)), str(tmp_path))
    ag.state["bible"] = {"logline": "l", "outline": []}
    ag.state["beats"] = []
    ag.writer.next_beat = lambda bible, beats: {"title": "t", "description": "raw"}
    ag.polisher.polish = lambda s: s + "/polished"
    ag.director.beat_to_prompt = lambda beat: "PROMPT"
    return ag


def _mk(path: str, content: bytes = b"x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


# ------------------------------------------------------------ _prepare_beat
def test_prepare_beat_polishes_and_attaches_keyframe(agent):
    agent.image_prompts = ["kp"]
    agent.keyframe_images = ["/tmp/kf.png"]
    beat, prompt, keyframe = agent._prepare_beat(0)
    assert beat["description"] == "raw/polished"
    assert beat["keyframe_prompt"] == "kp"
    assert beat["keyframe_image"] == "/tmp/kf.png"
    assert keyframe == "/tmp/kf.png"
    assert prompt == "PROMPT"


def test_prepare_beat_without_keyframes(agent):
    beat, _prompt, keyframe = agent._prepare_beat(0)
    assert "keyframe_prompt" not in beat and "keyframe_image" not in beat
    assert keyframe is None


# --------------------------------------------------- 白模条件：ref_images
def test_ref_images_from_existing_controls_only(agent, tmp_path):
    depth = _mk(str(tmp_path / "depth.png"))
    line = _mk(str(tmp_path / "line.png"))
    agent.blocking_control = [{"depth": depth, "normal": str(tmp_path / "nope.png"), "line": line}]
    got = agent._ref_images_for(0, {"use_as_ref_images": True})
    assert got == [depth, line]


def test_ref_images_is_none_when_nothing_exists(agent, tmp_path):
    agent.blocking_control = [{"depth": str(tmp_path / "nope.png")}]
    assert agent._ref_images_for(0, {"use_as_ref_images": True}) is None


def test_ref_images_appends_anchor_once(agent, tmp_path, monkeypatch):
    anchor = _mk(str(tmp_path / "anchor.png"))
    monkeypatch.setattr(agent, "_character_anchor", lambda: anchor)
    # 1) 原本没有 ref_images -> 变成 [anchor]
    assert agent._ref_images_for(0, {"use_as_i2v_start": True}) == [anchor]
    # 2) anchor 已在列表里 -> 不重复追加
    agent.blocking_control = [{"depth": anchor}]
    got = agent._ref_images_for(0, {"use_as_ref_images": True, "use_as_i2v_start": True})
    assert got == [anchor]


# ---------------------------------------------------- 白模条件：ref_video
def test_ref_video_skips_below_floor(agent, tmp_path, monkeypatch):
    anim = _mk(str(tmp_path / "anim.mp4"))
    agent.blocking_anim = [anim]
    monkeypatch.setattr(agent.editor, "probe_duration", lambda p: 1.0)
    assert agent._ref_video_for(0, {"use_as_ref_video": True}) is None


def test_ref_video_passes_when_long_enough(agent, tmp_path, monkeypatch):
    anim = _mk(str(tmp_path / "anim.mp4"))
    agent.blocking_anim = [anim]
    monkeypatch.setattr(agent.editor, "probe_duration", lambda p: 3.0)
    assert agent._ref_video_for(0, {"use_as_ref_video": True}) == anim


def test_ref_video_requires_mp4(agent, tmp_path, monkeypatch):
    mov = _mk(str(tmp_path / "anim.mov"))
    agent.blocking_anim = [mov]
    monkeypatch.setattr(agent.editor, "probe_duration", lambda p: 9.0)
    assert agent._ref_video_for(0, {"use_as_ref_video": True}) is None


# ----------------------------------------------- 白模条件：Fun Control
def test_control_video_requires_existing_mp4(agent, tmp_path):
    fc = _mk(str(tmp_path / "fc.mp4"))
    agent.blocking_fc = [fc]
    assert agent._control_video_for(0, {"use_as_fun_control": True}) == fc
    agent.blocking_fc = [str(tmp_path / "missing.mp4")]
    assert agent._control_video_for(0, {"use_as_fun_control": True}) is None


def test_fc_strength_rules(agent):
    assert agent._fc_strength(None, {}) is None                       # 无控制视频 -> 不传
    assert agent._fc_strength("a.mp4", {}) == 1.2                     # 默认 1.2
    assert agent._fc_strength("a.mp4", {"fun_control_strength": 0.9}) == 0.9
    assert agent._fc_strength("a.mp4", {"fun_control_strength": "x"}) is None


def test_white_model_kwargs_filters_by_capability(agent, tmp_path, monkeypatch):
    depth = _mk(str(tmp_path / "depth.png"))
    agent.blocking_control = [{"depth": depth}]
    agent.blocking_fc = [_mk(str(tmp_path / "fc.mp4"))]
    agent.engine = _FakeEngine()
    agent.config["blender"] = {"use_as_ref_images": True, "use_as_fun_control": True}
    extra, ignored = agent._white_model_kwargs(0)
    assert extra["ref_images"] == [depth]
    assert extra["control_video"].endswith("fc.mp4")
    assert extra["fc_strength"] == 1.2
    assert ignored == {}

    # 换成一个什么都不支持的引擎 -> 全部走 ignored，不进 extra
    agent.engine.CAPABILITIES = frozenset()
    extra, ignored = agent._white_model_kwargs(0)
    assert extra == {}
    assert set(ignored) == {"ref_images", "control_video", "fc_strength"}


# ------------------------------------------------------- 备份 / 落盘
def test_rotate_film_backups_previous(agent, tmp_path):
    _mk(agent.film, b"old")
    new_clip = _mk(str(tmp_path / "scene_001.mp4"), b"new")
    agent._rotate_film(1, new_clip)
    assert open(agent.film, "rb").read() == b"new"
    backup = os.path.join(agent.scenes_dir, "film_after_001.mp4")
    assert open(backup, "rb").read() == b"old"
    assert not os.path.exists(new_clip)


def test_rotate_film_no_backup_on_first_scene(agent, tmp_path):
    new_clip = _mk(str(tmp_path / "scene_001.mp4"), b"new")
    agent._rotate_film(0, new_clip)
    assert os.path.exists(agent.film)
    assert not os.path.exists(os.path.join(agent.scenes_dir, "film_after_000.mp4"))


# ------------------------------------------------- generate_one_scene 端到端
def test_generate_one_scene_returns_none_when_engine_not_ready(agent):
    agent.engine = _FakeEngine(ready=False)
    assert agent.generate_one_scene() is None
    assert agent.state["scene_count"] == 0


def test_generate_one_scene_happy_path(agent, tmp_path):
    depth = _mk(str(tmp_path / "depth.png"))
    fc = _mk(str(tmp_path / "fc.mp4"))
    agent.engine = _FakeEngine()
    agent.blocking_control = [{"depth": depth}]
    agent.blocking_fc = [fc]
    agent.config["blender"] = {"use_as_ref_images": True, "use_as_fun_control": True,
                               "fun_control_strength": 0.8}

    beat = agent.generate_one_scene(seed=7)
    assert beat is not None
    assert agent.state["scene_count"] == 1
    assert os.path.exists(agent.film)

    kw = agent.engine.calls[0]
    assert kw["seed"] == 7
    assert kw["ref_images"] == [depth]
    assert kw["control_video"] == fc and kw["fc_strength"] == 0.8

    # 参数落盘必须带 control_video（缺它这一镜不可复现）
    params = json.load(open(os.path.join(agent.workdir, "gen_params.json"), encoding="utf-8"))
    rec = params["scene_001"]
    assert rec["control_video"] == "fc.mp4"
    assert rec["ref_images"] == ["depth.png"]
    assert rec["seed"] == 7

    # 分镜流水日志
    assert os.path.exists(agent.script_path)


def test_generate_one_scene_rerolls_on_qa_failure(agent, tmp_path, monkeypatch):
    """质检开启且不达标：换 seed 重 roll，最后一次被采用且如实记录。"""
    agent.engine = _FakeEngine()
    agent.config["qa"] = {"agent_enabled": True, "max_rerolls": 1}

    scores = iter([{"ok": False, "reasons": ["太黑"], "black_ratio": 0.9},
                   {"ok": False, "reasons": ["太黑"], "black_ratio": 0.9}])
    monkeypatch.setattr(agent, "_score_shot", lambda path, n: dict(next(scores)))

    beat = agent.generate_one_scene(seed=100)
    assert beat is not None
    assert len(agent.engine.calls) == 2, "应重 roll 一次"
    assert agent.engine.calls[0]["seed"] == 100
    assert agent.engine.calls[1]["seed"] == 100 + 7919
    # 落盘的是真正用上的最后一次 seed
    params = json.load(open(os.path.join(agent.workdir, "gen_params.json"), encoding="utf-8"))
    assert params["scene_001"]["seed"] == 100 + 7919
    assert params["scene_001"]["attempt"] == 1
    assert agent.state["qa"]["last_attempts"] == 2
    assert agent.state["qa"]["last_ok"] is False


def test_generate_one_scene_stops_reroll_when_ok(agent, tmp_path, monkeypatch):
    agent.engine = _FakeEngine()
    agent.config["qa"] = {"agent_enabled": True, "max_rerolls": 2}
    monkeypatch.setattr(agent, "_score_shot",
                        lambda path, n: {"ok": True, "reasons": [], "black_ratio": 0.0})
    agent.generate_one_scene(seed=5)
    assert len(agent.engine.calls) == 1
    assert agent.state["qa"]["last_ok"] is True
