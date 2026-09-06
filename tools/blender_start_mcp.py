"""在 Blender(GUI) 启动时启用 Blender MCP 插件并开启其 MCP Server。

用法（注意：必须 GUI 模式，不能加 --background）：
    blender.exe --python tools/blender_start_mcp.py

背景与坑：
  1) 官方 addon 的 BlenderMCPServer.start() 在 bpy.app.background 时拒绝启动
     （命令靠主线程 bpy.app.timers 消费，无 GUI 事件循环则永不执行）。
  2) 便携版 Blender 的 addons 扫描路径不是 <blender>/4.5/scripts/addons，
     addon_utils.enable() 在这种情况下会静默失败（不抛异常但模块根本没加载）。
     因此这里把插件文件所在目录加入 sys.path 后直接 import，不依赖扫描路径。
  3) 插件 register() 末尾自带 auto-start（默认端口 9876）；若其未生效，
     则再手动实例化官方 BlenderMCPServer 并 start()，等价于点侧栏 "Connect to Claude"。

过程日志写到 outputs/blender_mcp_start.log（Blender GUI 在 Windows 不显示 stdout）。
"""
import os
import socket
import sys
import traceback

import addon_utils
import bpy

MODULE = "blender_mcp_addon"
PORT = 9876
LOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "outputs", "blender_mcp_start.log",
)


def log(msg):
    line = str(msg)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass
    print(line, flush=True)


def _port_open() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def _enable_and_start():
    log(f"=== run: background={bpy.app.background} ===")

    # 0) 让插件文件所在目录可被 import（便携版扫描不到 addons 目录时的兜底）
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    log(f"tools_dir={tools_dir}")

    # 1) 走 Blender 插件系统启用（成功则 UI 里也能看到插件）
    try:
        addon_utils.enable(MODULE, default_set=True, persistent=True)
        log("ADDON_ENABLED ok")
    except Exception:  # noqa: BLE001
        log("ADDON_ENABLE_FAIL\n" + traceback.format_exc())

    # 2) 直接 import 插件模块
    mod = sys.modules.get(MODULE)
    if mod is None:
        try:
            mod = __import__(MODULE)
            log("module imported")
        except Exception:  # noqa: BLE001
            log("MODULE_IMPORT_FAIL\n" + traceback.format_exc())
            return None

    # 3) register()：注册 Scene 属性/面板/operator，且其末尾自带 auto-start
    if not hasattr(bpy.types.Scene, "blendermcp_port"):
        try:
            mod.register()
            log("register() ok (contains auto-start)")
        except Exception:  # noqa: BLE001
            log("register() failed\n" + traceback.format_exc())
    else:
        log("already registered")

    # 4) 若 auto-start 没起来，手动启动官方 server
    srv = getattr(bpy.types, "blendermcp_server", None)
    if srv is not None and getattr(srv, "running", False):
        log("server running via addon auto-start")
    else:
        try:
            srv = mod.BlenderMCPServer(port=PORT)
            bpy.types.blendermcp_server = srv
            srv.start()
            log(f"manual start() -> running={srv.running}")
        except Exception:  # noqa: BLE001
            log("SERVER_START_FAIL\n" + traceback.format_exc())
            return None

    log(f"PORT_{PORT}_OPEN={_port_open()}")
    return None


bpy.app.timers.register(_enable_and_start, first_interval=3.0)
log("timer registered, will enable+start in 3s")
