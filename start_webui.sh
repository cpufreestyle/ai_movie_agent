#!/usr/bin/env bash
# AI 电影 Agent — 启动 WebUI（Linux / macOS）
set -e
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .venv/bin/activate
exec python cli.py webui --host 0.0.0.0 --port 8000
