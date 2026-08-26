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

HERE = os.path.dirname(os.path.abspath(__file__))


def ensure(cond, msg: str):
    if not cond:
        print(f"[错误] {msg}")
        sys.exit(1)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
        import sys as _sys
        _sys.argv = ["webui", "--host", args.host, "--port", str(args.port)]
        webui_main()
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
