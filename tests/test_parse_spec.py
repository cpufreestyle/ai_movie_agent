"""`BlockingGenerator.parse_spec` 的规则表回归（P1-⑥）。

改写成声明式规则表后，这里把「已真机验证过的关键词」逐条锁住，避免以后有人调整
表顺序（= 改优先级）却不知道后果：

* 机位 / 镜头大小 / 运镜：表顺序即优先级（`摇` 必须先于 `推/拉`）；
* 走位 6 类输出：左→右 / 右→左 / 走近镜头 / 远离镜头 / 来回 / 绕圈；
* 「左/右同时出现」优先按出现先后定方向，压过其它走位关键词；
* 匹配统一按小写（原实现 shot/camera/height 大小写敏感、走位却又先 lower）。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture()
def bg(tmp_path):
    from agent.blocking import BlockingGenerator
    # use_llm_parse 默认关 → 不会触发网络；BlenderMCP 是惰性连接，构造安全
    return BlockingGenerator({"blender": {"enabled": False}, "engine": {}},
                             str(tmp_path))


# ---------------------------------------------------------------- 默认值
def test_defaults_when_no_keyword(bg):
    spec = bg.parse_spec("一个角色站在雨中")
    assert spec["shot"] == "medium"
    assert spec["camera"] == "static"
    assert spec["height"] == "eye"
    assert spec["walk"] == ""
    assert spec["characters"] == 1 and spec["props"] == []
    assert bg.parse_spec("")["walk"] == ""
    assert bg.parse_spec(None)["shot"] == "medium"     # 空文本不应崩


# ---------------------------------------------------------------- 镜头大小
@pytest.mark.parametrize("text,expect", [
    ("大全景，城市天际线", "wide"),
    ("远景", "wide"),
    ("establishing shot of the city", "wide"),
    ("Wide Shot of the city", "wide"),            # 大写也要命中（旧实现选不中）
    ("角色特写镜头", "close"),
    ("大特写", "close"),
    ("CLOSE UP on her face", "close"),
    ("中景", "medium"),                            # 无关键词 → 默认
])
def test_shot_rules(bg, text, expect):
    assert bg.parse_spec(text)["shot"] == expect


def test_shot_wide_wins_over_close(bg):
    """表顺序 = 优先级：wide 在前（"全景特写"这种混合描述取 wide）。"""
    assert bg.parse_spec("全景加特写")["shot"] == "wide"


# ---------------------------------------------------------------- 运镜
@pytest.mark.parametrize("text,expect", [
    ("镜头缓慢平摇", "pan"),
    ("pan across the room", "pan"),
    ("镜头推进", "dolly"),
    ("镜头向后拉", "dolly"),
    ("slow dolly in", "dolly"),
    ("镜头横移跟拍", "track"),
    ("camera tracks the actor", "track"),
    ("镜头环绕角色", "orbit"),
    ("orbit around her", "orbit"),
    ("固定机位", "static"),
])
def test_camera_rules(bg, text, expect):
    assert bg.parse_spec(text)["camera"] == expect


def test_camera_priority_pan_beats_dolly(bg):
    """`摇` 必须排在 `推/拉` 之前：整链顺序就是原实现的优先级。"""
    assert bg.parse_spec("先摇再推")["camera"] == "pan"


def test_camera_priority_dolly_beats_track(bg):
    assert bg.parse_spec("推镜跟拍")["camera"] == "dolly"


# ---------------------------------------------------------------- 机位高度
@pytest.mark.parametrize("text,expect", [
    ("低机位仰拍", "low"),
    ("low angle shot", "low"),
    ("高机位俯拍", "high"),
    ("航拍", "high"),
    ("俯视角度", "high"),
    ("平视机位", "eye"),
])
def test_height_rules(bg, text, expect):
    assert bg.parse_spec(text)["height"] == expect


def test_height_low_wins_over_high(bg):
    assert bg.parse_spec("低机位接高机位")["height"] == "low"


# ---------------------------------------------------------------- 走位（6 类）
WALK_CASES = [
    ("角色从左到右走过", "-1,0:1,0"),
    ("从画面左侧走到右侧", "-1,0:1,0"),
    ("由左向右", "-1,0:1,0"),
    ("walks from left to right", "-1,0:1,0"),
    ("角色从右到左走过", "1,0:-1,0"),
    ("由右向左", "1,0:-1,0"),
    ("walks from right to left", "1,0:-1,0"),
    ("走近镜头", "0,0.6:0,-0.6"),
    ("approach the camera", "0,0.6:0,-0.6"),
    ("远离镜头", "0,-0.6:0,0.6"),
    ("walk away slowly", "0,-0.6:0,0.6"),
    ("在原地来回", "-1,0:1,0:-1,0"),
    ("踱步", "-1,0:1,0:-1,0"),
    ("绕着角色走了一圈", "-1,0.4:1,0.4:1,-0.4:-1,-0.4:-1,0.4"),
    ("circle walk", "-1,0.4:1,0.4:1,-0.4:-1,-0.4:-1,0.4"),
    ("静止不动", ""),
]


@pytest.mark.parametrize("text,expect", WALK_CASES)
def test_walk_rules(bg, text, expect):
    assert bg.parse_spec(text)["walk"] == expect


def test_walk_left_right_beats_other_walk_keywords(bg):
    """"左/右同时出现"优先级最高：压过"走近镜头"（原实现的 if 分支顺序）。"""
    assert bg.parse_spec("从左到右走近镜头")["walk"] == "-1,0:1,0"
    assert bg.parse_spec("从左到右来回走")["walk"] == "-1,0:1,0"


def test_walk_only_one_side_present_falls_through(bg):
    """只出现"左"（不出现"右"）不构成方向走位，应继续匹配其它走位关键词。"""
    assert bg.parse_spec("左侧走近镜头")["walk"] == "0,0.6:0,-0.6"
    assert bg.parse_spec("往左看一眼")["walk"] == ""


# ---------------------------------------------------------------- 规则表结构
def test_rule_tables_are_wellformed(bg):
    """规则表必须 (值, 关键词元组) 且非空 —— 防止手改时写坏结构。"""
    from agent.blocking import BlockingGenerator as BG
    for name in ("SHOT_RULES", "CAMERA_RULES", "HEIGHT_RULES"):
        rules = getattr(BG, name)
        assert rules, name
        for value, keywords in rules:
            assert isinstance(value, str) and value
            assert keywords and all(isinstance(k, str) and k for k in keywords)
    for name, keywords, walk in BG.WALK_RULES:
        assert name and keywords and walk
        # 走位串形如 "x,y:x,y[:x,y...]"，解析后每段必须是两个浮点数
        for seg in walk.split(":"):
            x, y = seg.split(",")
            float(x), float(y)


def test_walk_rule_names_unique():
    from agent.blocking import BlockingGenerator as BG
    names = [n for n, _k, _w in BG.WALK_RULES]
    assert len(names) == len(set(names))


def test_parse_spec_keys_stable(bg):
    """JSON 落盘依赖键集合稳定，别随手增删。"""
    assert set(bg.parse_spec("任意文本")) == {
        "characters", "props", "shot", "camera", "height", "walk"}
