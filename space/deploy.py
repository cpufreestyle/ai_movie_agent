"""部署 ai_movie_agent 到魔搭创空间 (ModelScope Studio)。

前置（需用户在 modelscope.cn 操作）：
  1. 登录 modelscope.cn → 创空间 → 新建（选 Gradio SDK 模板）→ 记下 git 地址，
     形如 https://www.modelscope.cn/studios/<用户名>/<空间名>.git
  2. 账号设置 → 访问令牌，生成 token。
本脚本用 token 作 git 密码，把「完整项目 + Space 包装」推到该仓库，平台自动装依赖并启动 app.py。

用法：
  python space/deploy.py --space-url <git_url> --token <MODELSCOPE_TOKEN> [--branch master]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPACE_DIR = os.path.join(REPO_ROOT, "space")

# 推到创空间根目录的内容（白名单，排除重型/私密文件）
COPY_DIRS = ["agent", "tools", "skills", "workflows", "docs"]
COPY_FILES = [
    "cli.py", "config.yaml", "requirements.txt",
    "README.md", "DEPLOY.md", "deploy.py",
    os.path.join("space", "app.py"),
    os.path.join("space", "requirements.txt"),
    os.path.join("space", "README.md"),
]
SKIP_DIRS = {".git", ".venv", "__pycache__", "outputs", "legacy", "node_modules",
             "material", "kb", "tasks", "webui"}


def _auth_url(space_url: str, token: str) -> str:
    # https://www.modelscope.cn/studios/u/s.git -> https://oauth2:token@www.modelscope.cn/studios/u/s.git
    if "oauth2:" in space_url:
        return space_url
    if space_url.startswith("https://"):
        return "https://oauth2:" + token + "@" + space_url[len("https://"):]
    if space_url.startswith("http://"):
        return "http://oauth2:" + token + "@" + space_url[len("http://"):]
    return space_url


def _copytree(src, dst):
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        if any(part in SKIP_DIRS for part in rel.split(os.sep)):
            continue
        for d in list(dirs):
            if d in SKIP_DIRS:
                dirs.remove(d)
        for f in files:
            s = os.path.join(root, f)
            t = os.path.join(dst, rel, f)
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copy2(s, t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--space-url", required=True, help="创空间 git 地址")
    ap.add_argument("--token", required=True, help="ModelScope 访问令牌")
    ap.add_argument("--branch", default="master")
    ap.add_argument("--workdir", default=os.path.join(SPACE_DIR, "_deploy_tmp"))
    args = ap.parse_args()

    # 1) 克隆空仓库
    if os.path.exists(args.workdir):
        shutil.rmtree(args.workdir)
    auth = _auth_url(args.space_url, args.token)
    subprocess.run(["git", "clone", "--branch", args.branch, auth, args.workdir],
                   check=True)

    # 2) 复制项目 + Space 包装
    for d in COPY_DIRS:
        s = os.path.join(REPO_ROOT, d)
        if os.path.isdir(s):
            _copytree(s, os.path.join(args.workdir, d))
    for f in COPY_FILES:
        s = os.path.join(REPO_ROOT, f)
        if os.path.exists(s):
            t = os.path.join(args.workdir, os.path.basename(f))
            shutil.copy2(s, t)

    # 3) 提交推送
    subprocess.run(["git", "-C", args.workdir, "add", "-A"], check=True)
    msg = "feat: deploy ai_movie_agent as ModelScope Studio (电影 Agent 参赛作品)"
    try:
        subprocess.run(["git", "-C", args.workdir, "commit", "-m", msg], check=True)
    except subprocess.CalledProcessError:
        print("[deploy] 无变更，跳过提交")
    subprocess.run(["git", "-C", args.workdir, "push", "origin", args.branch], check=True)
    print("\n[deploy] 已推送。平台将自动安装 requirements.txt 并启动 app.py。")
    print("[deploy] 构建/启动日志在创空间『设置 → 查看日志』；上线后在空间主页拿到作品链接。")


if __name__ == "__main__":
    main()
