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
import inspect
import json
import os
import subprocess
import sys
import uuid

import yaml
from config_env import apply_env_overrides

HERE = os.path.dirname(os.path.abspath(__file__))


def ensure(cond, msg: str):
    if not cond:
        print(f"[错误] {msg}")
        sys.exit(1)


def load_config(path: str) -> dict:
    """读配置。config.yaml 缺失时回退到同目录的 config.example.yaml。

    config.yaml 不入库（WebUI 的「接口与模型设置」会把 api_key 写回它，
    公开仓库里放不得），所以新克隆的仓库里只有模板；这里回退一次，
    免得每条子命令都抛 FileNotFoundError。
    """
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(path) or ".", "config.example.yaml")
        if os.path.exists(example):
            path = example
        else:
            sys.exit(f"找不到配置文件 {path}；请先 "
                     f"`cp config.example.yaml config.yaml` 再重试。")
    with open(path, "r", encoding="utf-8") as f:
        return apply_env_overrides(yaml.safe_load(f) or {})


def build_parser() -> argparse.ArgumentParser:
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

    p_pipe = sub.add_parser("pipeline", help="跑完整 A-H 流水线（采集->发布）")
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

    p_md = sub.add_parser("metadata",
                          help="投稿元数据自动生成：标题候选/简介/标签/动态")
    p_md.add_argument("--ep", type=int, required=True, help="集号")
    p_md.add_argument("--script", default=os.path.join(HERE, "outputs", "series_script.json"))
    p_md.add_argument("--out", default=None, help="写出 JSON 路径（默认 outputs/epN_metadata.json）")
    p_md.add_argument("--no-llm", action="store_true", help="只用规则法（离线/确定性）")
    p_md.add_argument("--preflight", action="store_true", help="顺带跑投稿静态校验")
    p_md.add_argument("--json", action="store_true", help="输出 JSON")

    p_en = sub.add_parser("enhance",
                          help="画质增强：RIFE 插帧 → ESRGAN 超分 → 统一高质量编码")
    p_en.add_argument("src", nargs="?", default="", help="源视频")
    p_en.add_argument("dst", nargs="?", default="", help="输出视频")
    p_en.add_argument("--profile", default="", choices=["draft", "standard", "high", "anime"],
                      help="编码档位（默认按内容类型推断）")
    p_en.add_argument("--kind", default="", choices=["anime", "real"], help="内容类型")
    p_en.add_argument("--sr-model", default="", help="超分模型文件名")
    p_en.add_argument("--rife-ckpt", default="", help="RIFE 权重（默认取本机最新版）")
    p_en.add_argument("--multiplier", type=int, default=2, help="RIFE 插帧倍数")
    p_en.add_argument("--chunk", type=int, default=60, help="超分分段帧数（防 OOM）")
    p_en.add_argument("--no-rife", action="store_true", help="跳过插帧")
    p_en.add_argument("--no-sr", action="store_true", help="跳过超分")
    p_en.add_argument("--denoise", action="store_true", help="轻度降噪")
    p_en.add_argument("--loudnorm", action="store_true", help="响度归一化 -16 LUFS")
    p_en.add_argument("--api", default="http://127.0.0.1:8188", help="ComfyUI 地址")
    p_en.add_argument("--strict", action="store_true", help="ComfyUI 不可用则报错退出")
    p_en.add_argument("--dry-run", action="store_true", help="只打印命令")
    p_en.add_argument("--list-profiles", action="store_true", help="列出编码档位")

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

    p_mcp = sub.add_parser(
        "mcp", help="启动 Web MCP 服务，把 Agent 能力以 MCP 工具暴露（供 API/本地模型驱动）")
    p_mcp.add_argument("--host", default="127.0.0.1",
                       help="HTTP 监听地址（默认仅本机 127.0.0.1）")
    p_mcp.add_argument("--port", type=int, default=9000, help="HTTP 监听端口")
    p_mcp.add_argument("--transport", default="streamable-http",
                       choices=["streamable-http", "sse", "stdio"],
                       help="MCP 传输：streamable-http(默认,网页/远程) / sse(旧版HTTP) / stdio(本地)")

    p_call = sub.add_parser(
        "call", help="单入口 JSON 调度器：JSON 进 / JSON 出，直接调用 Agent 能力"
                     "（供其他 agent 用 subprocess 驱动，无需起服务、无需 LLM）")
    p_call.add_argument("tool", help="工具名：list_comfy_models / quality_probe / "
                                      "enhance_video / generate_clip / agent_status / "
                                      "generate_metadata / get_job_status")
    p_call.add_argument("args_json", nargs="?", default="{}",
                        help="JSON 参数字符串，如 '{\"src\":\"x.mp4\",\"dry_run\":true}'")
    p_call.add_argument("--wait", action="store_true",
                        help="长任务（enhance_video/generate_clip）在本进程阻塞到完成，"
                             "直接返回结果（不返 job_id），适合能阻塞等待的调用方")

    p_jw = sub.add_parser("_job_worker", help=argparse.SUPPRESS)
    p_jw.add_argument("jid")
    p_jw.add_argument("tool")
    p_jw.add_argument("args_json")

    p_tl = sub.add_parser("timeline", help="WebUI 时间轴：查看/重建/导出镜头时间轴")
    p_tl.add_argument("--workdir", default=os.path.join(HERE, "outputs"))
    p_tl.add_argument("--build", action="store_true", help="从 series_manifest 重建时间轴")
    p_tl.add_argument("--export", default=None, metavar="OUT.json")
    p_tl.add_argument("--concat", default=None, metavar="OUT.mp4",
                      help="按时间轴顺序导出 ffmpeg 拼接命令（含裁剪）")
    p_tl.add_argument("--print", dest="do_print", action="store_true", help="打印当前时间轴")

    return ap


