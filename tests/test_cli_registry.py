"""cli.py 子命令注册表：防止「加了子命令忘了登记 / 改了名字没同步」。

重构前 main() 是一条 if/elif 长链，新增子命令漏写 elif 时不会报错、只会静默
走到最后什么都不做。现在用 COMMANDS 表分派，这里锁两件事：
  1. argparse 里的每个子命令都能在 COMMANDS 里找到 handler（不多不少）；
  2. main() 确实按表分派，并把 (args, config) 传进去。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cli


def _subcommands() -> set:
    ap = cli.build_parser()
    for act in ap._subparsers._group_actions:
        choices = getattr(act, "choices", None)
        if choices and "run" in choices:
            return set(choices)
    raise AssertionError("未找到子命令表")


def test_commands_registry_matches_subcommands():
    assert _subcommands() == set(cli.COMMANDS)


def test_expected_subcommands_present():
    expected = {"run", "pipeline", "status", "init", "publish", "publish-concept",
                "enrich-bible", "webui", "blender", "ltx", "mmh3", "ab",
                "preflight", "style", "mix", "tts", "charcard", "timeline"}
    assert expected == _subcommands()


def test_main_dispatches_by_registry(monkeypatch, tmp_path):
    seen = {}

    def fake_status(args, config):
        seen["workdir"] = args.workdir
        seen["config"] = config

    monkeypatch.setitem(cli.COMMANDS, "status", fake_status)
    monkeypatch.setattr(sys, "argv",
                        ["cli.py", "status", "--workdir", str(tmp_path)])
    cli.main()
    assert seen["workdir"] == str(tmp_path)
    assert isinstance(seen["config"], dict)


def test_main_errors_on_unknown_command(monkeypatch):
    """表里没有的 cmd 必须报错退出，不能静默什么都不做。"""
    monkeypatch.setattr(sys, "argv", ["cli.py", "status"])
    monkeypatch.setattr(cli, "COMMANDS", {})
    with pytest.raises(SystemExit):
        cli.main()


def test_real_status_still_works(tmp_path, capsys):
    monkeypatch_argv = ["cli.py", "status", "--workdir", str(tmp_path)]
    old = sys.argv
    sys.argv = monkeypatch_argv
    try:
        cli.main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    assert "scene_count" in out
