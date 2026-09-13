"""AI 电影 Agent —— 魔搭创空间 (ModelScope Space) 演示应用。

设计目标：
- 在创空间里把一个"电影 Agent"系统可视化、可交互，对应赛题「进阶创作｜电影 Agent」。
- 优先调用真实流水线 `python cli.py pipeline`（当环境装有 torch / 配置了 COMFYUI_API 时，
  能真正企划并生成镜头）；
- 若环境无 torch（纯 CPU 创空间）或未配置视频引擎，自动降级为「内置模板企划 Demo」，
  保证任意环境都能跑通、都能展示"一句话创意 → 世界观 → 逐镜分镜"的 Agent 能力。

运行：在创空间根目录（已包含本项目 agent/ cli.py config.yaml）下 `python app.py`。
"""
from __future__ import annotations

import gradio as gr
import json
import os
import subprocess
import sys
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.yaml")
OUT_DIR = os.path.join(HERE, "outputs")


def _load_project():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("project", {}), cfg.get("engine", {})
    except Exception:
        return {}, {}


# ---------- 内置模板企划 Demo（不依赖 torch / ComfyUI，纯 CPU 可用） ----------
def _template_plan(topic: str, style: str, n: int):
    topic = (topic or "近未来赛博都市，记忆与身份").strip()
    style = (style or "anime style, cel-shaded 2D animation, dramatic lighting").strip()
    bible = (
        f"# 世界观\n片名：《看见未来之前》\n主题：{topic}\n"
        f"视觉风格：{style}\n\n"
        "设定：一座被记忆交易统治的近未来都市。主角靠贩卖他人记忆为生，"
        "直到发现自己的一段记忆被人篡改——于是踏上追查自我的旅程。"
    )
    beats = []
    arcs = [
        ("开场", "主角在霓虹雨夜的出租屋里，把一段陌生记忆写入客户芯片。"),
        ("转折", "主角回放自己的记忆，发现结尾被人剪接过——那段本不存在。"),
        ("行动", "潜入记忆黑市的数据塔，追踪篡改者的数字指纹。"),
        ("高潮", "在悬浮列车顶端的对决，城市天际线在身后崩解重组。"),
        ("落点", "主角取回真实记忆，却选择保留那处被改写的温柔。"),
    ]
    for i, (kind, desc) in enumerate(arcs[: max(1, n)], 1):
        beats.append({
            "scene": i,
            "kind": kind,
            "desc": desc,
            "prompt": f"{style}。{desc} 缓慢镜头，电影感，近未来赛博都市。",
        })
    return bible, beats


def _beats_to_md(bible, beats):
    md = "## 世界观 Bible\n\n" + bible + "\n\n## 逐镜分镜 (Storyboard)\n\n"
    for b in beats:
        md += (
            f"### 第 {b['scene']} 镜 · {b['kind']}\n"
            f"- 描述：{b['desc']}\n"
            f"- 视频提示词：{b['prompt']}\n\n"
        )
    return md


