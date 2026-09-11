#!/usr/bin/env python3
"""冒烟测试：在无 LLM / Blender / SkyReels / biliup 环境下验证各模块关键路径。

用法: python tests/smoke_test.py
任何断言失败都会打印 FAIL 并以非零退出码结束。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = 0
FAIL = 0

TMP = tempfile.mkdtemp(prefix="smoke_")

# 模板模式配置：禁用所有外部依赖，验证降级路径
CFG = {
    "project": {"title": "测试片", "theme": "a test theme about memory",
                "style": "anime style, cel-shaded", "scene_frames": 97},
    "engine": {"fps": 24, "skyreels_repo": "./skyreels_v2"},
    "llm": {"disabled": True},
    "publish": {"enabled": False, "binary": "definitely_not_exist_biliup_xyz"},
    "collector": {"enabled": True, "method": "requests", "urls": []},
    # backend=ragflow 但未配置 api/key → 应降级本地检索
    "knowledge": {"enabled": True, "backend": "ragflow", "ragflow": {"api": "", "api_key": ""}},
    "planner": {"enabled": True},
    "image_prompt": {"enabled": False},
    "polisher": {"enabled": True, "method": "llm"},
    "blender": {"enabled": False},
}


def check(name: str, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  PASS  {name}")
    except Exception:
        FAIL += 1
        print(f"  FAIL  {name}")
        traceback.print_exc()


# ---------- llmutil ----------

def test_extract_json():
    from agent.llmutil import extract_json
    assert json.loads(extract_json('{"a": 1}')) == {"a": 1}
    assert json.loads(extract_json('```json\n{"a": 2}\n```')) == {"a": 2}
    assert json.loads(extract_json('前缀 {"a": 3} 后缀')) == {"a": 3}


def test_make_client_disabled():
    from agent.llmutil import make_client
    assert make_client(CFG) is None


# ---------- Writer / Director（模板降级） ----------

def test_writer_story_bible():
    from agent.writer import Writer
    w = Writer(CFG)
    bible = w.story_bible()
    assert isinstance(bible, dict) and "title" in bible and "logline" in bible


def test_writer_next_beat():
    from agent.writer import Writer
    w = Writer(CFG)
    beat = w.next_beat({"setting": "a rainy city"}, [])
    assert isinstance(beat, dict) and "title" in beat and "description" in beat


def test_director_template_prompt():
    from agent.director import Director
    d = Director(CFG)
    p = d.beat_to_prompt({"description": "a figure in rain", "shot": "wide",
                          "camera": "slow pan", "mood": "tense"})
    assert isinstance(p, str) and len(p) <= 200 and "anime style" in p


# ---------- Knowledge ----------

def test_knowledge_local_retrieve():
    from agent.knowledge import Knowledge
    kb = Knowledge({"knowledge": {"backend": "local"}}, tempfile.mkdtemp())
    kb.ingest([{"url": "u1", "text": "apple banana cherry durian elderberry fig\n\n"
                                     "dog cat fish bird horse sheep goat duck"}])
    r = kb.retrieve("apple banana", k=2)
    assert r and "apple" in r[0]


def test_knowledge_ragflow_falls_back_to_local():
    kb_dir = tempfile.mkdtemp()
    from agent.knowledge import Knowledge
    kb = Knowledge(CFG, kb_dir)  # ragflow 未配置 → 推送应跳过、检索降级本地
    kb.ingest([{"url": "u1", "text": "hello world foo bar baz qux quux corge\n\n"
                                     "second chunk about entirely different topic"}])
    r = kb.retrieve("hello world", k=1)
    assert r and "hello" in r[0]


# ---------- Planner / ImagePrompt / Polisher（模板降级） ----------

def test_planner_template_concept():
    from agent.planner import Planner
    p = Planner(CFG, TMP)
    c = p.plan("test topic", [], None)
    assert isinstance(c.get("outline"), list) and len(c["outline"]) > 0
    assert "logline" in c


def test_planner_enrich_fallback():
    from agent.planner import Planner
    p = Planner(CFG, TMP)
    c = p.enrich({"logline": "x", "protagonist": "主角甲"}, "topic")
    assert c.get("characters") and c.get("three_act")


def test_image_prompt_template():
    from agent.image_prompt import ImagePrompt
    ip = ImagePrompt(CFG, TMP)
    out = ip.generate({"outline": ["a shot", "b shot"], "theme": "noir"})
    assert len(out) == 2 and all(isinstance(s, str) and s for s in out)


def test_polisher_passthrough_without_llm():
    from agent.polisher import Polisher
    po = Polisher(CFG, TMP)
    assert po.polish("原文保持不变") == "原文保持不变"
    assert po.polish("") == ""


# ---------- Publisher ----------

def test_publisher_fill_template():
    from agent.publisher import Publisher
    pub = Publisher(CFG, TMP)
    assert pub._fill("{title} · 第{n}集", title="X", n=3) == "X · 第3集"
    # 未知占位符保持原样
    assert "{unknown}" in pub._fill("{unknown} {n}", n=1)


def test_publisher_not_ready_and_errors():
    from agent.publisher import Publisher
    pub = Publisher(CFG, TMP)
    assert not pub.is_ready()
    res = pub.upload(os.path.join(TMP, "no_such.mp4"))
    assert res["ok"] is False and "不存在" in res["error"]
    v = os.path.join(TMP, "v.mp4")
    with open(v, "wb") as f:
        f.write(b"x")
    res = pub.upload(v)
    assert res["ok"] is False and "biliup" in res["error"]


def test_publisher_resolve_relative_binary():
    from agent.publisher import Publisher
    pub = Publisher({"publish": {"binary": "tools/blender_mcp.py"}}, TMP)
    p = pub._resolve_binary()
    assert os.path.isabs(p) and os.path.exists(p)


# ---------- BlockingGenerator ----------

def test_blocking_parse_spec_rules():
    from agent.blocking import BlockingGenerator
    bg = BlockingGenerator(CFG, tempfile.mkdtemp())
    s = bg.parse_spec("大全景，镜头缓慢平摇，低机位")
    assert s["shot"] == "wide" and s["camera"] == "pan" and s["height"] == "low"
    s2 = bg.parse_spec("角色特写镜头，环绕")
    assert s2["shot"] == "close" and s2["camera"] == "orbit"


def test_blocking_codegen_syntax_and_sig():
    """模板填充后的 bpy 代码必须是合法 Python（否则只有发到 Blender 才炸）。"""
    from agent.blocking import BlockingGenerator
    bg = BlockingGenerator(CFG, tempfile.mkdtemp())
    spec = {"characters": 2, "props": ["桌"], "shot": "wide",
            "camera": "dolly", "height": "low"}
    for mode in ("merged", "legacy"):
        for eng in ("eevee", "cycles"):
            compile(bg._build_block_code(spec, bg.out_dir, mode, eng),
                    "<blocking>", "exec")
    compile(bg._build_anim_code(spec, bg.out_dir, 12, "eevee"), "<blocking>", "exec")
    # 场景复用签名：几何相同则稳定，几何变化则变化
    assert bg._scene_sig(spec) == bg._scene_sig(dict(spec))
    assert bg._scene_sig(spec) != bg._scene_sig({**spec, "characters": 3})


def test_blocking_raises_instead_of_fake_paths():
    """Blender 不在时 render_block 必须抛 BlockingError，而不是返回不存在的路径。"""
    from agent.blocking import BlockingGenerator, BlockingError
    cfg = {**CFG, "blender": {"enabled": True, "retries": 0,
                              "timeout": 0.2, "port": 59999}}
    bg = BlockingGenerator(cfg, tempfile.mkdtemp())
    if bg.client.is_ready():
        return  # 本机 Blender MCP 恰在运行，跳过（本用例针对未就绪行为）
    try:
        bg.render_block({"characters": 1, "props": [], "shot": "medium",
                         "camera": "static", "height": "eye"}, bg.out_dir)
        raise AssertionError("Blender 未就绪时不应返回路径")
    except BlockingError:
        pass
    assert not bg.is_ready()  # blender.enabled=false


# ---------- concept_video ----------

def test_wrap_and_structure():
    from PIL import Image, ImageDraw
    from agent.concept_video import _wrap, _font, _build_structure
    img = Image.new("RGB", (320, 180))
    d = ImageDraw.Draw(img)
    f = _font(16)
    lines = _wrap(d, "这是一段需要被自动换行的长文本" * 5, f, 100)
    assert len(lines) > 1
    assert all(d.textlength(ln, font=f) <= 100 for ln in lines if ln)
    st = _build_structure("一句话创意", [])
    assert len(st) == 4 and "起" in st[0]


def test_render_cover():
    from agent.concept_video import render_cover
    out = os.path.join(TMP, "cover.png")
    render_cover({"title": "T", "logline": "l"}, [], out, width=180, height=240)
    assert os.path.exists(out) and os.path.getsize(out) > 0


def test_render_concept_video():
    from agent.concept_video import render_concept_video
    concept = {"title": "T", "logline": "l", "outline": ["shot a", "shot b"],
               "setting": "s", "protagonist": "p", "tone": "t"}
    out = os.path.join(TMP, "demo.mp4")
    r = render_concept_video(concept, [], out, fps=4, hold=0.25, xfade=0,
                             width=320, height=180)
    # mp4 成功编码，或退化为 PNG 序列目录；两者都算通过
    assert os.path.exists(r)


# ---------- Engine（未克隆 SkyReels 时的错误路径） ----------

def test_engine_not_ready_raises():
    from agent.engine import SkyReelsEngine
    eng = SkyReelsEngine(CFG, agent_root=ROOT)
    assert not eng.is_ready()
    try:
        eng.generate("x", os.path.join(TMP, "a.mp4"))
    except RuntimeError as e:
        assert "generate_video_df.py" in str(e)
    else:
        raise AssertionError("engine.generate 应抛出 RuntimeError")


# ---------- MovieAgent ----------

def test_agent_init_and_status():
    from agent.agent import MovieAgent
    tmp = tempfile.mkdtemp()
    ag = MovieAgent(CFG, tmp)
    st = ag.status()
    assert st["title"] == "测试片" and st["scene_count"] == 0 and st["film"] is None
    assert os.path.exists(os.path.join(tmp, "state.json"))


# ---------- CLI / WebUI ----------

def test_cli_help():
    r = subprocess.run([sys.executable, "cli.py", "--help"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "pipeline" in r.stdout


def test_cli_status():
    tmp = tempfile.mkdtemp()
    r = subprocess.run([sys.executable, "cli.py", "status", "--workdir", tmp],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["scene_count"] == 0


def test_webui_import():
    import webui
    assert webui.app is not None


# ---------- comfyui_post（超分/锐化共用实现） ----------

def test_comfyui_post_read_cfg():
    from agent import comfyui_post
    assert comfyui_post.read_post_cfg(None) == ("", 0.0)
    assert comfyui_post.read_post_cfg({"upscale_model": "  ", "sharpen": -1}) == ("", 0.0)
    assert comfyui_post.read_post_cfg(
        {"upscale_model": "4x-UltraSharp.pth", "sharpen": 0.2}) == ("4x-UltraSharp.pth", 0.2)


def test_comfyui_post_rewires_saver():
    from agent import comfyui_post
    wf = {"12": {"class_type": "VHS_VideoCombine",
                 "inputs": {"images": ["11", 0], "audio": ["11", 1]}}}
    ids = {"upscale_loader": "30", "upscale_apply": "31", "sharpen": "32"}
    out = comfyui_post.apply_post(
        wf, "12", upscale="4x.pth", sharpen=0.2, alloc=lambda n: ids[n])
    assert out == ["32", 0]
    # 保存节点的 images 已改指锐化输出；音频输入未被动过（避免音画不同步）
    assert wf["12"]["inputs"]["images"] == ["32", 0]
    assert wf["12"]["inputs"]["audio"] == ["11", 1]
    # 链路顺序：11(解码) -> 31(超分) -> 32(锐化)
    assert wf["31"]["inputs"]["images"] == ["11", 0]
    assert wf["32"]["inputs"]["image"] == ["31", 0]
    # ImageSharpen 的真实参数名是 sharpen_radius/sigma/alpha，写成 `sharpen` 会被 ComfyUI 拒绝
    assert wf["32"]["class_type"] == "ImageSharpen"
    assert wf["32"]["inputs"]["alpha"] == 0.2
    assert wf["32"]["inputs"]["sharpen_radius"] == 1
    assert "sharpen" not in wf["32"]["inputs"]


def test_comfyui_post_disabled_and_guards():
    from agent import comfyui_post
    wf = {"12": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["11", 0]}}}
    assert comfyui_post.apply_post(wf, "12", alloc=lambda n: n) is None  # 未启用 → 不动
    wf2 = {"12": {"class_type": "VHS_VideoCombine", "inputs": {"images": ["11", 0]}}}
    out = comfyui_post.apply_post(wf2, "12", sharpen=0.3, alloc=lambda n: "32")
    assert out == ["32", 0] and "30" not in wf2 and "31" not in wf2  # 只锐化不建超分节点
    wf3 = {"12": {"class_type": "VHS_VideoCombine", "inputs": {"images": "oops"}}}
    assert comfyui_post.apply_post(wf3, "12", sharpen=0.3, alloc=lambda n: "32") is None
    assert comfyui_post.find_video_saver({}) is None


# ---------- 引擎后处理接线（两个引擎都必须走同一实现） ----------

def test_mmh3_post_nodes_wired():
    from agent.mmh3_engine import MMH3Engine
    # 注意：配置键是 comfyui_mmH3（大写 H3），引擎另接受 minimax_h3 / comfyui_h3
    cfg = {"engine": {"comfyui_mmH3": {"resolution": "768x448", "num_frames": 56,
                                      "post": {"sharpen": 0.2}}}}
    wf = MMH3Engine(cfg)._build_workflow("a shot", 1, None)
    assert wf["12"]["inputs"]["images"] == ["32", 0]
    assert wf["32"]["class_type"] == "ImageSharpen"
    assert wf["32"]["inputs"]["alpha"] == 0.2
    assert "30" not in wf and "31" not in wf  # 未配超分模型 → 不建超分节点
    # 关闭后处理 → 保存节点直连解码输出
    off = {"engine": {"comfyui_mmH3": {"post": {"sharpen": 0}}}}
    assert MMH3Engine(off)._build_workflow("a shot", 1, None)["12"]["inputs"]["images"] == ["11", 0]


def test_mmh3_post_upscale_chain():
    from agent.mmh3_engine import MMH3Engine
    cfg = {"engine": {"comfyui_mmH3": {"post": {"upscale_model": "4x.pth", "sharpen": 0.2}}}}
    wf = MMH3Engine(cfg)._build_workflow("a shot", 1, None)
    assert wf["30"]["class_type"] == "UpscaleModelLoader"
    assert wf["31"]["inputs"]["images"] == ["11", 0]
    assert wf["32"]["inputs"]["image"] == ["31", 0]
    assert wf["12"]["inputs"]["images"] == ["32", 0]


def test_ltx_apply_post_rewires_saver():
    from agent.ltx_engine import LTXEngine
    eng = LTXEngine({"engine": {"comfyui_ltx": {"api": "", "post": {"sharpen": 0.2}}}})
    wf = {"5501": {"class_type": "VHS_VideoCombine",
                   "inputs": {"images": ["5500", 0], "audio": ["5499", 1]}}}
    eng._apply_post(wf)
    # 用户工作流 ID 任意 → 动态分配高位空闲 ID
    assert wf["5501"]["inputs"]["images"] == ["90003", 0]
    assert wf["5501"]["inputs"]["audio"] == ["5499", 1]
    assert wf["90003"]["class_type"] == "ImageSharpen"
    assert wf["90003"]["inputs"]["alpha"] == 0.2


# ---------- agent/align（旁白 / 字幕强制对齐） ----------

def test_align_cues_follow_speech():
    from agent import align
    assert isinstance(align.is_available(), bool)   # 没装 faster-whisper 也只是 False
    # 语音 0.4s 开始、1.8s 结束；段起点 10s、无变速、delay 0.25
    cues = align.cues_for_lines([{"start": 0.4, "end": 1.8}], [10.0],
                                delay=0.25, total=20.0)
    (s, e), = cues
    assert abs(s - 10.65) < 1e-6              # 10.0 + 0.25 + 0.4
    assert abs(e - (10.25 + 1.8 + 0.10)) < 1e-6


def test_align_cues_compensate_atempo():
    from agent import align
    # atempo=2 把语音压缩一半：原 1.0~3.0 的语音在成片里落在 0.5~1.5
    cues = align.cues_for_lines([{"start": 1.0, "end": 3.0}], [0.0],
                                tempo=[2.0], delay=0.0, total=10.0)
    (s, e), = cues
    assert abs(s - 0.5) < 1e-6
    assert abs(e - (1.5 + 0.10)) < 1e-6


def test_align_cues_fallback_and_clamp():
    from agent import align
    # 取不到语音时间戳 → 占满该段槽位，且不得侵入下一段起点
    cues = align.cues_for_lines([None, None], [0.0, 4.0], total=8.0)
    assert len(cues) == 2
    assert cues[0][1] <= 4.0 - 0.05 + 1e-9
    assert cues[1][1] <= 8.0 - 0.05 + 1e-9
    assert cues[0][0] < cues[0][1]


def test_align_cue_min_duration_and_srt():
    from agent import align
    # 极短语音也至少显示 min_dur，避免字幕一闪而过
    s, e = align.cue(1.0, 1.05, min_dur=0.6)
    assert e - s >= 0.6 - 1e-9
    # SRT 时间戳格式
    assert align._fmt_ts(12.345) == "00:00:12,345"
    p = os.path.join(TMP, "align.srt")
    out = align.write_srt([(0.0, 1.0), (1.5, 3.0)], ["first", "second"], p)
    assert out == p and os.path.exists(p)
    body = open(p, encoding="utf-8").read()
    assert "00:00:00,000 --> 00:00:01,000" in body
    assert "00:00:01,500 --> 00:00:03,000" in body
    assert body.startswith("1\n") and "second" in body


# ---------- agent/prompting（负向提示词库） ----------

def test_prompting_negative_presets_and_priority():
    from agent import prompting
    for name in ("quality", "identity", "anime"):
        assert prompting.NEGATIVE_PRESETS[name]
    # 空配置 → 默认 quality 预设
    assert prompting.resolve_negative({}) == prompting.NEGATIVE_PRESETS["quality"]
    # config.prompting.negative 写预设名
    assert prompting.resolve_negative({"prompting": {"negative": "identity"}}) \
        == prompting.NEGATIVE_PRESETS["identity"]
    # 写整串（非预设名）→ 按字面处理
    assert prompting.resolve_negative({"prompting": {"negative": "no hands, no text"}}) \
        == "no hands, no text"
    # 引擎级显式配置最具体，优先
    assert prompting.resolve_negative({"prompting": {"negative": "quality"}},
                                      engine_negative="explicit one") == "explicit one"
    # extra 追加且按逗号去重（保序）
    r = prompting.resolve_negative({"prompting": {"negative": "no text"}},
                                   extra="no text, extra thing")
    assert r == "no text, extra thing"


def test_prompting_supports_negative():
    from agent import prompting
    assert prompting.supports_negative("ltx") is True
    assert prompting.supports_negative("comfyui_ltx") is True
    # H3 走 flow matching / shift，BasicGuider 无 negative 端 → 配了也不生效
    for name in ("mmh3", "comfyui_mmh3", "minimax_h3", "h3"):
        assert prompting.supports_negative(name) is False


def test_ltx_engine_uses_negative_library():
    from agent.ltx_engine import LTXEngine
    # 引擎未配 negative → 回落到 quality 预设（不能是空串）
    eng = LTXEngine({"engine": {"comfyui_ltx": {"api": ""}}})
    assert eng.negative and "deformed face" in eng.negative
    # 引擎级显式配置优先
    eng2 = LTXEngine({"engine": {"comfyui_ltx": {"api": "", "negative": "custom neg"}}})
    assert eng2.negative == "custom neg"


def test_resolve_anchor_auto_and_explicit():
    import shutil
    from PIL import Image
    import run_series as rs
    assert rs.resolve_anchor("") == ("", "")          # 留空 = 不锚定
    p, note = rs.resolve_anchor("no/such/anchor.png")
    assert p == "" and "不存在" in note               # 显式路径不存在 → 给原因
    # auto：把 ROOT 临时指向一个自制角色卡目录
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "outputs", "anchor"))
    card = os.path.join(tmp, "outputs", "anchor", "mira_anchor.png")
    Image.new("RGB", (8, 8), (0, 0, 0)).save(card)
    old_root = rs.ROOT
    try:
        rs.ROOT = tmp
        p2, note2 = rs.resolve_anchor("auto")
        assert p2 == card and "mira_anchor.png" in note2
    finally:
        rs.ROOT = old_root
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- comfyui_client（错误可读性） ----------

# ---------- agent/qa（逐镜质检） ----------

def _qa_write_video(path: str, frames: int = 24, size=(128, 72), kind: str = "moving"):
    """写合成测试视频：moving=有运动且有高频细节；static_black=全黑且完全静止。"""
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 12.0, size)
    assert writer.isOpened(), "cv2.VideoWriter 打不开（环境缺 mp4v 编码器）"
    try:
        for i in range(frames):
            if kind == "static_black":
                frame = np.zeros((size[1], size[0], 3), dtype="uint8")
            else:
                frame = np.full((size[1], size[0], 3), 40, dtype="uint8")
                # 移动的亮块（产生运动）+ 边缘（产生非零拉普拉斯方差）
                x = 4 + (i * 3) % (size[0] - 20)
                cv2.rectangle(frame, (x, 12), (x + 14, 40), (240, 240, 240), -1)
                cv2.line(frame, (0, 60 + (i % 3)), (size[0], 60 - (i % 3)), (200, 60, 60), 1)
            writer.write(frame)
    finally:
        writer.release()
    return path


def test_qa_score_moving_video():
    from agent import qa
    p = _qa_write_video(os.path.join(TMP, "qa_moving.mp4"), kind="moving")
    sc = qa.score_video(p, {"sample_frames": 6})
    assert sc["frames"] > 0
    assert sc["motion"] > 0.5      # 确实有运动
    assert sc["brightness"] > 8    # 不是全黑
    ok, reasons = qa.evaluate(sc, qa.DEFAULTS)
    assert ok, reasons


def test_qa_detects_black_and_static():
    from agent import qa
    p = _qa_write_video(os.path.join(TMP, "qa_black.mp4"), kind="static_black")
    sc = qa.score_video(p, {"sample_frames": 6})
    ok, reasons = qa.evaluate(sc, qa.DEFAULTS)
    assert not ok
    assert any("全黑" in r or "过暗" in r for r in reasons)
    assert any("静帧" in r for r in reasons)


def test_qa_policy_thresholds_and_summarize():
    from agent import qa
    pol = qa.load_policy(None)
    assert pol["enabled"] is True and pol["max_rerolls"] == 1
    assert qa.load_policy({"qa": {"enabled": False, "max_rerolls": 3}})["max_rerolls"] == 3
    # 阈值全设 0 = 各项都不检查 → 明确不判死
    ok, reasons = qa.evaluate({"sharpness": 0, "motion": 0, "brightness": 0},
                              {"min_sharpness": 0, "min_motion": 0,
                               "min_brightness": 0, "max_brightness": 0})
    assert ok and reasons == []
    # 超阈值要能准确指出原因（人脸类指标未开启时不参与判定）
    ok2, r2 = qa.evaluate({"sharpness": 1, "motion": 9, "brightness": 50},
                          {"min_sharpness": 5, "max_motion": 5, "face_check": False})
    assert not ok2 and any("偏糊" in x for x in r2) and any("抖动" in x for x in r2)
    s = qa.summarize([{"sharpness": 10, "motion": 2}, {"sharpness": 30, "motion": 4}])
    assert s["count"] == 2 and s["sharpness"]["min"] == 10 and s["sharpness"]["max"] == 30


def test_comfyui_client_error_messages():
    from tools.comfyui_client import (ComfyUIError, ComfyUITimeout,
                                      _fmt_node_errors, _fmt_status_error)
    assert issubclass(ComfyUITimeout, ComfyUIError)
    assert issubclass(ComfyUIError, RuntimeError)  # 兼容既有 except RuntimeError 的调用方
    # /prompt 被拒：消息里带节点号与原因
    msg = _fmt_node_errors({"70": {"errors": [
        {"type": "invalid_input", "message": "mat1 and mat2 shapes cannot be multiplied"}]}})
    assert "节点 70" in msg and "cannot be multiplied" in msg
    assert _fmt_node_errors(None) == ""
    # 执行期报错：带节点号 + 节点类型 + 异常类型
    m = _fmt_status_error({"prompt_id": "p1", "status": {
        "status_str": "error",
        "messages": [["execution_error", {
            "node_id": 70, "node_type": "KSampler",
            "exception_type": "RuntimeError", "exception_message": "boom"}]]}})
    assert "节点 70" in m and "KSampler" in m and "RuntimeError: boom" in m and "p1" in m


# ---------- agent/record（生成参数全量落盘，可复现） ----------

class _MMH3LikeEngine:
    resolution = "768x448"
    num_frames = 56
    fps = 24
    steps = 30
    lora = "minimax_h3_fl2v_turbo.safetensors"
    negative = "deformed face, extra limbs"
    two_pass = False
    block_cache = True


def test_record_collect_and_roundtrip():
    import tempfile
    from agent import record
    eng = _MMH3LikeEngine()
    p = record.collect(eng, prompt="a shot of the woman", seed=123, attempt=0,
                       image="c:/x/anchor.png", ref_images=["c:/a/mira_1.png"],
                       ref_video="c:/v/blocking.mp4",
                       style_anchor="c:/b/style.png",
                       qa_policy={"enabled": True, "max_rerolls": 1})
    assert p["engine"] == "mmh3"
    assert p["resolution"] == "768x448" and p["steps"] == 30
    assert p["seed"] == 123 and p["attempt"] == 0
    assert p["image"] == "anchor.png" and p["ref_images"] == ["mira_1.png"]
    assert p["ref_video"] == "blocking.mp4"          # 白模走位参考视频也要可复现
    assert p["style_anchor"] == "style.png"
    assert p["qa"]["enabled"] is True
    eng.lora = None
    p2 = record.collect(eng, prompt="x", seed=1, attempt=0)
    assert "lora" not in p2
    wd = tempfile.mkdtemp()
    record.save(wd, "ep1_shot1", p)
    data = record.load(wd)
    assert data["ep1_shot1"]["seed"] == 123
    record.save(wd, "ep1_shot2", p2)
    assert len(record.load(wd)) == 2


# ---------- agent/ab（A/B 工作台：同镜不同参数并排） ----------

def test_ab_build_rows_and_param_diff():
    import json
    import os
    import tempfile
    from agent import ab, record
    d = tempfile.mkdtemp()
    record.save(d, "ep1_shot1",
                record.collect(_MMH3LikeEngine(), prompt="a", seed=123, attempt=0))
    record.save(d, "ep1_shot2",
                record.collect(_MMH3LikeEngine(), prompt="b", seed=7, attempt=0))
    man = {"ep1_shot1": os.path.join(d, "ep1_shot1.mp4"),
           "ep1_shot2": os.path.join(d, "ep1_shot2.mp4")}
    json.dump(man, open(os.path.join(d, "series_manifest.json"), "w", encoding="utf-8"))
    open(man["ep1_shot1"], "wb").close()   # 空文件：cv2 读不到帧，QA 应留 0 不崩
    rows = ab.build_rows(d)
    keys = [r["key"] for r in rows]
    assert "ep1_shot1" in keys and "ep1_shot2" in keys
    r1 = next(r for r in rows if r["key"] == "ep1_shot1")
    assert r1["params"]["seed"] == 123 and r1["params"]["steps"] == 30
    assert r1["key"] in ab.render_single(rows)        # 渲染不崩且含镜头 key
    assert r1["key"] in ab.render_markdown(rows)
    diff = ab.param_diff({"seed": 1, "steps": 30}, {"seed": 2, "steps": 30})
    assert ("seed", 1, 2) in diff
    assert all(f != "steps" for f, _, _ in diff)       # 相同字段不应出现在差异里


def test_ab_compare_runs_and_seed_from_name():
    import json
    import os
    import tempfile
    from agent import ab, record
    da = tempfile.mkdtemp()
    db = tempfile.mkdtemp()
    record.save(da, "ep1_shot1",
                record.collect(_MMH3LikeEngine(), prompt="a", seed=123, attempt=0))
    record.save(db, "ep1_shot1",
                record.collect(_MMH3LikeEngine(), prompt="a", seed=200, attempt=0))
    for d in (da, db):
        json.dump({"ep1_shot1": os.path.join(d, "ep1_shot1.mp4")},
                  open(os.path.join(d, "series_manifest.json"), "w", encoding="utf-8"))
    rows = ab.compare_runs(da, db)
    assert len(rows) == 1 and rows[0]["key"] == "ep1_shot1"
    diff = ab.param_diff(rows[0]["a"]["params"], rows[0]["b"]["params"])
    assert ("seed", 123, 200) in diff                   # 同镜两次 seed 不同
    assert ab._seed_from_name("ep1_shot1_s77") == 77    # 变体文件名含 seed 可解析
    assert ab._seed_from_name("ep1_shot1") is None
    assert "ep1_shot1" in ab.render_runs(rows)          # 渲染不崩


# ---------- 白模 -> 视频：灰模动画帧数对齐 ----------

def test_blocking_anim_frames_auto_aligns_and_floors():
    """灰模动画帧数：auto 对齐出片帧数，并按 H3 参考视频 2s 下限兜底；显式整数尊重原值。"""
    from agent.blocking import BlockingGenerator

    def _mk(eng: dict, frames):
        c = {**CFG, "engine": {**CFG["engine"], **eng}}
        return BlockingGenerator({**c, "blender": {"enabled": True,
                                                   "anim_frames": frames}},
                                 tempfile.mkdtemp())

    bg = _mk({"fps": 24, "backend": "comfyui_mmH3", "comfyui_mmH3": {"num_frames": 56}},
             "auto")
    assert bg.fps == 24
    assert bg.anim_frames == 56          # 对齐出片 56 帧（2.33s）
    assert _mk({"fps": 24, "comfyui_mmH3": {"num_frames": 22}},
               "auto").anim_frames == 48  # 出片仅 0.92s → 被 2s 下限抬到 48
    assert _mk({"fps": 24}, "auto").anim_frames == 48   # 拿不到出片帧数 → 兜底 2s
    assert _mk({"fps": 24, "comfyui_mmH3": {"num_frames": 56}},
               24).anim_frames == 24      # 显式整数尊重原值（过短由 agent 侧预检拦下）


def test_ffmpeg_and_probe_duration_fallbacks():
    """ffmpeg/ffprobe 不在 PATH 时也要能定位与探测时长。

    本机只有 imageio-ffmpeg 提供的 ffmpeg、没有 ffprobe；若只查 PATH，
    白模灰模动画会静默跳过（ref_video 永远不可用），时长也会恒为 0。
    """
    from agent import editor
    from agent.blocking import BlockingGenerator
    ff = BlockingGenerator._ffmpeg()
    assert ff, "未定位到 ffmpeg（PATH 与 imageio-ffmpeg 均失败）"
    assert os.path.exists(ff), ff

    # 用该 ffmpeg 造一段 1s 测试片，验证 probe_duration 的兜底解析（无 ffprobe 也能测）
    p = os.path.join(tempfile.mkdtemp(), "probe.mp4")
    r = subprocess.run([ff, "-y", "-v", "error", "-f", "lavfi",
                        "-i", "color=c=black:s=64x64:r=24:d=1",
                        "-pix_fmt", "yuv420p", "-frames:v", "24", p],
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not os.path.exists(p):
        return                        # 该 ffmpeg 构建缺 lavfi，跳过时长断言
    assert abs(editor.Editor().probe_duration(p) - 1.0) < 0.2


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    print(f"== smoke_test: {len(tests)} cases, workdir={ROOT}")
    for fn in tests:
        check(fn.__name__, fn)
    print(f"== 结果: {PASS} pass, {FAIL} fail")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
