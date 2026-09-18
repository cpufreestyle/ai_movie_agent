"""run_series 批量多集（--eps）与竖屏比例（--ratio）预设的纯函数单测。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_series as rs  # noqa: E402


def _data(n=5):
    return {f"ep{i}": {} for i in range(1, n + 1)}


def test_parse_eps_range_and_list():
    d = _data()
    assert rs._parse_eps("1-3", d) == [1, 2, 3]
    assert rs._parse_eps("1,3,5", d) == [1, 3, 5]
    assert rs._parse_eps("all", d) == [1, 2, 3, 4, 5]
    assert rs._parse_eps("", d) == [1, 2, 3, 4, 5]   # 缺省=剧本全部集数


def test_parse_eps_dedup_sorted():
    assert rs._parse_eps("2,1,2", _data(2)) == [1, 2]


def test_parse_eps_rejects_bad_spec():
    import pytest
    with pytest.raises(SystemExit):
        rs._parse_eps("x", {"ep1": {}})


def test_resolve_episodes_priority():
    ns = argparse.Namespace(ep=2, eps="1-3")
    assert rs._resolve_episodes(ns, _data()) == [1, 2, 3]   # --eps 优先
    ns = argparse.Namespace(ep=2, eps="")
    assert rs._resolve_episodes(ns, _data()) == [2]         # --ep 单集
    ns = argparse.Namespace(ep=0, eps="")
    assert rs._resolve_episodes(ns, _data()) == [1, 2, 3, 4, 5]  # 全部（按剧本）


def test_apply_ratio_vertical_and_explicit_priority():
    a = argparse.Namespace(width=0, height=0, ratio="9:16")
    rs._apply_ratio(a)
    assert (a.width, a.height) == (576, 1024)
    b = argparse.Namespace(width=768, height=448, ratio="9:16")   # 显式优先
    rs._apply_ratio(b)
    assert (b.width, b.height) == (768, 448)
    c = argparse.Namespace(width=0, height=0, ratio="")
    rs._apply_ratio(c)
    assert (c.width, c.height) == (0, 0)
