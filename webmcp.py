#!/usr/bin/env python
"""Web MCP 服务：把 AI 电影 Agent 的能力以 MCP 工具形式通过 HTTP 暴露。

需求背景（用户原话）：「加上 webmcp 可以调用 api 或者 本地模型进行对 agent 的使用」。

含义：把 Agent 的画质增强 / 客观度量 / 模型清单 / 出片 / 状态 / 元数据等能力，
包装成一组 **MCP（Model Context Protocol）工具**，并通过 **HTTP（Streamable HTTP /
SSE）** 暴露出去。这样无论是「云端 API 大模型」还是「本地模型（Ollama 等）」，
只要它支持 MCP 客户端，都能来驱动这个 Agent —— 配套驱动脚本见 `webmcp_client.py`。

运行（推荐，单解释器）：
    python cli.py mcp --port 9000                 # 走 streamable-http，绑 127.0.0.1
    python cli.py mcp --transport stdio           # 给本地 stdio 客户端用
也可直接：python webmcp.py --port 9000

架构（方案 A：单解释器）：
  * 本模块只依赖标准库 + `mcp`。`mcp` 装在仓库 agent venv 里（见记忆里默认 venv），
    因此 `cli.py mcp` 能直接在 agent venv 一键起服务，工具也可直接 import `agent` 模块。
  * 只有真正需要 import mcp 的 `build_server()` / `main()` 才懒加载 `mcp`，
    故本模块在「没有装 mcp 的解释器」里也能被 import（便于单测纯逻辑）。
  * 长任务（enhance_video / generate_clip 是分钟级）走**异步 job 模式**：
    工具立即返回 job_id，调用方用 `get_job_status` 轮询，避免阻塞 MCP 调用 / 客户端超时。
  * 默认绑定 127.0.0.1（仅本机）。要被远程 / API 模型调用时再改绑 0.0.0.0，
    并建议放在隧道 / VPN 之后（本版未内置鉴权）。
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import subprocess
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 运行环境定位
def agent_python() -> str:
    """执行 Agent 重活的解释器。可被环境变量 AGENT_PY 覆盖。

    默认走记忆里的仓库 venv；找不到则退化为当前解释器（此时若当前解释器没有
    Agent 依赖，对应的工具子进程会失败并在返回里报出来，不会让 MCP 服务崩）。
    """
    env = os.environ.get("AGENT_PY", "").strip()
    if env and os.path.exists(env):
        return env
    default = r"d:/ai sheare/repo/ai管理/.venv/Scripts/python.exe"
    if os.path.exists(default):
        return default
    return sys.executable


def _script(name: str) -> str:
    return os.path.join(HERE, name)


def _run(cmd: list[str], timeout: int = 3600):
    """跑子进程；超时返回 None（调用方据 None 判超时）。

    关键：子进程只跟本机服务（ComfyUI 8188 / ffmpeg / 本地文件）打交道，
    必须给它们注入 NO_PROXY，否则会沿用本机 HTTP_PROXY=127.0.0.1:7897
    把 127.0.0.1 也走代理，导致连 ComfyUI 时隧道挂死（增强/出片卡住）。
    只加 NO_PROXY、不清外部代理 —— generate_metadata 的 LLM 调用仍需走代理。
    """
    env = os.environ.copy()
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              errors="ignore", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None


# ---------------------------------------------------------------- ComfyUI 模型清单（stdlib 实现，避免 import agent）
def _comfy_open(url: str, timeout: float):
    """打开 URL 并**绕过代理**（与 agent/comfy_models.py 同因：本机 HTTP_PROXY
    会错误代理 127.0.0.1:8188，导致查不到模型）。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url, timeout=timeout)


def _comfy_object_info(api: str, node: str, timeout: float = 8.0) -> dict:
    url = f"{api.rstrip('/')}/object_info/{urllib.parse.quote(node)}"
    try:
        with _comfy_open(url, timeout) as r:
            return json.loads(r.read().decode("utf-8")) or {}
    except Exception:  # noqa: BLE001 - 连不上就当空，工具返回值里标明
        return {}


