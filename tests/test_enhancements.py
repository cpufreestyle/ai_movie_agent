"""六项增强的单元测试（#3 #4 #6 #7 #8 #11），不依赖 GPU / ffmpeg / piper / biliup。

全部走优雅降级路径：缺二进制时只验证「能力存在 + 降级不崩」。
"""
from __future__ import annotations

import json
import os
import tempfile


# ---------- #11 投稿前体检 ----------
def test_preflight_flags_problems_without_external_bins():
    from agent import preflight as pf
    # 视频不存在 -> 错误；标题超长 -> 错误；标签过多 -> 错误
    res = pf.check(
        video="/no/such/file.mp4",
        title="x" * 81,
        tags=["t"] * 11,
    )
    assert res["ok"] is False
    assert any("视频文件不存在" in e for e in res["errors"])
    assert any("标题超长" in e for e in res["errors"])
    assert any("标签数" in e for e in res["errors"])
    # 合法输入（不传 video 时不报视频错误，标题/标签均合规）-> ok
    ok = pf.check(title="《片》 · 第一集", tags=["AI电影", "AIGC"])
    assert ok["ok"] is True
    assert not any("标题" in e or "标签" in e for e in ok["errors"])


# ---------- #4 风格预设库 ----------
def test_style_presets_render_and_lookup():
    from agent import style_presets as sp
    names = sp.list_presets()
    assert "anime" in names and len(names) >= 6
    p = sp.get("anime")
    assert "anime style" in p["prompt"]
    assert "anime style" in sp.render("anime")
    assert "a cat" in sp.render("anime", "a cat")
    assert sp.negative_of("noir")  # 负向非空
    assert sp.get("nope") is None   # 未知预设返回 None（与 dict.get 一致）
    try:
        sp.apply("nope")
        raise AssertionError("apply 应抛 KeyError")
    except KeyError:
        pass


# ---------- #7 自动配乐 + 旁白 ducking ----------
def test_audio_mix_selection_and_readiness():
    from agent import audio_mix as am
    assert isinstance(am.is_ready(), bool)          # 不崩，返回 bool
    d = tempfile.mkdtemp()
    assert am.list_bgm(d) == []                     # 空目录
    assert am.select_bgm(d) is None                 # 无候选
    # 有候选时确定性返回一个存在的文件
    open(os.path.join(d, "calm.mp3"), "wb").close()
    open(os.path.join(d, "epic.wav"), "wb").close()
    chosen = am.select_bgm(d)
    assert chosen and os.path.exists(chosen)


# ---------- #6 本地 TTS 稳定音色 ----------
def test_tts_backends_and_voice_config_roundtrip():
    from agent import tts as tts_mod
    assert tts_mod.list_backends() == ["piper", "edge-tts", "coqui"]
    assert isinstance(tts_mod.is_ready("piper"), bool)
    # 无可用后端时优雅返回错误（不抛异常）
    res = tts_mod.tts("你好", os.path.join(tempfile.mkdtemp(), "x.wav"))
    assert isinstance(res, dict) and "ok" in res
    # 音色配置持久化往返
    cfg_path = os.path.join(tempfile.mkdtemp(), "tv.json")
    tts_mod.save_voice_config("zh-CN-XiaoxiaoNeural", "edge-tts", path=cfg_path)
    loaded = tts_mod.load_voice_config(path=cfg_path)
    assert loaded == {"voice": "zh-CN-XiaoxiaoNeural", "backend": "edge-tts"}


# ---------- #3 角色卡自动生成 ----------
def test_character_card_build_and_generate():
    from agent import character_card as cc
    bible = {"characters": [
        {"name": "Mira", "role": "侦探", "visual": "蓝发",
         "description": "记忆猎人"},
        "Echo",
    ]}
    cards = cc.build_cards(bible, style="anime")
    assert len(cards) == 2
    assert cards[0]["name"] == "Mira" and cards[0]["role"] == "侦探"
    assert "character design sheet of Mira" in cards[0]["image_prompt"]
    assert cards[1]["name"] == "Echo" and cards[1]["ref_image"] is None

    # engine=None：只出 JSON，ref_image 全 None
    wd = tempfile.mkdtemp()
    res = cc.generate(bible, wd, style="anime", engine=None)
    assert os.path.exists(res["manifest"])
    assert all(c["ref_image"] is None for c in res["cards"])

    # 假引擎就绪：尝试出图并回填 ref_image
    class FakeEngine:
        def is_ready(self):
            return True

        def generate(self, prompt, out_path, seed=0):
            open(out_path, "wb").close()
            return out_path

    res2 = cc.generate(bible, wd, style="anime", engine=FakeEngine())
    assert any(c["ref_image"] for c in res2["cards"])


# ---------- #8 WebUI 时间轴 ----------
def test_timeline_build_and_shape():
    from agent import timeline as _tl
    d = _tl.build_timeline()
    assert isinstance(d, dict) and "shots" in d
    for s in d["shots"]:
        assert {"key", "enabled", "in_point", "out_point", "order"} <= set(s.keys())


def test_timeline_concat_export():
    from agent import timeline as _tl
    d = tempfile.mkdtemp()
    s1 = os.path.join(d, "s1.mp4"); open(s1, "wb").close()
    s2 = os.path.join(d, "s2.mp4"); open(s2, "wb").close()
    json.dump({"s1": s1, "s2": s2},
              open(os.path.join(d, "series_manifest.json"), "w", encoding="utf-8"))
    tl = _tl.build_timeline(d)
    tl["shots"][1]["enabled"] = False      # s2 禁用
    tl["shots"][0]["in_point"] = 1.5        # s1 裁剪入点
    out = os.path.join(d, "movie.mp4")
    cmd = _tl.export_concat(tl, d, out)
    assert "ffmpeg" in cmd and "concat" in cmd
    txt = out + ".concat.txt"
    assert os.path.exists(txt)
    content = open(txt, encoding="utf-8").read()
    assert "s1.mp4" in content and "inpoint 1.5" in content
    assert "s2.mp4" not in content          # 禁用镜头不进拼接