# ---------------------------------------------------------------- 子命令实现
# 原先 main() 里是一条 C901=74 的 if/elif 长链：改一个子命令要在 300 行里找位置，
# 而且 `from agent.agent import MovieAgent` 在所有命令路径上都被执行（style / ab
# 这种根本不需要 agent 的命令也要付这份导入代价）。
# 现在每个子命令一个函数、由 COMMANDS 表登记，main() 只做
# 「解析 -> 取 handler -> 调用」。**参数与输出逐字不变**，只换了组织方式。


def _cmd_run(args, config):
    from agent.agent import MovieAgent

    agent = MovieAgent(config, args.workdir)
    agent.run(continuous=args.continuous, max_scenes=args.max_scenes,
              auto=not args.interactive, seed=args.seed, do_research=False)


def _cmd_pipeline(args, config):
    from agent.agent import MovieAgent

    agent = MovieAgent(config, args.workdir)
    agent.run(continuous=args.continuous, max_scenes=args.max_scenes,
              auto=not args.interactive, seed=args.seed,
              topic=args.topic, do_research=args.do_research)


def _cmd_status(args, config):
    from agent.agent import MovieAgent

    agent = MovieAgent(config, args.workdir)
    print(json.dumps(agent.status(), ensure_ascii=False, indent=2))


def _cmd_init(args, config):
    from agent.agent import MovieAgent

    if os.path.exists(os.path.join(args.workdir, "state.json")):
        os.remove(os.path.join(args.workdir, "state.json"))
    agent = MovieAgent(config, args.workdir)
    print("世界观已重置:", json.dumps(agent.state["bible"], ensure_ascii=False, indent=2))


def _cmd_enrich_bible(args, config):
    from agent.agent import MovieAgent

    agent = MovieAgent(config, args.workdir)
    res = agent.enrich_bible(xfade=args.xfade, bgm=args.bgm)
    print("[enrich-bible] 已充实 bible 并重渲染 demo:")
    print(f"  video: {res['video']}")
    print(f"  cover: {res['cover']}")
    b = res["bible"]
    print(f"  characters: {len(b.get('characters', []))} 个 · "
          f"visual_style: {'有' if b.get('visual_style') else '无'} · "
          f"three_act: {len(b.get('three_act', []))} 段")


def _cmd_webui(args, config):
    from webui import main as webui_main
    sys.argv = ["webui", "--host", args.host, "--port", str(args.port)]
    webui_main()


def _render_blender_mode(agent, args) -> str:
    """按 --mode 分派白模渲染（调用方已确认 blocking 就绪）。"""
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
    return out