def _combo_options(info: dict, node: str, field: str) -> list[str]:
    """兼容 ComfyUI 的两种 COMBO 形态（官方 ['COMBO',{options}] 与第三方插件的
    [['a.pth',...],{default}]）。"""
    try:
        entry = info[node]["input"]["required"][field]
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(entry, list):
        return []
    for item in entry:
        if isinstance(item, list):
            return [str(x) for x in item]
    for item in entry:
        if isinstance(item, dict) and isinstance(item.get("options"), list):
            return [str(x) for x in item["options"]]
    return []


# ---------------------------------------------------------------- 工具实现（纯函数，便于单测，不依赖 mcp）
def list_comfy_models(api: str = "http://127.0.0.1:8188") -> dict:
    """列出 ComfyUI 实际已安装的超分 / RIFE 权重（以服务端 /object_info 为准）。

    返回 {ok, api, sr_models, rife_ckpts, note}。连不上或插件缺失时 ok=False 并说明。
    """
    sr = _combo_options(_comfy_object_info(api, "UpscaleModelLoader"),
                        "UpscaleModelLoader", "model_name")
    rife = _combo_options(_comfy_object_info(api, "RIFE VFI"), "RIFE VFI", "ckpt_name")
    ok = bool(sr or rife)
    note = "" if ok else "未连接到 ComfyUI，或对应节点（UpscaleModelLoader / RIFE VFI）未安装"
    return {"ok": ok, "api": api, "sr_models": sr, "rife_ckpts": rife, "note": note}


def quality_probe(ref: str, dist: str, mode: str = "vmaf",
                  max_sec: float = 0.0) -> dict:
    """客观画质度量：VMAF / PSNR / SSIM（ffmpeg 自带）。

    ref=基准片（如增强前的原片），dist=待评片（如增强后的片）。两者分辨率/时长
    不一致会自动对齐。返回 quality_probe.py 的 JSON 输出（含 mode/score/verdict/
    size/note），并补一个 ok 字段（score 非 None 为 True）。
    """
    cmd = [agent_python(), _script("quality_probe.py"), ref, dist,
           "--mode", mode, "--json"]
    if max_sec and max_sec > 0:
        cmd += ["--max-sec", str(max_sec)]
    r = _run(cmd, timeout=3600)
    if r is None:
        return {"ok": False, "note": "超时"}
    out = (r.stdout or "").strip()
    if out:
        try:
            data = json.loads(out)
            data["ok"] = data.get("score") is not None
            return data
        except Exception:  # noqa: BLE001
            pass
    err = (r.stderr or "").strip().splitlines()
    return {"ok": False, "note": (err[-1] if err else "无输出")[:300]}


def enhance_video(src: str, dst: str = "", profile: str = "", kind: str = "",
                  no_rife: bool = False, no_sr: bool = False, denoise: bool = False,
                  loudnorm: bool = False, multiplier: int = 2, chunk: int = 60,
                  api: str = "http://127.0.0.1:8188",
                  dry_run: bool = False) -> dict:
    """（同步实现 / 被异步任务调用）一键画质增强：RIFE 插帧 → ESRGAN 超分 → 统一高质量编码。

    依赖 ComfyUI(8188) 做插帧/超分；缺失时自动跳过并提示（dry_run 只打印将执行的命令）。
    返回 {ok, dst, dry_run, log_tail, note}。异步工具 `enhance_video` 会把它放进后台任务。
    """
    if not os.path.exists(src):
        return {"ok": False, "note": f"源文件不存在: {src}"}
    out = dst or (os.path.splitext(src)[0] + "_enhanced.mp4")
    cmd = [agent_python(), _script("enhance_video.py"), src, out]
    if profile:
        cmd += ["--profile", profile]
    if kind:
        cmd += ["--kind", kind]
    for flag, val in (("no_rife", no_rife), ("no_sr", no_sr),
                      ("denoise", denoise), ("loudnorm", loudnorm),
                      ("dry_run", dry_run)):
        if val:
            cmd.append("--" + flag.replace("_", "-"))
    cmd += ["--multiplier", str(multiplier), "--chunk", str(chunk), "--api", api]
    r = _run(cmd, timeout=3600)
    if r is None:
        return {"ok": False, "dst": out, "note": "超时（增强耗时过长，建议后台跑或缩小片段）"}
    ok = r.returncode == 0
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-15:]
    return {"ok": ok, "dst": out, "dry_run": dry_run,
            "log_tail": "\n".join(tail),
            "note": "" if ok else "增强失败，详情见 log_tail"}


