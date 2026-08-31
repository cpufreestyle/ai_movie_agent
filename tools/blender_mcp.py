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
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall((json.dumps(msg) + "\n").encode("utf-8"))
                buf = b""
                while b"\n" not in buf:
                    chunk = s.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
                line = buf.split(b"\n", 1)[0]
                return json.loads(line.decode("utf-8"))
        except Exception as e:
            print(f"  [blender-mcp] 通信失败: {e}", file=sys.stderr)
            return None

    def exec_code(self, code: str) -> Optional[str]:
        """在 Blender 内执行一段 bpy 代码，返回回执 message（含 stdout）。"""
        r = self._send({"type": "execute_code", "code": code})
        if r is None:
            return None
        # 兼容 fork：execute_blender_code + params
        if r.get("status") != "success" and "execute_blender_code" not in code:
            alt = self._send({"type": "execute_blender_code", "params": {"code": code}})
            if alt and alt.get("status") == "success":
                r = alt
        if r.get("status") != "success":
            print(f"  [blender-mcp] exec 错误: {r.get('message')}", file=sys.stderr)
            return None
        return r.get("message") or ""