def _cmd_blender(args, config):
    from agent.agent import MovieAgent

    agent = MovieAgent(config, args.workdir)
    if args.host:
        agent.blocking.client.host = args.host
    if args.port:
        agent.blocking.client.port = args.port
    if not agent.blocking.is_ready():
        print("[blender] 未就绪：请先安装 Blender + Blender MCP 插件，"
              "并在 Blender 内启动 MCP Server（端口 9876）。")
        print("  插件：https://github.com/ahujasid/blender-mcp  安装后侧栏 N -> BlenderMCP -> Start MCP Server")
        sys.exit(1)
    _render_blender_mode(agent, args)


def _cmd_ltx(args, config):
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


def _cmd_mmh3(args, config):
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


def _ab_compare_two(args):
    """两个 work 目录：逐镜对比参数差异。"""
    from agent import ab as ab_mod
    rows = ab_mod.compare_runs(args.dirs[0], args.dirs[1])
    if not args.key:
        print(ab_mod.render_runs(rows))
        return
    row = next((r for r in rows if r["key"] == args.key), None)
    if not row:
        print(f"[ab] 未找到镜头 {args.key}")
        sys.exit(1)
    print(f"== {args.key} 两次生成参数差异 ==")
    for f, va, vb in ab_mod.param_diff(row["a"]["params"], row["b"]["params"]):
        print(f"  {f}: A={va}  B={vb}")
    print(f"  A qa: {row['a']['qa']}")
    print(f"  B qa: {row['b']['qa']}")


def _ab_single(args):
    """单个 work 目录：多镜总览 / 聚焦某镜的变体与联系表。"""
    from agent import ab as ab_mod
    rows = ab_mod.build_rows(args.dirs[0])
    if not args.key:
        print(ab_mod.render_markdown(rows) if args.markdown else ab_mod.render_single(rows))
        return
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


def _cmd_ab(args, config):
    if len(args.dirs) >= 2:
        _ab_compare_two(args)
    else:
        _ab_single(args)


def _cmd_preflight(args, config):
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


def _cmd_metadata(args, config):
    from agent import metadata as md
    out = args.out or os.path.join(HERE, "outputs", f"ep{args.ep}_metadata.json")
    meta = md.generate(args.ep, script_path=args.script, config=config,
                       use_llm=not args.no_llm, out=out)
    if args.json:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    else:
        print(f"== {meta['series']}第{md.cn_num(args.ep)}集 · {meta['ep_title']}"
              f"（生成方式：{meta['source']}）==")
        print(f"标题最佳：{meta['title']}")
        print("标题候选：")
        for t in meta["titles"]:
            print(f"  - {t}")
        print("标签： " + " ".join("#" + t for t in meta["tags"]))
        print(f"动态： {meta['dynamic']}")
        print("简介：")
        print(meta["desc"])
        print(f"英文标题：{meta['en_title']}")
        print(f"已写出：{out}")
    if args.preflight:
        res = md.validate(meta)
        print(f"\n投稿校验：{'通过' if res['ok'] else '未通过'}")
        for e in res["errors"]:
            print(f"  [错误] {e}")
        for w in res["warnings"]:
            print(f"  [提醒] {w}")
        sys.exit(0 if res["ok"] else 1)


def _cmd_enhance(args, config):
    from agent import encode as enc

    if args.list_profiles or not args.src:
        print("编码档位（crf / preset / tune / 音频码率）：")
        for name in enc.profiles():
            p = enc.resolve(name)
            print(f"  {name:9s} crf={p['crf']:<3} preset={p['preset']:<9s} "
                  f"tune={p['tune'] or '-':<10s} audio={p['abitrate']}")
        print("\n超分模型推荐：")
        for k, v in enc.SR_MODELS.items():
            print(f"  {k:6s} {', '.join(v)}")
        if not args.src:
            print("\n用法: cli.py enhance <src.mp4> <dst.mp4> [--kind anime|real]")
        return

    import subprocess
    dst = args.dst or (os.path.splitext(args.src)[0] + "_enhanced.mp4")
    cmd = [sys.executable, os.path.join(HERE, "enhance_video.py"), args.src, dst]
    for flag in ("no_rife", "no_sr", "denoise", "strict", "dry_run", "loudnorm"):
        if getattr(args, flag):
            cmd.append("--" + flag.replace("_", "-"))
    for opt in ("profile", "kind", "sr_model", "rife_ckpt"):
        v = getattr(args, opt)
        if v:
            cmd += ["--" + opt.replace("_", "-"), str(v)]
    cmd += ["--multiplier", str(args.multiplier), "--chunk", str(args.chunk),
            "--api", args.api]
    raise SystemExit(subprocess.run(cmd).returncode)


