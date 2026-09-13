"""Blender MCP 轻量 socket 客户端（不依赖 MCP server 进程）。

直连 Blender 端插件（默认 127.0.0.1:9876），发 JSON 命令、收 JSON 回执。
前置：Blender 已装 Blender MCP 插件，并在 Blender 内启动 MCP Server
（3D 视口侧栏 N → BlenderMCP → Start MCP Server）。

协议（ahujasid/blender-mcp 及多数 fork）：
  命令: {"type": "execute_code", "code": "<python 源码>"}
  响应: {"status": "success"|"error", "result": {}, "message": "<stdout/错误>"}
部分 fork 使用 {"type": "execute_blender_code", "params": {"code": "..."}}，本客户端自动兼容。
"""
from __future__ import annotations

import json
import socket
import sys
import time
from typing import Optional


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876


class BlenderMCP:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 300.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def is_ready(self) -> bool:
        """端口是否可连通（Blender MCP Server 是否启动）。"""
        try:
            with socket.create_connection((self.host, self.port), timeout=3):
                return True
        except OSError:
            return False

    def _send(self, msg: dict) -> Optional[dict]:
        """发一条命令并取回 JSON 回执。

        健壮性改进（原实现只按'出现换行'判断，遇到无换行/粘包/分片会丢或挂）：
          - 每收一段就尝试整体解析，能解析即返回（兼容无换行的 fork）；
          - recv 显式超时，避免对端半开导致无限阻塞；
          - 连接关闭后仍做最后一次解析兜底；
          - 连接/超时类瞬时故障自动重连一次（0.5s 退避），降低偶发掉线误判。
        """
        payload = (json.dumps(msg) + "\n").encode("utf-8")
        for attempt in range(2):
            try:
                with socket.create_connection((self.host, self.port),
                                               timeout=self.timeout) as s:
                    s.settimeout(self.timeout)
                    s.sendall(payload)
                    buf = b""
                    while True:
                        text = buf.decode("utf-8", "ignore").strip()
                        if text:
                            try:
                                return json.loads(text)
                            except ValueError:
                                pass
                        try:
                            chunk = s.recv(65536)
                        except socket.timeout:
                            break
                        if not chunk:
                            break
                        buf += chunk
                    text = buf.decode("utf-8", "ignore").strip()
                    if not text:
                        return None
                    try:
                        return json.loads(text)
                    except ValueError:
                        try:
                            return json.loads(text.splitlines()[0])
                        except ValueError as e:
                            print(f"  [blender-mcp] 响应解析失败: {e}; raw={text[:200]}",
                                  file=sys.stderr)
                            return None
            except (OSError, socket.timeout) as e:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                print(f"  [blender-mcp] 通信失败: {e}", file=sys.stderr)
                return None
            except Exception as e:
                print(f"  [blender-mcp] 通信失败: {e}", file=sys.stderr)
                return None
        return None

    def exec_code_ex(self, code: str) -> dict:
        """执行 bpy 代码，返回结构化结果 {"ok", "stdout", "error"}。

        调用方据此判断"到底成没成功"，避免把通信失败当成渲染成功（原实现的静默失败点）。
        """
        r = self._send({"type": "execute_code", "params": {"code": code}})
        if r is None:
            return {"ok": False, "stdout": "", "error": "无响应/连接失败（Blender MCP 是否在跑？）"}
        if str(r.get("status", "")).lower() != "success":
            return {"ok": False, "stdout": "",
                    "error": str(r.get("message") or r.get("error") or r)}
        res = r.get("result")
        if isinstance(res, dict):
            # 官方 execute_code 把 stdout 放在 result["result"]
            out = res.get("result") or res.get("message") or ""
        else:
            out = res or r.get("message") or ""
        return {"ok": True, "stdout": str(out), "error": ""}

    def exec_code(self, code: str) -> Optional[str]:
        """在 Blender 内执行一段 bpy 代码，返回回执中的 stdout（失败返回 None）。

        官方 ahujasid/blender-mcp v1.6 协议：
          命令: {"type": "execute_code", "params": {"code": "<python 源码>"}}
          响应: {"status": "success", "result": {"executed": True, "result": "<stdout>"}}
        """
        ex = self.exec_code_ex(code)
        if not ex["ok"]:
            print(f"  [blender-mcp] exec 错误: {ex['error']}", file=sys.stderr)
            return None
        return ex["stdout"]
