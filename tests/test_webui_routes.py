"""WebUI 路由表回归：拆分（webui.py -> webserver/views/*）后 URL 与方法必须一条不少。

WebUI 被拆成 state / services / views 三层，蓝本(Blueprint)注册顺序与 prefix 都可能
被后续改动影响，而前端全部靠硬编码 URL fetch，丢一条路由不会报错、只会静默 404。
这里把 (URL, 方法) 集合冻结在测试里。

endpoint 名会带 blueprint 前缀（api_logs -> core.api_logs），属预期变化，不在此断言。
"""
from __future__ import annotations

import webui

# (URL 规则, 允许的方法) —— 与拆分前的单文件版本逐条比对过
EXPECTED = {
    ("/", ("GET",)),
    ("/timeline", ("GET",)),
    ("/white/<name>", ("GET",)),
    ("/static/<path:filename>", ("GET",)),

    ("/api/timeline", ("GET",)),
    ("/api/timeline", ("POST",)),
    ("/api/timeline/build", ("POST",)),
    ("/api/overview", ("GET",)),
    ("/api/config", ("GET",)),
    ("/api/config", ("POST",)),
    ("/api/logs", ("GET",)),

    ("/api/run", ("POST",)),
    ("/api/run/full", ("POST",)),
    ("/api/stop", ("POST",)),

    ("/api/enrich", ("POST",)),
    ("/api/publish_concept", ("POST",)),
    ("/api/publish", ("POST",)),
    ("/api/pipeline", ("GET",)),
    ("/api/pipeline/bible", ("POST",)),
    ("/api/pipeline/draft", ("POST",)),
    ("/api/pipeline/image-prompts", ("POST",)),
    ("/api/pipeline/stage/<stage>", ("POST",)),
    ("/api/board", ("GET",)),

    ("/api/biliup", ("GET",)),
    ("/api/bili/videos", ("GET",)),
    ("/api/bili/video/<bvid>", ("GET",)),
    ("/api/bili/update", ("POST",)),

    ("/api/media", ("GET",)),

    ("/api/blocking", ("GET",)),
    ("/api/blocking/parse", ("POST",)),
    ("/api/blocking/run", ("POST",)),
    ("/api/blocking/file", ("GET",)),

    ("/api/anchor", ("GET",)),
    ("/api/anchor/run", ("POST",)),
    ("/api/anchor/file", ("GET",)),
    ("/api/anchor/index", ("GET",)),

    ("/api/films", ("GET",)),
    ("/api/film/play", ("GET",)),
    ("/api/film/render", ("POST",)),
    ("/api/storyboard", ("GET",)),
    ("/api/storyboard", ("POST",)),
    ("/api/storyboard/generate", ("POST",)),
    ("/api/models", ("GET",)),

    ("/api/series", ("GET",)),
    ("/api/series/run", ("POST",)),

    ("/api/hw", ("GET",)),
}


def _actual() -> set:
    out = set()
    for r in webui.app.url_map.iter_rules():
        methods = tuple(sorted(m for m in r.methods if m not in ("HEAD", "OPTIONS")))
        out.add((str(r.rule), methods))
    return out


def test_route_table_matches_frozen_baseline():
    actual = _actual()
    missing = sorted(EXPECTED - actual)
    added = sorted(actual - EXPECTED)
    assert not missing, f"路由丢失（前端会静默 404）：{missing}"
    assert not added, f"出现未登记的新路由（请确认后更新 EXPECTED）：{added}"


def test_no_duplicate_rule_method_pairs():
    seen: dict = {}
    for r in webui.app.url_map.iter_rules():
        if r.endpoint == "static":
            continue
        for m in r.methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            seen.setdefault((str(r.rule), m), []).append(r.endpoint)
    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dupes, f"同一 URL+方法被多个视图占用：{dupes}"


def test_every_route_has_view_function():
    for r in webui.app.url_map.iter_rules():
        assert webui.app.view_functions.get(r.endpoint) is not None, r.endpoint


def test_shared_state_reexported_for_legacy_callers():
    """cli.py 的 `from webui import main` 与既有测试用的名字必须仍然可用。"""
    for name in ("app", "main", "_state", "_lock", "_stop_requested", "run_script",
                 "json_resp", "load_config", "get_agent", "WORKDIR", "MEDIA"):
        assert hasattr(webui, name), f"webui.{name} 丢失（旧调用点会 ImportError/AttributeError）"
