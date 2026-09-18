"""投稿元数据生成器单测：规则法确定性 + B 站约束收敛 + 中文数字。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import metadata as md  # noqa: E402

_LINES = [
    "雨还在下。可这一次，我知道自己是谁，也知道该往哪走。",
    "我回到了那家记忆店铺——第一次把童年卖出去的地方。",
    "读忆椅还在老地方，蓝光安静地亮着。",
    "我把那枚空白芯片留在柜台上。它不再属于我。",
    "我转身走出店门，沿着雨街，往自己选的方向去。",
    "雨还在下。但我记得自己是谁——这一次，门为我而亮。",
]


def test_cn_num():
    assert md.cn_num(1) == "一"
    assert md.cn_num(5) == "五"
    assert md.cn_num(10) == "十"
    assert md.cn_num(11) == "十一"
    assert md.cn_num(20) == "二十"
    assert md.cn_num(23) == "二十三"


def test_rule_metadata_deterministic():
    a = md.rule_metadata("《看见未来之前》", 5, "雨城归途", _LINES)
    b = md.rule_metadata("《看见未来之前》", 5, "雨城归途", _LINES)
    assert a == b


def test_enforce_limits_respects_bili_caps():
    raw = md.rule_metadata("《看见未来之前》", 5, "雨城归途", _LINES)
    m = md.enforce_limits(dict(raw, titles=list(raw["titles"]), tags=list(raw["tags"])))
    assert m["title"]
    assert len(m["title"]) <= md.TITLE_MAX
    assert 0 < len(m["tags"]) <= md.TAG_MAX
    assert all(len(t) <= md.TAG_LEN_MAX for t in m["tags"])
    assert len(m["dynamic"]) <= md.DYNAMIC_MAX
    assert "第五集" in m["title"]


def test_theme_tags_matches_content():
    tags = md._theme_tags(_LINES, k=4)
    assert tags
    assert all(t in md.THEME_TAGS for t in tags)
    assert "记忆" in tags          # 本集旁白含「记忆店铺」


def test_generate_writes_json(tmp_path):
    script = {
        "series": "《测试》",
        "ep1": {"title": "开场",
                "narration": ["第一次走进城。霓虹染雨。", "记忆能换钱，我半信半疑。"],
                "narration_en": ["The first time I entered the city."]},
    }
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    outp = tmp_path / "ep1_meta.json"
    meta = md.generate(1, script_path=str(sp),
                       config={"llm": {"disabled": True}}, out=str(outp))
    assert meta["source"] == "rule"
    assert outp.exists()
    saved = json.loads(outp.read_text(encoding="utf-8"))
    assert saved["title"] == meta["title"]
    assert md.validate(meta)["ok"]


def test_validate_flags_thin_metadata():
    res = md.validate({"title": "第一集", "tags": [], "dynamic": ""})
    assert res["ok"]
    assert any("标签" in w for w in res["warnings"])