def _cmd_style(args, config):
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


def _cmd_mix(args, config):
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


def _cmd_tts(args, config):
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


def _load_bible(path: str) -> dict:
    """读 bible/state.json；缺失或损坏都退化成空字典（仅出 JSON 也能跑）。"""
    if not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:                  # noqa: BLE001 - 读坏了就当没有，不让命令崩
        return {}
    return raw.get("bible", raw) if isinstance(raw, dict) else {}


def _charcard_engine(args, config):
    """--engine 指定的肖像生成引擎；未指定或初始化失败返回 None（仅出 JSON）。"""
    if not args.engine:
        return None
    try:
        if args.engine == "ltx":
            from agent.ltx_engine import LTXEngine
            return LTXEngine(config, agent_root=HERE)
        if args.engine == "mmh3":
            from agent.mmh3_engine import MMH3Engine
            return MMH3Engine(config, agent_root=HERE)
        from agent.agent import MovieAgent
        return MovieAgent(config, args.workdir).engine
    except Exception as e:
        print(f"[charcard] 引擎初始化失败（仅出 JSON）: {e}")
        return None


def _cmd_charcard(args, config):
    from agent import character_card as cc
    bp = args.bible or os.path.join(args.workdir, "state.json")
    res = cc.generate(_load_bible(bp), args.workdir, style=args.style,
                      engine=_charcard_engine(args, config))
    print(f"[charcard] 角色卡 {len(res['cards'])} 张 -> {res['manifest']}")
    for c in res["cards"]:
        print(f"  · {c['name']}（{c['role'] or '—'}）"
              f"{' [肖像]' if c['ref_image'] else ''}")


