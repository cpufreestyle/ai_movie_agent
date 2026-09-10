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


# ---------- comfyui_client（错误可读性） ----------

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