def _read_real_outputs():
    """读取 cli.py pipeline 落盘的 bible 与分镜（若生成成功）。"""
    bible, beats = None, []
    state = os.path.join(OUT_DIR, "state.json")
    if os.path.exists(state):
        try:
            with open(state, "r", encoding="utf-8") as f:
                st = json.load(f)
            bible = json.dumps(st.get("bible", st), ensure_ascii=False, indent=2)
        except Exception:
            pass
    script = os.path.join(OUT_DIR, "script.jsonl")
    if os.path.exists(script):
        try:
            with open(script, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    beats.append(json.loads(line))
        except Exception:
            pass
    return bible, beats


def run_agent(topic: str, max_scenes: int):
    """尝试真实流水线；失败则降级模板 Demo。返回 (log, result_md)。"""
    project, _ = _load_project()
    style = project.get("style", "")
    log_lines = []

    # 1) 真实 Agent 流水线：仅在显式配置视频引擎时尝试。
    #    创空间默认是轻量 CPU 环境（无 torch / 无引擎），cli.py 会缺依赖且最多
    #    卡满 600s 才失败——直接尝试既看不到产出也拖慢体验，故改为显式开关。
    if os.environ.get("COMFYUI_API"):
        log_lines.append(
            "[模式] 检测到 COMFYUI_API，尝试真实 Agent 流水线（A→H，最长 10 分钟）。"
        )
        try:
            cmd = [
                sys.executable, "cli.py", "pipeline",
                "--topic", topic or project.get("theme", ""),
                "--max-scenes", str(int(max_scenes)),
                "--no-research",
            ]
            proc = subprocess.run(
                cmd, cwd=HERE, capture_output=True, text=True, timeout=600,
            )
            log_lines.append(proc.stdout)
            log_lines.append(proc.stderr)
            if proc.returncode == 0:
                bible, beats = _read_real_outputs()
                if beats:
                    return "\n".join(log_lines), _beats_to_md(
                        bible or json.dumps(project, ensure_ascii=False, indent=2), beats
                    ) + "\n\n> 由真实 Agent 流水线生成（含视频导演阶段，需 COMFYUI_API 可达）。"
                log_lines.append("[降级] 流水线未产出分镜文件，回退内置模板。")
        except Exception as e:  # 超时 / 导入失败 / 无 ComfyUI 等
            log_lines.append(f"[降级] 真实流水线不可用：{e}")
    else:
        log_lines.append(
            "[模式] 未配置 COMFYUI_API，直接使用内置模板企划 Demo（纯 CPU，秒级返回）。"
        )

    # 2) 降级：内置模板企划 Demo（纯 CPU，必然可跑）
    bible, beats = _template_plan(topic or project.get("theme", ""), style, int(max_scenes))
    note = (
        "\n\n> 当前为 **内置模板企划 Demo**（创空间默认 CPU 环境 / 未配置视频引擎时自动降级）。\n"
        "> 要跑真实流水线并生成视频，请在创空间环境变量中配置：\n"
        "> `COMFYUI_API`（视频服务地址）、`LLM_MODEL`（可选，本地 LLM）、"
        "`ENGINE_BACKEND`（comfyui_mmH3 / comfyui_ltx / skyreels），\n"
        "> 并在 requirements 中安装本项目完整依赖（含 torch）。详见仓库 README / DEPLOY.md。"
    )
    return "\n".join(log_lines), _beats_to_md(bible, beats) + note


ABOUT_MD = """# AI 电影 Agent（本地持续创作）

把"编剧 → 导演 → 引擎"的剧组协作，固化成一套**可运行、可无限续写**的电影 Agent 系统。

## 流水线（A→H）
```
A 资料采集 → B 知识沉淀 → C 概念企划 → D 图像提示词/关键帧
→ E 剧本创作 → F 去AI味润色 → G 视频导演 → H 自动发布
```
- 每个阶段 =「方法论(skills/) + 工具(agent/)」，由统一编排器 `MovieAgent` 串起。
- 基于 SkyReels-V2 Diffusion Forcing，**影片可无限续写**；逐镜自动质检，不达标换 seed 重出。
- 多引擎可切换：MiniMax H3 / LTX-2.5 / SkyReels / 远程 DGX Spark(Sol-H3)。
- 跨平台（Win/Linux/macOS+WSL，NVIDIA+AMD），配置驱动部署见 `deploy.py` / `DEPLOY.md`。

## 本 Space 演示
- 「创意企划」标签：输入一句话主题，Agent 产出世界观 Bible + 逐镜分镜。
- 默认 CPU 环境走内置模板 Demo；配置 `COMFYUI_API` 等变量后即跑真实流水线并出片。

## 合规
示例片《看见未来之前》为完全原创近未来赛博都市设定，画面 100% 本地生成，不含任何第三方影视的角色/台词/造型/剧照/片名。

源码：https://github.com/cpufreestyle/ai_movie_agent
参赛类别：进阶创作｜电影 Agent（AI+∞ 开发者创作大赛 · 第二期 · AI+影视流）
"""


def build_ui():
    with gr.Blocks(title="AI 电影 Agent") as demo:
        gr.Markdown("# AI 电影 Agent · 用 AI 提前看见未来")
        with gr.Tabs():
            with gr.Tab("创意企划"):
                topic = gr.Textbox(
                    label="一句话主题",
                    placeholder="近未来赛博都市，记忆与身份",
                    value="近未来赛博都市，记忆与身份",
                )
                max_scenes = gr.Slider(1, 8, value=3, step=1, label="分镜数")
                btn = gr.Button("运行 Agent 企划")
                log_box = gr.Textbox(label="运行日志", lines=8)
                res_box = gr.Markdown(label="产出：世界观 + 逐镜分镜")
                btn.click(
                    fn=run_agent, inputs=[topic, max_scenes],
                    outputs=[log_box, res_box],
                )
            with gr.Tab("关于 / 架构"):
                gr.Markdown(ABOUT_MD)
            with gr.Tab("部署与运行"):
                gr.Markdown(
                    "## 在创空间启用真实视频生成\n"
                    "1. 创空间环境变量：`COMFYUI_API=http://<你的ComfyUI>:8188`、"
                    "`ENGINE_BACKEND=comfyui_mmH3`（或 `comfyui_ltx`/`skyreels`）。\n"
                    "2. `LLM_MODEL`（可选）：本地 LLM，如 `qwen2.5:3b`（Ollama）。\n"
                    "3. requirements 安装本项目完整依赖（含 torch / skyreels 等）。\n"
                    "4. 重新点击「运行 Agent 企划」即跑完整 A–H 并出片。\n\n"
                    "详见仓库 `README.md` / `DEPLOY.md`。"
                )
    return demo


if __name__ == "__main__":
    build_ui().launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", 7860)))