def _cmd_timeline(args, config):
    from agent import timeline as _tl
    if args.build or not os.path.exists(_tl.timeline_path(args.workdir)):
        data = _tl.build_timeline(args.workdir)
        _tl.save_timeline(args.workdir, data)
        print(f"[timeline] 已重建 {len(data['shots'])} 个镜头 -> {_tl.timeline_path(args.workdir)}")
    else:
        data = _tl.load_timeline(args.workdir)
    if args.export:
        json.dump(data, open(args.export, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[timeline] 已导出: {args.export}")
    if args.concat:
        try:
            cmd = _tl.export_concat(data, args.workdir, args.concat)
            print(f"[timeline] 拼接命令（已写出 {args.concat}.concat.txt）：\n  {cmd}")
        except Exception as e:
            print(f"[timeline] 拼接导出失败: {e}")
            sys.exit(1)
    if args.do_print or not (args.export or args.concat):
        for s in _tl.ordered_shots(data):
            flag = "+" if s.get("enabled") else "-"
            print(f"  [{flag}] #{s.get('order')} {s['key']}"
                  f"  ({s.get('in_point')}-{s.get('out_point')})")


def _cmd_publish(args, config):
    from agent.agent import MovieAgent
    ensure(config.get("publish", {}).get("enabled", False), "未启用发布(publish.enabled)")
    agent = MovieAgent(config, args.workdir)
    if not agent.publisher.is_ready():
        print("[publish] 未检测到 biliup，请先安装 biliup-rs。")
        print(agent.publisher.login_guide())
        return
    res = agent.publish_only(args.video)
    if res.get("ok"):
        print(f"[publish] 投稿成功: {res['title']}")
        return
    print(f"[publish] 投稿失败: {res.get('error')}")
    if "login" in str(res.get("error", "")).lower() \
            or "cookie" in str(res.get("error", "")).lower():
        print(agent.publisher.login_guide())


def _cmd_mcp(args, config):
    import webmcp
    argv = ["--host", args.host, "--port", str(args.port), "--transport", args.transport]
    raise SystemExit(webmcp.main(argv))


# ---------------------------------------------------------------- call：单入口 JSON 调度器
# 设计（用户选定）：`cli.py call <tool> '{json参数}'` —— JSON 进、JSON 出，直接复用
# webmcp 的各 handler，无需起服务、无需 LLM。任意 agent 用 subprocess 即可驱动全部能力。
#   * 短任务（list_comfy_models / quality_probe / agent_status / generate_metadata）
#     在本进程同步跑完，直接打印结果 JSON。
#   * 长任务（enhance_video / generate_clip）默认**后台**跑并返回 job_id，
#     调用方再 `cli.py call get_job_status '{"job_id":"..."}'` 轮询；
#     若调用方能阻塞等待，加 `--wait` 则在本进程跑到完成直接返回结果。
#   * 跨进程的 job 状态用「文件型 job store」持久化（每个 job 一个 json），
#     所以轮询命令是独立进程也能读到上一进程启动的后台任务。
def _job_dir() -> str:
    d = os.path.join(HERE, ".webmcp_jobs")
    os.makedirs(d, exist_ok=True)
    return d


def _job_path(jid: str) -> str:
    return os.path.join(_job_dir(), f"{jid}.json")


def _job_write(jid: str, data: dict):
    tmp = _job_path(jid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _job_path(jid))  # 原子写，避免轮询读到半截


def _job_read(jid: str):
    p = _job_path(jid)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _spawn_detached(cmd: list[str], log_path: str):
    """启动一个与会话脱钩的后台进程（父进程退出后它继续跑）。"""
    flags = 0
    if os.name == "nt":
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "DETACHED_PROCESS", 0))
    with open(log_path, "wb") as lf:
        return subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
            close_fds=True, creationflags=flags,
        )


def _clean_kwargs(handler, kwargs: dict) -> dict:
    """只把 handler 接受的形参传进去，避免多余 key 触发 TypeError。"""
    params = inspect.signature(handler).parameters
    return {k: v for k, v in kwargs.items() if k in params}


def _cmd_call(args, config):
    import webmcp

    tool = args.tool
    spec = next((s for s in webmcp.TOOL_SPECS if s["name"] == tool), None)
    if spec is None:
        names = ", ".join(s["name"] for s in webmcp.TOOL_SPECS)
        print(json.dumps({"ok": False, "note": f"未知工具: {tool}。可用: {names}"},
                          ensure_ascii=False))
        return

    # 参数解析（失败也要给 JSON，便于调用方判定）
    try:
        kwargs = json.loads(args.args_json or "{}")
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "note": f"JSON 参数解析失败: {e}"},
                          ensure_ascii=False))
        return
    if not isinstance(kwargs, dict):
        print(json.dumps({"ok": False, "note": "args_json 必须是 JSON 对象"},
                          ensure_ascii=False))
        return

    # get_job_status 是查询：直接读文件型 job store，跨进程有效
    if tool == "get_job_status":
        jid = (kwargs.get("job_id") or "").strip()
        if not jid:
            print(json.dumps({"ok": False, "note": "get_job_status 需要 job_id"},
                             ensure_ascii=False))
            return
        j = _job_read(jid)
        if not j:
            print(json.dumps({"ok": False, "note": f"未知 job_id: {jid}"},
                             ensure_ascii=False))
            return
        print(json.dumps(j, ensure_ascii=False, indent=2))
        return

    handler = spec["handler"]
    clean = _clean_kwargs(handler, kwargs)

    # 长任务：默认后台跑 + 返 job_id；--wait 则本进程阻塞到完成
    if spec.get("long") and not args.wait:
        jid = uuid.uuid4().hex[:12]
        _job_write(jid, {"status": "queued", "name": tool, "result": None,
                         "note": "", "log_tail": ""})
        log_path = os.path.join(_job_dir(), f"{jid}.log")
        worker_cmd = [sys.executable, os.path.abspath(__file__),
                      "_job_worker", jid, tool,
                      json.dumps(kwargs, ensure_ascii=False)]
        _spawn_detached(worker_cmd, log_path)
        print(json.dumps({"job_id": jid, "status": "queued",
                          "note": "已后台启动，用 get_job_status 轮询结果"},
                         ensure_ascii=False))
        return

    res = handler(**clean)
    if isinstance(res, str):  # 异步 handler 返 JSON 字符串
        print(res)
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))


