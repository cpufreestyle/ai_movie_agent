"""series（run_series.py）表单 → argv 的契约锁 + 分镜保存校验。

这两块都**没有跑真机**就能验的关键性质：

  - `build_argv` 是纯函数：UI 上勾了「镜间尾帧续写」就必须真的出现 `--i2v`，
    勾了 `chain` 就必须出现 `--anchor-mode chain`。这类「开关没接上」的 bug
    在界面上完全看不出来（点了没反应/静默用默认值），只能靠断言 argv 拦住。
  - `validate_storyboard` 是保存前唯一的闸门：历史上「末两镜内容完全一样」
    被原样写进 storyboard.json，渲染完才发现。

`services/series.py` 刻意不 import run_series（会建目录 + 探 ffmpeg），
所以这里也不 import 脚本，只对照它 argparse 里的 flag 名字断言。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webserver.services import series  # noqa: E402
from webserver.services.pipeline import (  # noqa: E402
    normalize_shot_indices,
    validate_storyboard,
)


# ---------------- series.build_argv ----------------

def test_default_argv_is_plain_t2v_run():
    argv = series.build_argv({})
    assert argv[0] == "run_series.py"
    assert "--i2v" not in argv, "默认必须是每镜 T2V（I2V 会让画面趋同、与旁白脱节）"
    assert argv[argv.index("--ep") + 1] == "0"
    assert argv[argv.index("--engine") + 1] == "mmh3"
    assert argv[argv.index("--anchor-mode") + 1] == "first"
    assert "--anchor" not in argv
    assert "--only" not in argv
    assert "--force" not in argv


def test_i2v_flag_is_actually_passed():
    """UI 上勾了「镜间尾帧续写」必须真的带上 --i2v —— 否则界面上完全看不出来。"""
    assert "--i2v" in series.build_argv({"i2v": True})
    assert "--i2v" in series.build_argv({"i2v": 1})
    assert "--i2v" in series.build_argv({"i2v": "true"})


def test_anchor_mode_and_anchor_are_passed():
    argv = series.build_argv({"anchor_mode": "chain", "anchor": "auto"})
    assert argv[argv.index("--anchor-mode") + 1] == "chain"
    assert argv[argv.index("--anchor") + 1] == "auto"


def test_blank_anchor_is_omitted_not_empty_arg():
    """留空 = 不锚定，不能传成 `--anchor ""`（脚本会把它当成显式请求）。"""
    assert "--anchor" not in series.build_argv({"anchor": ""})
    assert "--anchor" not in series.build_argv({"anchor": "   "})


def test_only_indices_are_normalized_and_sorted():
    argv = series.build_argv({"only": "16, 14，15、14"})
    assert argv[argv.index("--only") + 1] == "14,15,16"


def test_concat_ignores_ep_and_uses_concat_flag():
    argv = series.build_argv({"concat": 2, "ep": 3})
    assert argv[argv.index("--concat") + 1] == "2"
    assert "--ep" not in argv


def test_engine_and_ep_are_passed():
    argv = series.build_argv({"engine": "ltx", "ep": 2})
    assert argv[argv.index("--engine") + 1] == "ltx"
    assert argv[argv.index("--ep") + 1] == "2"


def test_force_flag():
    assert "--force" in series.build_argv({"force": True})


@pytest.mark.parametrize("bad", [{"engine": "sora"}, {"anchor_mode": "middle"},
                                 {"ep": 9}, {"concat": 4}, {"only": "abc"}])
def test_validate_rejects_bad_input(bad):
    assert series.validate(bad), f"{bad} 应被拒绝"


def test_concat_zero_means_not_requested():
    """`concat: 0` 与脚本里 `--concat 0` 同义（falsy = 不拼接），不是「非法集号」。"""
    assert series.validate({"concat": 0}) == []
    assert "--concat" not in series.build_argv({"concat": 0})


def test_validate_rejects_concat_mixed_with_only():
    """--concat 会直接跳过 --only/--force/--i2v，混在一起是静默失效，必须拦。"""
    errors = series.validate({"concat": 1, "only": "3"})
    assert errors and "互斥" in errors[0]


def test_validate_accepts_typical_run():
    assert series.validate({"ep": 1, "engine": "mmh3", "i2v": True,
                            "anchor_mode": "chain", "anchor": "auto"}) == []


# ---------------- normalize_shot_indices ----------------

@pytest.mark.parametrize("value,expect", [
    ("1,3,5", [1, 3, 5]),
    ("3 1 2", [1, 2, 3]),
    ("1，2、3；4", [1, 2, 3, 4]),
    ([3, "1", 1], [1, 3]),
    (None, []),
    ("", []),
    ("0,-2,abc", []),        # 全非法 → 空（由调用方报错）
    ("1,0,-2,abc", [1]),     # 混入非法项 → 只留合法项
])
def test_normalize_shot_indices(value, expect):
    assert normalize_shot_indices(value) == expect


def test_shot_index_zero_is_dropped_not_meant_as_all():
    """`--shot 0` 在脚本语义里等于「全部」—— 绝不能把 0 当合法镜号传下去。"""
    assert normalize_shot_indices("0") == []


# ---------------- validate_storyboard ----------------

def _shots(n, prefix="shot"):
    return [f"{prefix} {i} cinematic" for i in range(1, n + 1)]


def test_valid_storyboard_passes_without_warnings():
    errors, warnings = validate_storyboard(_shots(18), ["独白一", "独白二"])
    assert errors == []
    assert warnings == []


def test_duplicate_shots_are_rejected():
    """历史 bug：末两镜都是 FINAL SHOT，渲染完才发现画面重复。"""
    shots = _shots(17) + ["FINAL SHOT: she looks into camera"]
    shots[16] = "FINAL SHOT: she looks into camera"
    errors, _ = validate_storyboard(shots, ["独白"])
    assert any("完全相同" in e for e in errors), errors


def test_duplicate_detection_ignores_case_and_whitespace():
    shots = ["A wide shot", "  a wide shot  ", "b"]
    errors, _ = validate_storyboard(shots, ["独白"])
    assert any("完全相同" in e for e in errors), errors


def test_empty_shot_and_empty_narration_are_rejected():
    errors, _ = validate_storyboard(["a", "   "], [])
    assert any("空行" in e for e in errors)
    assert any("解说不能为空" in e for e in errors)


def test_shot_count_deviation_is_only_a_warning():
    """镜数不是 18 可能是刻意的 —— 只提醒，不拦。"""
    errors, warnings = validate_storyboard(_shots(12), ["独白"])
    assert errors == []
    assert any("12 镜" in w for w in warnings)


def test_more_narration_than_shots_is_a_warning():
    errors, warnings = validate_storyboard(_shots(3), ["一", "二", "三", "四"])
    assert errors == []
    assert any("多于镜头" in w for w in warnings)


def test_chinese_shot_is_flagged():
    """视频模型吃英文提示词，中文描述出不来预期画面。"""
    errors, warnings = validate_storyboard(["霓虹街景大雨"], ["独白"])
    assert errors == []
    assert any("英文字母" in w for w in warnings)


def test_overlong_narration_is_flagged():
    errors, warnings = validate_storyboard(_shots(1), ["字" * 70])
    assert errors == []
    assert any("超过" in w for w in warnings)


# ---------------- 路由层：只验形状与防护，桩掉后台任务 ----------------

@pytest.fixture()
def client():
    import webui

    webui.app.config["TESTING"] = True
    return webui.app.test_client()


@pytest.fixture()
def stub_run(monkeypatch):
    """桩掉后台执行 + 复位忙锁 —— 绝不能在测试里真起渲染子进程。"""
    from webserver.views import film as film_view
    from webserver.views import series as series_view

    captured: dict = {}
    monkeypatch.setattr(series_view, "run_in_background",
                        lambda fn: captured.setdefault("fn", fn))
    monkeypatch.setattr(film_view, "run_in_background",
                        lambda fn: captured.setdefault("fn", fn))
    monkeypatch.setitem(series_view._state, "running", False)   # 与 film 同一个 dict
    return captured


def test_api_series_shape(client):
    j = client.get("/api/series").get_json()
    assert [e["value"] for e in j["episodes"]] == [0, 1, 2, 3]
    assert {e["value"] for e in j["engines"]} == {"mmh3", "ltx"}
    assert {m["value"] for m in j["anchor_modes"]} == {"first", "chain"}
    assert j["i2v"] is False
    assert isinstance(j["films"], list)


def test_api_series_run_rejects_unknown_engine(client, stub_run):
    r = client.post("/api/series/run", json={"engine": "sora"})
    assert r.status_code == 400


def test_api_series_run_busy_returns_409(client, monkeypatch):
    from webserver.views import series as view

    monkeypatch.setitem(view._state, "running", True)
    assert client.post("/api/series/run", json={}).status_code == 409


def test_api_storyboard_rejects_duplicate_shots(client):
    dup = ["same shot"] * 2
    r = client.post("/api/storyboard", json={"shots": dup, "narration": ["独白"]})
    assert r.status_code == 400
    assert "完全相同" in r.get_json()["error"]


def test_api_storyboard_returns_warnings(client, tmp_path, monkeypatch):
    """告警要回给前端（而不是只写日志），否则用户看不到。"""
    from webserver.views import film as view

    monkeypatch.setattr(view, "STORYBOARD_PATH", str(tmp_path / "sb.json"))
    r = client.post("/api/storyboard",
                    json={"shots": ["中文镜", "另一镜"], "narration": ["独白"]})
    j = r.get_json()
    assert j["ok"] and j["warnings"], j


def test_api_film_render_shots_requires_indices(client, stub_run):
    r = client.post("/api/film/render", json={"only": "shots", "shots": "abc"})
    assert r.status_code == 400


def test_api_film_render_shots_out_of_range(client, stub_run, monkeypatch):
    from webserver.views import film as view

    monkeypatch.setattr(view, "storyboard_shot_count", lambda: 3)
    r = client.post("/api/film/render", json={"only": "shots", "shots": "1,99"})
    assert r.status_code == 400
    assert "越界" in r.get_json()["error"]