def generate_clip(engine: str, prompt: str, image: str = "", frames: int = 0,
                  resolution: str = "", out: str = "",
                  api: str = "http://127.0.0.1:8188") -> dict:
    """（同步实现 / 被异步任务调用）用 ComfyUI 生成短视频片段。engine 仅 mmh3 / ltx。

    返回 {ok, out, log_tail, note}。长任务（Diffusion 出片）可能耗时数分钟；
    异步工具 `generate_clip` 会把它放进后台任务。
    """
    if engine not in ("mmh3", "ltx"):
        return {"ok": False, "note": "engine 仅支持 mmh3 / ltx"}
    out = out or os.path.join(HERE, "outputs", f"{engine}_clip.mp4")
    cmd = [agent_python(), _script("cli.py"), engine,
           "--prompt", prompt, "--out", out, "--api", api]
    if image:
        cmd += ["--image", image]
    if frames:
        cmd += ["--frames", str(frames)]
    if resolution:
        cmd += ["--resolution", resolution]
    r = _run(cmd, timeout=3600)
    if r is None:
        return {"ok": False, "out": out, "note": "超时"}
    ok = r.returncode == 0
    tail = (r.stderr or r.stdout or "").strip().splitlines()[-15:]
    return {"ok": ok, "out": out, "log_tail": "\n".join(tail),
            "note": "" if ok else "生成失败，详情见 log_tail"}


