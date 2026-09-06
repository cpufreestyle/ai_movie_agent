"""Blender 内运行的 MCP 兼容服务端（监听 127.0.0.1:9876）。

用途：不依赖第三方 Blender MCP 插件，也能让 agent/blocking.py 的 socket 客户端
直接工作——协议与主流 blender-mcp 插件一致：

  请求: {"type": "execute_code", "code": "..."}
        或 {"type": "execute_blender_code", "params": {"code": "..."}}
  响应: {"status": "success"|"error", "message": "<stdout/异常>", "result": {}}

启动方式（后台常驻，无需 GUI）：
    blender.exe --background --python tools/blender_server.py
"""
import contextlib
import io
import json
import socket
import sys
import traceback

import bpy  # Blender 内置模块，仅在 Blender 进程内可用

HOST = "127.0.0.1"
PORT = 9876


def _exec(code: str) -> dict:
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(code, "<blender-mcp>", "exec"), globals())
        return {"status": "success", "message": out.getvalue(), "result": {}}
    except Exception:
        return {"status": "error", "message": traceback.format_exc(), "result": {}}


def _handle(conn: socket.socket) -> None:
    try:
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        if not buf:
            return
        msg = json.loads(buf.decode("utf-8"))
        t = msg.get("type")
        code = msg.get("code") or (msg.get("params") or {}).get("code")
        if t in ("execute_code", "execute_blender_code") and code:
            resp = _exec(code)
        else:
            resp = {"status": "error", "message": f"unsupported type: {t}", "result": {}}
        conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
    except Exception:
        try:
            conn.sendall((json.dumps({"status": "error",
                                      "message": traceback.format_exc()}) + "\n").encode("utf-8"))
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(8)
    print(f"[blender-mcp-server] listening on {HOST}:{PORT}", flush=True)
    sys.stdout.flush()
    while True:
        conn, _addr = srv.accept()
        _handle(conn)


main()
