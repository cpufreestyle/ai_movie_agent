#!/usr/bin/env python
"""webmcp 的 LLM 驱动端：用「云端 API 大模型」或「本地模型（Ollama）」驱动 Agent。

对应需求里的「可以调用 api 或者 本地模型进行对 agent 的使用」：
本脚本作为 **MCP 客户端**连上 `webmcp.py` 暴露的服务，拉取工具清单，再用一个大模型
（API / 本地）做 ReAct 循环 —— 把用户的自然语言目标翻译成对 Agent 工具的调用，
直到任务完成。

两大模型来源（二选一，环境变量或 --llm 指定）：
  * API 模式（默认）：OpenAI 兼容接口。
        OPENAI_API_KEY=sk-...   OPENAI_BASE_URL=https://api.openai.com/v1
        OPENAI_MODEL=gpt-4o-mini
  * 本地模式：Ollama（本机 11434）。
        OLLAMA_URL=http://127.0.0.1:11434   OLLAMA_MODEL=qwen2.5:7b

LLM 调用只用标准库 urllib（不引入额外依赖）；MCP 客户端用本机已有的 `mcp` SDK。

示例：
    python webmcp_client.py "把 outputs/movie_final.mp4 做动漫风画质增强" --llm openai
    python webmcp_client.py "评估 outputs/raw.mp4 与 outputs/enhanced.mp4 的画质差异" --llm ollama
    python webmcp_client.py "现在影片创作到第几集、做了多少镜？" --server http://127.0.0.1:9000/mcp
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SYSTEM_PROMPT = (
    "你是一个 AI 电影 Agent 的调度助手。你可以通过调用工具来完成用户的自然语言目标。"
    "可用工具由服务端动态提供（画质增强、客观画质度量、ComfyUI 模型清单、视频生成、"
    "创作状态查询、投稿元数据生成等）。规则：\n"
    "1) 先把用户目标拆成工具调用；增强/出片是长任务，调用后等待结果。\n"
    "2) 涉及「变好没」必须用 quality_probe 给出 VMAF/PSNR/SSIM 客观证据，不要主观臆断。\n"
    "3) 调用前确认必要输入（如文件是否存在、本地 GPU 权重是否齐备）。\n"
    "4) 所有结论必须来自工具返回的真实数据，不得编造。\n"
    "5) 任务完成后，用一段中文向用户总结做了什么、结果如何。"
)


# ---------------------------------------------------------------- LLM 调用（urllib only）
def _post_json(url: str, payload: dict, headers: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"LLM HTTP {e.code}: {body}") from e
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"LLM 请求失败: {e}") from e


def _openai_chat(messages: list, tools: list, model: str, base: str, api_key: str) -> dict:
    url = base.rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "tools": tools,
               "tool_choice": "auto", "temperature": 0.2}
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    data = _post_json(url, payload, headers)
    msg = data["choices"][0]["message"]
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}


def _ollama_chat(messages: list, tools: list, model: str, base: str) -> dict:
    url = base.rstrip("/") + "/api/chat"
    payload = {"model": model, "messages": messages, "tools": tools, "stream": False}
    headers = {"Content-Type": "application/json"}
    data = _post_json(url, payload, headers)
    msg = data.get("message", {})
    return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}


def llm_chat(messages: list, tools: list, provider: str, cfg: dict) -> dict:
    """统一入口：provider='openai' 走 API，provider='ollama' 走本地。"""
    if provider == "ollama":
        return _ollama_chat(messages, tools, cfg["model"], cfg["base"])
    return _openai_chat(messages, tools, cfg["model"], cfg["base"], cfg["api_key"])


def _norm_tool_calls(raw, provider: str) -> list[dict]:
    """把各家 LLM 的 tool_calls 规范成统一形状：{id, name, arguments(dict)}。"""
    out = []
    for tc in raw or []:
        fn = tc.get("function", {})
        name = fn.get("name") or tc.get("name") or ""
        args = fn.get("arguments") or {}
        if isinstance(args, str):  # 个别实现把 arguments 返回成 JSON 字符串
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        tid = tc.get("id") or f"call_{len(out)}"
        out.append({"id": tid, "name": name, "arguments": args})
    return out


# ---------------------------------------------------------------- MCP 客户端（async）
def _extract_text(result) -> str:
    parts = []
    for c in getattr(result, "content", []) or []:
        t = getattr(c, "text", None)
        if t is not None:
            parts.append(t)
    return "\n".join(parts)


def _job_id_from(text: str):
    """若工具返回是 JSON 且含 job_id（长任务已入队），返回该 id，否则 None。"""
    try:
        d = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    return d.get("job_id")


async def _poll_job(session, job_id: str, max_polls: int = 120, interval: float = 5.0) -> str:
    """长任务返回 job_id 后，轮询 get_job_status 直到 done/failed。"""
    for _ in range(max_polls):
        await asyncio.sleep(interval)
        try:
            r = await session.call_tool("get_job_status", {"job_id": job_id})
            text = _extract_text(r)
        except Exception as e:  # noqa: BLE001
            return f"[轮询异常] {e}"
        try:
            d = json.loads(text)
        except Exception:  # noqa: BLE001
            return text
        st = d.get("status")
        print(f"[webmcp-client]   job {job_id} 状态={st}")
        if st in ("done", "failed"):
            return text
    return text


async def drive(goal: str, server_url: str, provider: str, cfg: dict,
                max_steps: int = 8) -> str:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(server_url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_res = await session.list_tools()
            mcp_tools = [{"name": t.name, "description": t.description or "",
                          "input_schema": t.inputSchema} for t in tools_res.tools]
            llm_tools = [{
                "type": "function",
                "function": {"name": t["name"], "description": t["description"],
                             "parameters": t["input_schema"]},
            } for t in mcp_tools]

            print(f"[webmcp-client] 已连接 {server_url}，可用工具 {len(mcp_tools)} 个："
                  + ", ".join(t["name"] for t in mcp_tools))
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": goal}]

            for step in range(1, max_steps + 1):
                resp = llm_chat(messages, llm_tools, provider, cfg)
                tcs = _norm_tool_calls(resp["tool_calls"], provider)
                if not tcs:
                    print(f"[webmcp-client] 第 {step} 步：模型给出最终答复")
                    return resp["content"] or "（模型未返回文本）"

                # 把助手这轮的工具调用记录进对话（OpenAI 格式；Ollama 也能识别）
                asst_msg = {"role": "assistant",
                            "content": resp["content"] or "",
                            "tool_calls": [
                                {"id": tc["id"], "type": "function",
                                 "function": {"name": tc["name"],
                                              "arguments": json.dumps(tc["arguments"], ensure_ascii=False)}}
                                for tc in tcs
                            ]}
                messages.append(asst_msg)

                for tc in tcs:
                    print(f"[webmcp-client] 调用工具 {tc['name']} 参数={tc['arguments']}")
                    try:
                        res = await session.call_tool(tc["name"], tc["arguments"])
                        text = _extract_text(res)
                    except Exception as e:  # noqa: BLE001
                        text = f"[工具调用异常] {e}"
                    # 长任务（enhance_video / generate_clip）返回 job_id → 自动轮询
                    jid = _job_id_from(text)
                    if jid:
                        text = await _poll_job(session, jid)
                    print(f"[webmcp-client]   -> {text[:400]}")
                    messages.append({"role": "tool", "tool_call_id": tc["id"],
                                     "content": text})

            # 步数用尽：让模型基于已有结果做总结
            final = llm_chat(messages, llm_tools, provider, cfg)
            return (final["content"] or "（已达最大步数，未给出总结）")


# ---------------------------------------------------------------- 配置解析 + CLI
def _provider_config(provider: str, model_arg: str) -> dict:
    if provider == "ollama":
        return {
            "base": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
            "model": model_arg or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        }
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key and provider == "openai":
        print("[warn] 未检测到 OPENAI_API_KEY，API 模式可能鉴权失败。", file=sys.stderr)
    return {
        "base": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "model": model_arg or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "api_key": api_key,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用 API / 本地模型驱动 AI 电影 Agent（MCP 客户端）")
    ap.add_argument("goal", help="自然语言目标，例如「把 outputs/movie_final.mp4 做动漫风画质增强」")
    ap.add_argument("--server", default="http://127.0.0.1:9000/mcp",
                    help="webmcp 服务地址（streamable-http）")
    ap.add_argument("--llm", default=os.environ.get("WEBMCP_LLM", "openai"),
                    choices=["openai", "ollama"], help="大模型来源：openai(API) / ollama(本地)")
    ap.add_argument("--model", default="", help="模型名（覆盖环境变量默认值）")
    ap.add_argument("--max-steps", type=int, default=8, help="最多工具调用轮数")
    args = ap.parse_args(argv)

    cfg = _provider_config(args.llm, args.model)
    print(f"[webmcp-client] 模式={args.llm} 模型={cfg['model']} 服务={args.server}")
    try:
        answer = asyncio.run(drive(args.goal, args.server, args.llm, cfg, args.max_steps))
    except KeyboardInterrupt:
        print("\n[webmcp-client] 已中断")
        return 130
    print("\n================ Agent 执行总结 ================")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
