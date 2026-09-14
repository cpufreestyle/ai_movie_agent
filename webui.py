#!/usr/bin/env python3
"""AI 电影 Agent · 本地 WebUI（零构建，纯 Flask + 原生前端）。

启动：  python webui.py            # 默认 http://127.0.0.1:8000
      python webui.py --port 9000  # 自定义端口

四个页签：
  1) 概览配置  - 影片状态 + biliup 登录态 + config.yaml 在线编辑
  2) 运行监控  - 启动 A–H 流水线 / 续写，实时日志 + 进度
  3) 创意策划  - enrich-bible 充实设定 + 预览 concept demo + 投稿到 B 站
  4) 发布      - biliup 登录引导 + 投稿正式成片

后端把耗时操作放到后台线程，print 日志被捕获后经 /api/logs 轮询给前端。

代码结构（本文件只负责组装 app 与启动）：
  webserver/state.py           共享状态与基础设施（路径 / 日志捕获 / 后台任务 / agent 惰性构造）
  webserver/services/          业务逻辑，不依赖 Flask 请求上下文
  webserver/views/             按域拆分的 Blueprint（core / run / pipeline / bili / media /
                               blocking / film），URL 规则与原单文件版完全一致
"""
from __future__ import annotations

import argparse

from flask import Flask

from webserver import register_blueprints

# 兼容旧调用点与既有测试：
#   - cli.py 的 `from webui import main`
#   - tests 里的 `import webui; webui.app / webui._state / webui._lock /
#     webui._stop_requested / webui.run_script`
# 拆分后这些名字的实际归属见注释，这里只做一层再导出。
from webserver.services.pipeline import run_script                       # noqa: F401
from webserver.state import (                                            # noqa: F401
    CONFIG_PATH,
    EP_TITLES,
    HERE,
    MEDIA,
    STAGE_NAMES,
    STORYBOARD_PATH,
    TIMELINE_PATH,
    WHITE_PAGES,
    WORKDIR,
    _kill_tree,
    _lock,
    _stop_requested,
    _state,
    get_agent,
    json_resp,
    load_config,
    load_material,
    pipeline_snapshot,
    run_in_background,
    save_agent_state,
    start_stage,
)

app = Flask(__name__)
app.json.ensure_ascii = False  # 中文不乱码

register_blueprints(app)


def main():
    p = argparse.ArgumentParser(description="AI 电影 Agent WebUI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    print(f"[webui] 启动于 http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