def agent_status(workdir: str = "outputs") -> dict:
    """读取 outputs/state.json 概览当前影片创作状态（不构造 MovieAgent，轻量）。

    返回 {ok, episode, scenes_done, theme, bible_characters,
    bible_has_visual_style, last_video, workdir, note}。
    """
    base = workdir if os.path.isabs(workdir) else os.path.join(HERE, workdir)
    sj = os.path.join(base, "state.json")
    if not os.path.exists(sj):
        return {"ok": False, "note": f"未找到 {sj}（先运行 cli.py run / pipeline 生成世界观）"}
    try:
        st = json.load(open(sj, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "note": f"state.json 解析失败: {e}"}
    bible = st.get("bible", {}) or {}
    return {
        "ok": True,
        "episode": st.get("episode"),
        "scenes_done": st.get("scenes_done"),
        "theme": st.get("theme"),
        "bible_characters": len(bible.get("characters", []) or []),
        "bible_has_visual_style": bool(bible.get("visual_style")),
        "last_video": st.get("last_video"),
        "workdir": base,
    }


def generate_metadata(ep: int, script: str = "outputs/series_script.json",
                      no_llm: bool = False) -> dict:
    """生成投稿元数据（标题候选/简介/标签/动态）。默认用 LLM，--no-llm 走规则法。

    返回 cli.py metadata --json 的输出（含 title/titles/tags/desc/...），并补 ok 字段。
    """
    cmd = [agent_python(), _script("cli.py"), "metadata",
           "--ep", str(ep), "--json"]
    if script:
        cmd += ["--script", script]
    if no_llm:
        cmd += ["--no_llm"]
    r = _run(cmd, timeout=600)
    if r is None:
        return {"ok": False, "note": "超时"}
    out = (r.stdout or "").strip()
    if out:
        try:
            return {"ok": True, **json.loads(out)}
        except Exception:  # noqa: BLE001
            pass
    err = (r.stderr or "").strip().splitlines()
    return {"ok": False, "note": (err[-1] if err else "无输出")[:300]}


# ---------------------------------------------------------------- 异步 job 机制（长任务不阻塞）
JOBS: dict[str, dict] = {}


async def _spawn_job(name: str, handler, kwargs: dict) -> str:
    """把同步 handler 放进后台任务，立即返回 job_id。"""
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"status": "queued", "name": name, "result": None,
                 "note": "", "log_tail": ""}
    loop = asyncio.get_running_loop()

    async def _worker():
        try:
            print(f"[webmcp:worker] START {jid} {name}", file=sys.stderr, flush=True)
            res = await asyncio.to_thread(handler, **kwargs)
            JOBS[jid].update({
                "result": res,
                "status": "done" if res.get("ok") else "failed",
                "note": res.get("note", ""),
                "log_tail": res.get("log_tail", ""),
            })
            print(f"[webmcp:worker] DONE {jid} status={JOBS[jid]['status']}",
                  file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001
            import traceback as _tb
            print(f"[webmcp:worker] ERR {jid} {type(e).__name__}: {e}\n"
                  + _tb.format_exc(), file=sys.stderr, flush=True)
            JOBS[jid].update({"status": "failed",
                               "note": f"{type(e).__name__}: {e}"})

    t = loop.create_task(_worker())
    JOBS[jid]["task"] = t
    print(f"[webmcp:spawn] {jid} {name} loop={id(loop)} task={id(t)}",
          file=sys.stderr, flush=True)
    return jid


def _queued(job_id: str) -> str:
    return json.dumps({"job_id": job_id, "status": "queued",
                       "note": "已入队，用 get_job_status 轮询结果"}, ensure_ascii=False)


async def enhance_video_job(src: str, dst: str = "", profile: str = "", kind: str = "",
                            no_rife: bool = False, no_sr: bool = False, denoise: bool = False,
                            loudnorm: bool = False, multiplier: int = 2, chunk: int = 60,
                            api: str = "http://127.0.0.1:8188",
                            dry_run: bool = False) -> str:
    """（异步）一键画质增强：RIFE 插帧 → ESRGAN 超分 → 统一高质量编码。

    立即返回 job_id，用 get_job_status 轮询结果（分钟级长任务，避免阻塞 MCP 调用）。
    参数同同步实现：src 必填；profile(anime/standard/high/draft)、kind(anime/real)、
    no_rife/no_sr/denoise/loudnorm、multiplier、chunk、api、dry_run。
    """
    if not os.path.exists(src):
        return json.dumps({"ok": False, "note": f"源文件不存在: {src}"})
    jid = await _spawn_job("enhance_video", enhance_video, dict(
        src=src, dst=dst, profile=profile, kind=kind, no_rife=no_rife,
        no_sr=no_sr, denoise=denoise, loudnorm=loudnorm,
        multiplier=multiplier, chunk=chunk, api=api, dry_run=dry_run))
    return _queued(jid)


async def generate_clip_job(engine: str, prompt: str, image: str = "", frames: int = 0,
                            resolution: str = "", out: str = "",
                            api: str = "http://127.0.0.1:8188") -> str:
    """（异步）用 ComfyUI 生成短视频片段。engine=mmh3(MiniMax H3 带原生立体声) 或 ltx(LTX-2.5)。

    立即返回 job_id，用 get_job_status 轮询结果。参数：engine/prompt 必填，
    image(可选首帧)、frames、resolution、out、api。
    """
    if engine not in ("mmh3", "ltx"):
        return json.dumps({"ok": False, "note": "engine 仅支持 mmh3 / ltx"})
    jid = await _spawn_job("generate_clip", generate_clip, dict(
        engine=engine, prompt=prompt, image=image, frames=frames,
        resolution=resolution, out=out, api=api))
    return _queued(jid)


async def get_job_status(job_id: str) -> str:
    """查询异步任务状态。返回 {job_id, status(queued|running|done|failed),
    name, result, note, log_tail}。status 为 done/failed 时 result 含工具最终返回值。"""
    j = JOBS.get(job_id)
    if not j:
        return json.dumps({"ok": False, "note": f"未知 job_id: {job_id}"}, ensure_ascii=False)
    return json.dumps({
        "job_id": job_id, "status": j["status"], "name": j.get("name"),
        "result": j.get("result"), "note": j.get("note"),
        "log_tail": j.get("log_tail", ""),
    }, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------- 工具注册表（单一事实源：单测 + 服务都读它）
# long=True 的工具有对应的异步 job 版（enhance_video_job / generate_clip_job）；
# async=True 表示 handler 本身就是 async（get_job_status）。
TOOL_SPECS: list[dict] = [
    {
        "name": "list_comfy_models",
        "description": "列出 ComfyUI 实际已安装的超分模型与 RIFE 插帧权重（以服务端 /object_info 为准）。"
                       "用于确认增强/出片前本地有哪些真实可用的 GPU 权重。",
        "handler": list_comfy_models,
    },
    {
        "name": "quality_probe",
        "description": "客观画质度量。比较基准片(ref)与待评片(dist)，返回 VMAF/PSNR/SSIM 分数与判读"
                       "(verdict)。用于验证画质增强是否真的变好（而非只是锐化）。",
        "handler": quality_probe,
    },
    {
        "name": "enhance_video",
        "description": "（异步 job）一键画质增强：RIFE 插帧 → ESRGAN 超分 → 统一高质量编码。"
                       "立即返回 job_id，用 get_job_status 轮询。需 ComfyUI(8188) 做插帧/超分；"
                       "缺失会自动跳过。可指定 profile(anime/standard/high/draft)、kind(anime/real)、"
                       "是否降噪/响度归一化。",
        "handler": enhance_video, "long": True,
    },
    {
        "name": "generate_clip",
        "description": "（异步 job）用 ComfyUI 生成短视频片段。engine=mmh3(MiniMax H3 带原生立体声)"
                       "或 ltx(LTX-2.5)。给定 prompt（与可选首帧 image）即可出片；立即返回 job_id。",
        "handler": generate_clip, "long": True,
    },
    {
        "name": "agent_status",
        "description": "读取当前影片创作状态概览（集号/已完成镜头数/世界观角色数/最近成片路径）。"
                       "轻量，不加载大模型。",
        "handler": agent_status,
    },
    {
        "name": "generate_metadata",
        "description": "为某一集生成 B 站投稿元数据（标题候选/简介/标签/动态）。默认调用 LLM，"
                       "可 no_llm 走纯规则法（离线确定性）。",
        "handler": generate_metadata,
    },
    {
        "name": "get_job_status",
        "description": "查询异步任务（enhance_video / generate_clip）的状态与结果。"
                       "传入 job_id，返回 status(queued/running/done/failed) 与最终 result。",
        "handler": get_job_status, "async": True,
    },
]


def _as_text_tool(handler):
    """把同步 handler（返回 dict）包成 MCP 工具（返回 JSON 字符串文本）。

    functools.wraps 会复制 __wrapped__ / __annotations__ / __doc__，
    因此 FastMCP 仍能从原函数推断出正确的参数 schema 与描述。
    """
    @functools.wraps(handler)
    def wrapper(*a, **kw):
        res = handler(*a, **kw)
        return json.dumps(res, ensure_ascii=False, indent=2)
    return wrapper


_LONG_ASYNC = {"enhance_video": enhance_video_job, "generate_clip": generate_clip_job}


# ---------------------------------------------------------------- 服务装配（懒加载 mcp）
def build_server(name: str = "ai-movie-agent", host: str = "127.0.0.1",
                 port: int = 9000):
    """构造 FastMCP 服务并注册全部工具。只有这里 import mcp。"""
    from mcp.server.fastmcp import FastMCP

    instructions = (
        "你是 AI 电影 Agent 的调度端。用户用自然语言提出目标（如「把某片做动漫风画质增强」、"
        "「评估增强前后画质差异」），你通过调用下方工具完成它。增强/出片是长任务，"
        "会立即返回 job_id，必须用 get_job_status 轮询到 done/failed 才算完成；"
        "完成后用 quality_probe 给出客观证据。不要编造结果，所有结论都来自工具返回。"
    )
    m = FastMCP(name, instructions=instructions, host=host, port=port)
    for spec in TOOL_SPECS:
        nm = spec["name"]
        if nm in _LONG_ASYNC:
            m.add_tool(_LONG_ASYNC[nm], name=nm, description=spec["description"])
        elif spec.get("async"):
            m.add_tool(spec["handler"], name=nm, description=spec["description"])
        else:
            m.add_tool(_as_text_tool(spec["handler"]), name=nm, description=spec["description"])
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI 电影 Agent Web MCP 服务")
    ap.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址（默认仅本机 127.0.0.1）")
    ap.add_argument("--port", type=int, default=9000, help="HTTP 监听端口")
    ap.add_argument("--transport", default="streamable-http",
                    choices=["streamable-http", "sse", "stdio"],
                    help="MCP 传输：streamable-http(默认,网页/远程) / sse(旧版HTTP) / stdio(本地)")
    args = ap.parse_args(argv)

    m = build_server(host=args.host, port=args.port)
    if args.transport == "stdio":
        m.run(transport="stdio")
    else:
        m.run(transport=args.transport)  # host/port 已在构造函数里设置
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