def _cmd_job_worker(args, config):
    """内部命令：在脱钩后台进程里跑长任务的同步 handler，并把结果写回 job store。

    不加载 config（main() 已为 _job_worker 跳过），也不依赖 mcp。
    """
    import webmcp

    jid, tool = args.jid, args.tool
    try:
        kwargs = json.loads(args.args_json)
    except Exception as e:  # noqa: BLE001
        _job_write(jid, {"status": "failed", "name": tool, "result": None,
                         "note": f"参数解析失败: {e}", "log_tail": ""})
        return
    handler = next((s["handler"] for s in webmcp.TOOL_SPECS if s["name"] == tool), None)
    if handler is None:
        _job_write(jid, {"status": "failed", "name": tool, "result": None,
                         "note": f"未知工具: {tool}"})
        return
    try:
        j = _job_read(jid) or {}
        j.update({"status": "running"})
        _job_write(jid, j)
        res = handler(**_clean_kwargs(handler, kwargs))
        _job_write(jid, {"status": "done" if res.get("ok") else "failed",
                         "name": tool, "result": res,
                         "note": res.get("note", ""),
                         "log_tail": res.get("log_tail", "")})
    except Exception as e:  # noqa: BLE001
        import traceback as _tb
        _job_write(jid, {"status": "failed", "name": tool, "result": None,
                         "note": f"{type(e).__name__}: {e}",
                         "log_tail": _tb.format_exc()})


def _cmd_publish_concept(args, config):
    from agent.agent import MovieAgent
    ensure(config.get("publish", {}).get("enabled", False), "未启用发布(publish.enabled)")
    agent = MovieAgent(config, args.workdir)
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


#: 子命令 -> 处理函数。新增子命令只需在这里登记一行。
COMMANDS = {
    "run": _cmd_run,
    "pipeline": _cmd_pipeline,
    "status": _cmd_status,
    "init": _cmd_init,
    "enrich-bible": _cmd_enrich_bible,
    "webui": _cmd_webui,
    "blender": _cmd_blender,
    "ltx": _cmd_ltx,
    "mmh3": _cmd_mmh3,
    "ab": _cmd_ab,
    "preflight": _cmd_preflight,
    "metadata": _cmd_metadata,
    "enhance": _cmd_enhance,
    "style": _cmd_style,
    "mix": _cmd_mix,
    "tts": _cmd_tts,
    "charcard": _cmd_charcard,
    "timeline": _cmd_timeline,
    "mcp": _cmd_mcp,
    "call": _cmd_call,
    "_job_worker": _cmd_job_worker,
    "publish": _cmd_publish,
    "publish-concept": _cmd_publish_concept,
}


def main():
    ap = build_parser()
    # parse_known_args + 针对 call 的容错：argparse 存在「可选参数插在两个位置参数之间」
    # 的已知缺陷——`call enhance_video --wait '{json}'` 会把 json 当成多余参数。
    # 这里把 call 子命令留下的「多余参数」归给 args_json；其余命令仍严格报错。
    args, extra = ap.parse_known_args()
    if args.cmd == "call":
        if extra:
            if len(extra) == 1 and isinstance(getattr(args, "args_json", ""), str):
                args.args_json = extra[0]
            else:
                ap.error(f"无法识别的参数: {' '.join(extra)}")
    elif extra:
        ap.error(f"无法识别的参数: {' '.join(extra)}")
    # _job_worker 是内部后台进程，不需要也不应依赖 config 加载（避免配置缺失时任务直接挂掉）
    config = {} if args.cmd == "_job_worker" else load_config(args.config)
    handler = COMMANDS.get(args.cmd)
    if handler is None:                  # subparsers(required=True) 之外的兜底
        ap.error(f"未知子命令: {args.cmd}")
    handler(args, config)


if __name__ == "__main__":
    main()
