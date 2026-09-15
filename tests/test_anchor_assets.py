"""锚定资产业务层与生成脚本的契约锁。

`webserver/services/anchor.py` **故意不 import** `gen_anchor_assets`（后者顶层
`import run_series`，会在 Web 服务启动路径上建目录并探测 ffmpeg），代价是两边
各存了一份条目清单。这份清单一旦漂移，界面会**静默**少显示/多显示一张图 ——
没有异常、没有日志，只是点了一键生成却什么都没出。

所以这里把三件事钉死：
  1. `services.SHOT_SPECS` 的名称与顺序 == 脚本 `SHOTS`；
  2. 默认勾选 == 三视图（用户规则）；
  3. 只看本层的 `safe_path` 不能穿出 anchor 目录。
"""
from __future__ import annotations

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webserver.services import anchor  # noqa: E402

VIEW_NAMES = ["mira_front", "mira_side", "mira_back"]


def _script_shots() -> list:
    """脚本里的 SHOTS 名称顺序（import 会拉起 run_series，故只读源码取名称）。"""
    src = open(os.path.join(ROOT, "gen_anchor_assets.py"), encoding="utf-8").read()
    m = re.search(r"^SHOTS = \[(.*?)^\]", src, re.S | re.M)
    assert m, "未能在 gen_anchor_assets.py 中定位 SHOTS"
    names = re.findall(r'^\s*\("([a-z_]+)",', m.group(1), re.M)
    assert names, "SHOTS 里没解析出任何条目名"
    return names


def test_shot_specs_match_generator_shots():
    """界面条目名/顺序必须与脚本 SHOTS 完全一致（顺序也锁 —— 图册按它排版）。"""
    assert anchor.generatable_names() == _script_shots()


def test_specs_are_unique_and_have_hints():
    names = anchor.generatable_names()
    assert len(names) == len(set(names)), "SHOT_SPECS 有重名条目"
    for name, grp, label, hint in anchor.SHOT_SPECS:
        assert grp in (anchor.VIEW_GROUP, anchor.SCENE_GROUP), name
        assert label and hint, f"{name} 缺少中文标签或悬停说明"


def test_default_names_are_three_views():
    """用户规则（2026-09-15）：一键生成默认出三视图。"""
    assert anchor.default_names() == VIEW_NAMES


def test_shot_groups_cover_all_names_once_view_first():
    groups = anchor.shot_groups()
    assert [g["key"] for g in groups] == [anchor.VIEW_GROUP, anchor.SCENE_GROUP]
    flat = [it["name"] for g in groups for it in g["items"]]
    assert flat == anchor.generatable_names()
    assert sorted(flat) == sorted(set(flat))
    # 默认勾选项必须落在「三视图」组内，否则界面上默认勾选会跑到场景图里
    checked = [it["name"] for g in groups for it in g["items"] if it["default"]]
    assert checked == VIEW_NAMES


def test_derived_items_are_not_generatable():
    """mira_anchor 由脚本自动裁出，不能被前端当参数传进 --only。"""
    derived = [x["name"] for x in anchor.derived_items()]
    assert derived == ["mira_anchor"]
    assert not set(derived) & set(anchor.generatable_names())


def test_default_resolution_matches_script():
    """services 的分辨率默认值必须跟脚本 argparse 的默认值同源。"""
    src = open(os.path.join(ROOT, "gen_anchor_assets.py"), encoding="utf-8").read()
    m = re.search(r'"--resolution",\s*default="([^"]+)"', src)
    assert m, "未能在 gen_anchor_assets.py 中定位 --resolution 默认值"
    assert m.group(1) == anchor.DEFAULT_RESOLUTION
    assert anchor.DEFAULT_RESOLUTION in anchor.RESOLUTIONS


@pytest.mark.parametrize("evil", ["../config.yaml", "..\\config.yaml",
                                  "sub/../../config.yaml", "/etc/passwd"])
def test_safe_path_rejects_traversal(evil):
    assert anchor.safe_path(evil) == ""


def test_safe_path_accepts_plain_name():
    got = anchor.safe_path("mira_front.png")
    assert got.startswith(anchor.anchor_dir() + os.sep)
    assert got.endswith("mira_front.png")


def test_comfy_ready_false_on_dead_port():
    """给一个必然没人监听的端口 —— 探测必须是「返回 False」而不是抛异常。"""
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert anchor.comfy_ready(f"http://127.0.0.1:{port}", timeout=0.5) is False


# ---- 路由层：只验「参数白名单 + 防护 + 返回形状」，一律桩掉后台任务 ----

@pytest.fixture()
def client():
    import webui

    webui.app.config["TESTING"] = True
    return webui.app.test_client()


@pytest.fixture()
def stub_run(monkeypatch):
    """把 /api/anchor/run 的后台执行换成空操作 —— 绝不能在测试里真起生成子进程。"""
    from webserver.views import anchor as view

    captured = {}
    monkeypatch.setattr(view, "run_in_background", lambda fn: captured.setdefault("fn", fn))
    monkeypatch.setitem(view._state, "running", False)
    return captured


def test_api_anchor_shape(client):
    j = client.get("/api/anchor").get_json()
    assert j["defaults"] == VIEW_NAMES
    assert j["resolution"] in j["resolutions"]
    assert [g["key"] for g in j["groups"]] == [anchor.VIEW_GROUP, anchor.SCENE_GROUP]
    assert j["script"] == "gen_anchor_assets.py"


def test_api_anchor_file_blocks_traversal(client):
    assert client.get("/api/anchor/file?name=../config.yaml").status_code == 400
    assert client.get("/api/anchor/file?name=..%2Fconfig.yaml").status_code == 400


def test_api_anchor_run_defaults_to_three_views(client, stub_run):
    j = client.post("/api/anchor/run", json={}).get_json()
    assert j["ok"] and j["only"] == VIEW_NAMES
    assert "fn" in stub_run, "未把任务交给后台执行"


def test_api_anchor_run_filters_unknown_names(client, stub_run):
    """只有白名单内的名称能进 --only（否则等于把任意字符串拼进命令行）。"""
    j = client.post("/api/anchor/run",
                    json={"only": ["../../evil", "mira_front", "rm -rf /"]}).get_json()
    assert j["only"] == ["mira_front"]


def test_api_anchor_run_rejects_bad_resolution(client, stub_run):
    r = client.post("/api/anchor/run", json={"only": ["mira_front"], "resolution": "1x1"})
    assert r.status_code == 400
    assert "分辨率" in r.get_json()["error"]


def test_api_anchor_run_busy_returns_409(client, monkeypatch):
    from webserver.views import anchor as view

    monkeypatch.setitem(view._state, "running", True)
    r = client.post("/api/anchor/run", json={"only": ["mira_front"]})
    assert r.status_code == 409

