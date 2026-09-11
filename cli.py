#!/usr/bin/env python3
"""AI 电影 Agent 命令行入口。

示例：
  # 持续创作（自动续写，直到 Ctrl-C）
  python cli.py run --continuous

  # 只生成 5 镜后停止
  python cli.py run --max-scenes 5

  # 交互式逐镜确认
  python cli.py run --no-continuous --interactive

  # 查看当前影片状态
  python cli.py status

  # 初始化世界观（重新生成 story bible）
  python cli.py init
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import yaml
from config_env import apply_env_overrides

HERE = os.path.dirname(os.path.abspath(__file__))


def ensure(cond, msg: str):
    if not cond:
        print(f"[错误] {msg}")
        sys.exit(1)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return apply_env_overrides(yaml.safe_load(f) or {})


def main():
    ap = argparse.ArgumentParser(description="本地持续创作的 AI 电影 Agent (SkyReels-V2)")
    ap.add_argument("--config", default=os.path.join(HERE, "config.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="开始创作")
    p_run.add_argument("--continuous", action="store_true",
                       help="持续创作，直到 Ctrl-C（默认）")
    p_run.add_argument("--no-continuous", dest="continuous", action="store_false",
                       help="只生成一镜")
    p_run.set_defaults(continuous=True)
    p_run.add_argument("--max-scenes", type=int, default=None)
    p_run.add_argument("--interactive", action="store_true",
                       help="每镜前询问是否继续")
    p_run.add_argument("--seed", type=int, default=None)
    p_run.add_argument("--workdir", default=os.path.join(HERE, "outputs"))

    p_pipe = sub.add_parser("pipeline", help="跑完整 A–H 流水线（采集→发布）")
    p_pipe.add_argument("--topic", default=None, help="主题；留空用 config.project.theme")
    p_pipe.add_argument("--continuous", action="store_true", help="持续创作直到 Ctrl-C（默认）")
    p_pipe.add_argument("--no-continuous", dest="continuous", action="store_false",
                        help="只生成一镜")
    p_pipe.set_defaults(continuous=True)
    p_pipe.add_argument("--max-scenes", type=int, default=None)
    p_pipe.add_argument("--interactive", action="store_true", help="每镜前询问是否继续")
    p_pipe.add_argument("--seed", type=int, default=None)
    p_pipe.add_argument("--no-research", dest="do_research", action="store_false",
                        help="跳过 A–D 素材/企划阶段，直接用已有世界观")
    p_pipe.set_defaults(do_research=True)
    p_pipe.add_argument("--workdir", default=os.path.join(HERE, "outputs"))

    sub.add_parser("status", help="查看当前影片状态").add_argument(
        "--workdir", default=os.path.join(HERE, "outputs"))
    sub.add_parser("init", help="重新生成世界观").add_argument(
        "--workdir", default=os.path.join(HERE, "outputs"))

    p_pub = sub.add_parser("publish", help="把影片投稿到 B 站（需先 biliup login）")
    p_pub.add_argument("--video", default=os.path.join(HERE, "outputs", "movie_final.mp4"),
                       help="要投稿的影片路径（默认 outputs/movie_final.mp4）")
    p_pub.add_argument("--workdir", default=os.path.join(HERE, "outputs"))

    p_pc = sub.add_parser("publish-concept",
                          help="把创意/规划渲染成视频 demo（biliup 就绪则投稿到 B 站）")
    p_pc.add_argument("--workdir", default=os.path.join(HERE, "outputs"))
    p_pc.add_argument("--submit", action="store_true",
                      help="biliup 就绪时直接投稿（默认只生成视频素材）")
    p_pc.add_argument("--bgm", action="store_true",
                      help="混入 BGM（需 ffmpeg，否则仅做卡片转场）")
    p_pc.add_argument("--xfade", type=float, default=0.4,
                      help="卡片间交叉淡入转场时长(秒)，默认 0.4")
    p_pc.add_argument("--replace", default=None, metavar="BVID",
                      help="投新片前先下架该 BVID 旧视频（用于替换旧投稿）")

    p_en = sub.add_parser("enrich-bible",
                          help="C+ 阶段：充实 bible（人物小传/视觉风格/三幕）并重渲染 demo")
    p_en.add_argument("--workdir", default=os.path.join(HERE, "outputs"))
    p_en.add_argument("--bgm", action="store_true",
                      help="重新渲染的 demo 混入 BGM（需 ffmpeg）")
    p_en.add_argument("--xfade", type=float, default=0.4,
                      help="卡片间交叉淡入转场时长(秒)，默认 0.4")

    p_web = sub.add_parser("webui", help="启动本地 WebUI（浏览器可视化操作 Agent）")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)

    p_bl = sub.add_parser("blender",
                          help="渲染 Blender 白模（需本地 Blender + Blender MCP 插件并已启动 MCP Server）")
    p_bl.add_argument("--beat", default="一个角色的中景，镜头缓慢平摇",
                      help="分镜描述文本（用于解析机位/运镜/角色）")
    p_bl.add_argument("--mode", default="block",
                      choices=["block", "previs", "control", "anim"],
                      help="block=全套(预视+线框+深度+法线); previs=仅预视; control=控制图; anim=灰模动画")
    p_bl.add_argument("--host", default=None, help="Blender MCP 端口主机（默认 127.0.0.1）")
    p_bl.add_argument("--port", type=int, default=None, help="Blender MCP 端口（默认 9876）")
    p_bl.add_argument("--workdir", default=os.path.join(HERE, "outputs"))

    p_ltx = sub.add_parser("ltx",
                           help="用 ComfyUI + LTX-2.5(NVFP4) 生成短视频片段（需本地 ComfyUI + LTX-2.5 节点）")
    p_ltx.add_argument("--prompt", default="一个赛博都市的远景，镜头缓慢推进",
                       help="视频提示词（默认英文；可由本地 LLM 生成）")
    p_ltx.add_argument("--image", default=None, help="可选起始帧（图生视频 I2V）")
    p_ltx.add_argument("--frames", type=int, default=None, help="生成帧数（覆盖 config）")
    p_ltx.add_argument("--out", default=os.path.join(HERE, "outputs", "ltx_clip.mp4"))
    p_ltx.add_argument("--workdir", default=os.path.join(HERE, "outputs"))

    p_h3 = sub.add_parser("mmh3",
                          help="用 ComfyUI + MiniMax H3(Turbo 4 步) 生成带原生立体声的短视频")
    p_h3.add_argument("--prompt", default="一个赛博都市的远景，镜头缓慢推进",
                      help="视频提示词；H3 会同时用它生成画面与音效")
    p_h3.add_argument("--image", default=None, help="可选首帧（I2VA 图生视频）")
    p_h3.add_argument("--frames", type=int, default=None,
                      help="帧数（自动吸附到 H3 的 17n+5 网格）")
    p_h3.add_argument("--resolution", default=None,
                      help="如 768x448（自动修正为 32 的倍数）")
    p_h3.add_argument("--no-turbo", action="store_true",
                      help="关闭 Turbo LoRA，改用 30 步基线（画质对比用）")
    p_h3.add_argument("--out", default=os.path.join(HERE, "outputs", "mmh3_clip.mp4"))
    p_h3.add_argument("--workdir", default=os.path.join(HERE, "outputs"))

    p_ab = sub.add_parser(
        "ab", help="A/B 工作台：同镜不同参数并排对比（需 gen_params.json + series_manifest.json）")
    p_ab.add_argument("dirs", nargs="+",
                      help="work 目录（1 个=本目录多镜总览；2 个=两次生成逐镜对比）")
    p_ab.add_argument("--key", default=None,
                      help="聚焦某一镜（列出其变体 / 两次生成的参数差异）")
    p_ab.add_argument("--markdown", action="store_true", help="输出 Markdown 表格")
    p_ab.add_argument("--grid", default=None, metavar="OUT.png",
                      help="把聚焦镜的变体视频拼成联系表(contact sheet)")

    p_pf = sub.add_parser("preflight", help="投稿前体检：B 站元数据/封面/时长静态校验")
    p_pf.add_argument("--video", default=None)
    p_pf.add_argument("--title", default=None)
    p_pf.add_argument("--tags", default=None, help="逗号分隔标签")
    p_pf.add_argument("--cover", default=None)
    p_pf.add_argument("--dynamic", default=None)
    p_pf.add_argument("--workdir", default=os.path.join(HERE, "outputs"))
    p_pf.add_argument("--json", action="store_true", help="输出 JSON")

    p_st = sub.add_parser("style", help="风格预设库：列出/查看/应用视觉风格预设")
    p_st.add_argument("--list", action="store_true", help="列出全部预设")
    p_st.add_argument("--show", default=None, metavar="NAME", help="查看某预设")
    p_st.add_argument("--apply", default=None, metavar="NAME", help="应用某预设")
    p_st.add_argument("--base", default="", help="基础提示词前缀")
    p_st.add_argument("--out", default=None, help="把拼接后提示词写到文件")

    p_mx = sub.add_parser("mix", help="自动配乐 + 旁白 ducking（需 ffmpeg）")
    p_mx.add_argument("--video", required=True)
    p_mx.add_argument("--bgm-dir", default=os.path.join(HERE, "assets", "bgm"))
    p_mx.add_argument("--narration", default=None, help="旁白音轨（做侧链避让）")
    p_mx.add_argument("--out", required=True)
    p_mx.add_argument("--bgm-gain", type=int, default=-20)
    p_mx.add_argument("--mood", default=None, help="按文件名含该词筛选 BGM")

    p_tts = sub.add_parser("tts", help="本地 TTS 稳定音色：合成旁白")
    p_tts.add_argument("--text", required=True)
    p_tts.add_argument("--out", required=True)
    p_tts.add_argument("--voice", default=None)
    p_tts.add_argument("--backend", default=None, help="piper/edge-tts/coqui（缺省自动选）")
    p_tts.add_argument("--save-voice", action="store_true", help="把本次 voice/backend 存为默认音色")

    p_cc = sub.add_parser("charcard", help="角色卡自动生成（series_bible 驱动）")
    p_cc.add_argument("--bible", default=None, help="bible/state.json 路径（缺省读 outputs/state.json）")
    p_cc.add_argument("--workdir", default=os.path.join(HERE, "outputs"))
    p_cc.add_argument("--style", default="anime")
    p_cc.add_argument("--engine", default=None, help="skyreels/ltx/mmh3（缺省只出 JSON）")

    p_tl = sub.add_parser("timeline", help="WebUI 时间轴：查看/重建/导出镜头时间轴")
    p_tl.add_argument("--workdir", default=os.path.join(HERE, "outputs"))
    p_tl.add_argument("--build", action="store_true", help="从 series_manifest 重建时间轴")
    p_tl.add_argument("--export", default=None, metavar="OUT.json")
    p_tl.add_argument("--print", dest="do_print", action="store_true", help="打印当前时间轴")

    args = ap.parse_args()
    config = load_config(args.config)

    from agent.agent import MovieAgent

    if args.cmd == "run":
        agent = MovieAgent(config, args.workdir)
        agent.run(continuous=args.continuous, max_scenes=args.max_scenes,
                  auto=not args.interactive, seed=args.seed, do_research=False)
    elif args.cmd == "pipeline":
        agent = MovieAgent(config, args.workdir)
        agent.run(continuous=args.continuous, max_scenes=args.max_scenes,
                  auto=not args.interactive, seed=args.seed,
                  topic=args.topic, do_research=args.do_research)
    elif args.cmd == "status":
        agent = MovieAgent(config, args.workdir)
        print(json.dumps(agent.status(), ensure_ascii=False, indent=2))
    elif args.cmd == "init":
        if os.path.exists(os.path.join(args.workdir, "state.json")):
            os.remove(os.path.join(args.workdir, "state.json"))
        agent = MovieAgent(config, args.workdir)
        print("世界观已重置:", json.dumps(agent.state["bible"], ensure_ascii=False, indent=2))
    elif args.cmd == "enrich-bible":
        agent = MovieAgent(config, args.workdir)
        res = agent.enrich_bible(xfade=args.xfade, bgm=args.bgm)
        print("[enrich-bible] 已充实 bible 并重渲染 demo:")
        print(f"  video: {res['video']}")
        print(f"  cover: {res['cover']}")
        b = res["bible"]
        print(f"  characters: {len(b.get('characters', []))} 个 · "
              f"visual_style: {'有' if b.get('visual_style') else '无'} · "
              f"three_act: {len(b.get('three_act', []))} 段")
    elif args.cmd == "webui":
        from webui import main as webui_main
        sys.argv = ["webui", "--host", args.host, "--port", str(args.port)]
        webui_main()
    elif args.cmd == "blender":
        agent = MovieAgent(config, args.workdir)
        if args.host:
            agent.blocking.client.host = args.host
        if args.port:
            agent.blocking.client.port = args.port
        if not agent.blocking.is_ready():
            print("[blender] 未就绪：请先安装 Blender + Blender MCP 插件，"
                  "并在 Blender 内启动 MCP Server（端口 9876）。")
            print("  插件：https://github.com/ahujasid/blender-mcp  安装后侧栏 N → BlenderMCP → Start MCP Server")
            sys.exit(1)
        spec = agent.blocking.parse_spec(args.beat)
        print(f"[blender] 解析 spec: {spec}")
        if args.mode == "anim":
            out = agent.blocking.render_animation(spec,
                                                  os.path.join(agent.blocking.out_dir, "anim"),
                                                  agent.blocking.anim_frames)
            print(f"[blender] 灰模动画帧序列已渲染到: {out}")
        elif args.mode == "previs":
            out = agent.blocking.render_previs(spec, os.path.join(agent.blocking.out_dir, "preview.png"))
            print(f"[blender] 预视图: {out}")
        elif args.mode == "control":
            out = agent.blocking.render_control(spec, agent.blocking.out_dir)
            print(f"[blender] 控制图: {out}")
        else:
            out = agent.blocking.render_block(spec, agent.blocking.out_dir)
            print(f"[blender] 全套白模资产: {out}")
    elif args.cmd == "ltx":
        from agent.ltx_engine import LTXEngine
        agent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        eng = LTXEngine(config, agent_root=agent_root)
        if not eng.is_ready():
            print("[ltx] 未就绪：请启动 ComfyUI 并安装 LTX-2.5 节点（见 docs/ltx_comfyui_nvfp4.md）。")
            sys.exit(1)
        if args.frames:
            eng.num_frames = args.frames
        out = eng.generate(args.prompt, os.path.abspath(args.out), image=args.image)
        print(f"[ltx] 已生成片段: {out}")
    elif args.cmd == "mmh3":
        from agent.mmh3_engine import MMH3Engine
        agent_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        eng = MMH3Engine(config, agent_root=agent_root)
        if not eng.is_ready():
            print("[mmh3] 未就绪：请启动 ComfyUI(8188) 并安装 comfyui-minimax-h3-audio-T8 "
                  "+ ComfyUI-VideoHelperSuite 节点。")
            sys.exit(1)
        if args.frames:
            eng.num_frames = args.frames
        if args.resolution:
            eng.resolution = args.resolution
        if args.no_turbo:
            eng.lora = ""
        out = eng.generate(args.prompt, os.path.abspath(args.out), image=args.image)
        print(f"[mmh3] 已生成片段: {out}")
    elif args.cmd == "ab":
        from agent import ab as ab_mod
        if len(args.dirs) >= 2:
            rows = ab_mod.compare_runs(args.dirs[0], args.dirs[1])
            if args.key:
                row = next((r for r in rows if r["key"] == args.key), None)
                if not row:
                    print(f"[ab] 未找到镜头 {args.key}")
                    sys.exit(1)
                print(f"== {args.key} 两次生成参数差异 ==")
                for f, va, vb in ab_mod.param_diff(row["a"]["params"], row["b"]["params"]):
                    print(f"  {f}: A={va}  B={vb}")
                print(f"  A qa: {row['a']['qa']}")
                print(f"  B qa: {row['b']['qa']}")
            else:
                print(ab_mod.render_runs(rows))
        else:
            rows = ab_mod.build_rows(args.dirs[0])
            if args.key:
                row = next((r for r in rows if r["key"] == args.key), None)
                if not row:
                    print(f"[ab] 未找到镜头 {args.key}")
                    sys.exit(1)
                print(f"== {args.key} 参数 ==")
                print(json.dumps(row["params"], ensure_ascii=False, indent=2))
                if row["variants"]:
                    print(ab_mod.render_variants(args.key, row["variants"]))
                else:
                    print("  (无其它变体视频；series_manifest 只保留最新一版)")
                if args.grid:
                    paths = [row["video"]] + [v["path"] for v in row["variants"]]
                    out = ab_mod.contact_sheet([p for p in paths if p], args.grid)
                    print(f"  联系表: {out}")
            else:
                print(ab_mod.render_markdown(rows) if args.markdown
                      else ab_mod.render_single(rows))
    elif args.cmd == "preflight":
        from agent import preflight as pf
        res = pf.check_from_outputs(args.workdir, video=args.video,
                                    title=args.title, tags=args.tags,
                                    cover=args.cover, dynamic=args.dynamic)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"投稿前体检：{'通过' if res['ok'] else '未通过'}")
            for e in res["errors"]:
                print(f"  [错误] {e}")
            for w in res["warnings"]:
                print(f"  [提醒] {w}")
            if not res["errors"] and not res["warnings"]:
                print("  无问题。")
        sys.exit(0 if res["ok"] else 1)
    elif args.cmd == "style":
        from agent import style_presets as sp
        if args.list:
            for name in sp.list_presets():
                print(f"  {name:12s} {sp.get(name)['label']}")
            print(f"\n共 {len(sp.list_presets())} 个预设")
        elif args.show:
            p = sp.get(args.show)
            if not p:
                print(f"[style] 未知预设: {args.show}")
                sys.exit(1)
            print(f"== {args.show} · {p['label']} ==")
            print(f"prompt:   {p['prompt']}")
            print(f"negative: {p['negative']}")
            print(f"notes:    {p['notes']}")
        elif args.apply:
            r = sp.apply(args.apply, base=args.base, out=args.out)
            print(f"== 应用 {args.apply} · {r['label']} ==")
            if args.out:
                print(f"  已写出: {args.out}")
            print(f"prompt:   {r['prompt']}")
            print(f"negative: {r['negative']}")
        else:
            print("[style] 用 --list / --show NAME / --apply NAME [--base ...]")
    elif args.cmd == "mix":
        from agent import audio_mix as am
        if not am.is_ready():
            print("[mix] 未找到 ffmpeg，请先安装 ffmpeg。")
            sys.exit(1)
        bgm = am.select_bgm(args.bgm_dir, mood=args.mood)
        if not bgm:
            print(f"[mix] 未找到 BGM（{args.bgm_dir} 下无音频）")
            sys.exit(1)
        print(f"[mix] 自动选曲: {os.path.basename(bgm)}")
        res = am.duck_mix(args.video, bgm, args.out,
                          narration=args.narration, bgm_gain=args.bgm_gain)
        if res.get("ok"):
            print(f"[mix] 已混音: {res['out']}")
        else:
            print(f"[mix] 失败: {res.get('error')}")
            sys.exit(1)
    elif args.cmd == "tts":
        from agent import tts as tts_mod
        res = tts_mod.tts(args.text, args.out, voice=args.voice,
                          backend=args.backend)
        if res.get("ok"):
            if args.save_voice:
                tts_mod.save_voice_config(args.voice or "", res["backend"])
                print(f"[tts] 已存默认音色: {res['backend']}")
            print(f"[tts] 已合成: {res['out']}（{res['backend']}）")
        else:
            print(f"[tts] 失败: {res.get('error')}")
            sys.exit(1)
    elif args.cmd == "charcard":
        from agent import character_card as cc
        bible = {}
        bp = args.bible or os.path.join(args.workdir, "state.json")
        if os.path.exists(bp):
            try:
                raw = json.load(open(bp, encoding="utf-8"))
                bible = raw.get("bible", raw) if isinstance(raw, dict) else {}
            except Exception:
                bible = {}
        engine = None
        if args.engine:
            try:
                if args.engine == "ltx":
                    from agent.ltx_engine import LTXEngine
                    engine = LTXEngine(config, agent_root=HERE)
                elif args.engine == "mmh3":
                    from agent.mmh3_engine import MMH3Engine
                    engine = MMH3Engine(config, agent_root=HERE)
                else:
                    from agent.agent import MovieAgent
                    engine = MovieAgent(config, args.workdir).engine
            except Exception as e:
                print(f"[charcard] 引擎初始化失败（仅出 JSON）: {e}")
                engine = None
        res = cc.generate(bible, args.workdir, style=args.style, engine=engine)
        print(f"[charcard] 角色卡 {len(res['cards'])} 张 → {res['manifest']}")
        for c in res["cards"]:
            print(f"  · {c['name']}（{c['role'] or '—'}）"
                  f"{' ✅肖像' if c['ref_image'] else ''}")
    elif args.cmd == "timeline":
        tl_path = os.path.join(args.workdir, "timeline.json")
        if args.build or not os.path.exists(tl_path):
            man = {}
            mp = os.path.join(args.workdir, "series_manifest.json")
            if os.path.exists(mp):
                try:
                    man = json.load(open(mp, encoding="utf-8"))
                except Exception:
                    man = {}
            shots = [{"key": k, "label": k, "enabled": True,
                      "in_point": None, "out_point": None, "order": i}
                     for i, k in enumerate(man.keys())]
            data = {"shots": shots, "updated": None}
            with open(tl_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[timeline] 已重建 {len(shots)} 个镜头 → {tl_path}")
        else:
            data = json.load(open(tl_path, encoding="utf-8"))
        if args.export:
            json.dump(data, open(args.export, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            print(f"[timeline] 已导出: {args.export}")
        if args.do_print or not args.export:
            for s in sorted(data.get("shots", []), key=lambda x: x.get("order", 0)):
                flag = "✔" if s.get("enabled") else "✘"
                print(f"  [{flag}] #{s.get('order')} {s['key']}"
                      f"  ({s.get('in_point')}-{s.get('out_point')})")
    elif args.cmd in ("publish", "publish-concept"):
        ensure(config.get("publish", {}).get("enabled", False), "未启用发布(publish.enabled)")
        agent = MovieAgent(config, args.workdir)
        if args.cmd == "publish":
            if not agent.publisher.is_ready():
                print("[publish] 未检测到 biliup，请先安装 biliup-rs。")
                print(agent.publisher.login_guide())
            else:
                res = agent.publish_only(args.video)
                if res.get("ok"):
                    print(f"[publish] 投稿成功: {res['title']}")
                else:
                    print(f"[publish] 投稿失败: {res.get('error')}")
                    if "login" in str(res.get("error", "")).lower() \
                            or "cookie" in str(res.get("error", "")).lower():
                        print(agent.publisher.login_guide())
        else:  # publish-concept：把创意/规划渲染成视频 demo（biliup 就绪则投稿）
            old_bvid = getattr(args, "replace", None)
            if old_bvid:
                print(f"  [warn] 旧视频 {old_bvid} 需手动下架：自动删除已放弃"
                      "（B 站风控需人机验证），请到创作中心操作。")
            res = agent.publisher.publish_concept(submit=args.submit,
                                                  xfade=args.xfade, bgm=args.bgm)
            if isinstance(res, dict):
                if res.get("ok"):
                    print(f"[publish-concept] 投稿成功: {res['title']}")
                    print("STATUS=OK")  # ASCII 哨兵，供 publish_auto.bat 判断（避免中文编码坑）
                else:
                    print(f"[publish-concept] 投稿失败: {res.get('error')}")
                    print("STATUS=FAIL")
            else:
                print(f"[publish-concept] 已生成视频素材: {res}")


if __name__ == "__main__":
    main()
