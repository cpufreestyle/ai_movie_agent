"""锚定资产业务逻辑：outputs/anchor 产物扫描 + 「一键生成」条目目录。

产物由根目录脚本 `gen_anchor_assets.py`（MiniMax H3，与成片同源）生成：
主角三视图（mira_front / mira_side / mira_back）→ 各场景空镜 → 再由正面图裁出
`mira_anchor.png` 作换脸 / 身份锚定。

**为什么不 import 那个脚本**：`gen_anchor_assets` 顶层 `import run_series as rs`，
后者会在导入时建目录并探测 ffmpeg —— 不该发生在 Web 服务的启动路径上。代价是
这里的 `SHOT_SPECS` 与脚本的 `SHOTS` 存在重复，**由 `tests/test_anchor_assets.py`
断言两者名称一致**兜住漂移（漏一张图在界面上是静默的，必须有测试拦）。
"""
from __future__ import annotations

import glob
import os

from ..state import WORKDIR, load_config, safe_under

#: ComfyUI 地址兜底（config.engine.comfyui_mmH3.api 未配时用）
DEFAULT_API = "http://127.0.0.1:8188"
#: 与 gen_anchor_assets.py 的 --resolution 默认值一致
DEFAULT_RESOLUTION = "1024x576"
#: 显存不足时降到 768x448（脚本 help 里给的建议档）
RESOLUTIONS = ("1024x576", "768x448")

VIEW_GROUP = "view"
SCENE_GROUP = "scene"
#: 分组展示顺序（三视图在最前 —— 它是默认勾选项）
GROUP_ORDER = ((VIEW_GROUP, "三视图"), (SCENE_GROUP, "场景图"))

#: 脚本自动派生、不可直接勾选生成的产物：(名称, 标签, 说明)
DERIVED = (
    ("mira_anchor", "人脸锚定 face",
     "由三视图正面图裁出，供换脸 / 身份相似度（--anchor auto 首选它）"),
)

#: (名称, 分组, 中文标签, 悬停说明) —— 名称必须与 gen_anchor_assets.SHOTS 一致
SHOT_SPECS = (
    ("mira_front", VIEW_GROUP, "正面 front",
     "中性背景正面全身。mira_anchor 人脸锚定图由它裁出，所以缺它就没法做身份锚定。"),
    ("mira_side", VIEW_GROUP, "侧面 side",
     "侧脸轮廓，锁住发型长度与外套侧面剪影。"),
    ("mira_back", VIEW_GROUP, "背面 back",
     "背视图，锁住后脑发型与外套背面（转身镜头靠它）。"),
    ("scene_neon_street", SCENE_GROUP, "霓虹街景",
     "雨夜霓虹大街（第 1 集开场）。"),
    ("scene_shop_exterior", SCENE_GROUP, "店铺外景",
     "记忆铺外景，橱窗里一排发光的记忆瓶。"),
    ("scene_shop_interior", SCENE_GROUP, "店铺内景",
     "昏暗店内，货架 + 柜台 + 冷蓝光。"),
    ("scene_reading_chair", SCENE_GROUP, "读忆椅",
     "记忆读取椅与冷蓝扫描光。"),
    ("scene_meadow", SCENE_GROUP, "草地闪回",
     "金色时刻的阳光下草地（闪回镜头）。"),
    ("scene_chip", SCENE_GROUP, "芯片特写",
     "掌心里的黑色记忆芯片，冷蓝轮廓光。"),
    ("scene_door", SCENE_GROUP, "店门",
     "从店内向外看的雨夜店门。"),
)

#: 产物图册里只预览这些扩展名
_IMAGE_EXT = (".png", ".jpg", ".jpeg")


def anchor_dir() -> str:
    """锚定资产目录（outputs/anchor）。"""
    return os.path.join(WORKDIR, "anchor")


def generatable_names() -> list[str]:
    """可勾选生成的条目名（与 gen_anchor_assets.SHOTS 同名同序）。"""
    return [name for name, *_ in SHOT_SPECS]


def default_names() -> list[str]:
    """默认勾选项：**三视图**。

    用户规则（2026-09-15）：一键生成默认就出三视图 —— 它们是 I2V 首帧锚定与
    ref_images 身份参考的主力，场景图属于按需补充。
    """
    return [name for name, grp, *_ in SHOT_SPECS if grp == VIEW_GROUP]


def comfy_api() -> str:
    """H3 用的 ComfyUI 地址（走引擎配置解析，兼容 comfyui_mmH3 / minimax_h3 别名）。"""
    try:
        from agent.video_engine import H3_SECTION_ALIASES, pick_engine_section
        sec = pick_engine_section(load_config(), *H3_SECTION_ALIASES)
        return str(sec.get("api") or DEFAULT_API)
    except Exception:            # noqa: BLE001 - 配置缺失不该让页面 500
        return DEFAULT_API


def comfy_ready(api: str | None = None, timeout: float = 3.0) -> bool:
    """ComfyUI 是否可达（GET / 返回 200）。

    只探测**一次**、短超时：这只是页面上的提示，真门槛在 gen_anchor_assets.py
    里（它自己会 `is_ready()` 并在未就绪时报错退出）。不该为一次 GET 卡 10s。
    """
    url = (api or comfy_api()).rstrip("/") + "/"
    try:
        import requests
        return requests.get(url, timeout=timeout).status_code == 200
    except Exception:            # noqa: BLE001
        return False


def _file_info(path: str) -> dict:
    st = os.stat(path)
    return {"name": os.path.basename(path),
            "size_kb": round(st.st_size / 1024, 1),
            "mtime": st.st_mtime}


def artifacts(limit: int = 80) -> list[dict]:
    """anchor 目录下的图片产物，按修改时间倒序（图册用；只扫本层，不含 _tmp 中间件）。"""
    base = anchor_dir()
    items = []
    for path in glob.glob(os.path.join(base, "*")):
        if not os.path.isfile(path):
            continue
        if not path.lower().endswith(_IMAGE_EXT):
            continue
        items.append(_file_info(path))
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def _entry(name: str, label: str, hint: str, info: dict) -> dict:
    f = info.get(f"{name}.png")
    return {"name": name, "file": f"{name}.png", "label": label, "hint": hint,
            "exists": bool(f), "size_kb": (f or {}).get("size_kb", 0),
            "default": name in default_names()}


def shot_groups() -> list[dict]:
    """按分组给出可勾选条目（含「是否已存在」），供前端渲染勾选框 + 状态。"""
    info = {x["name"]: x for x in artifacts()}
    groups = []
    for key, label in GROUP_ORDER:
        items = [_entry(name, cn, hint, info)
                 for name, grp, cn, hint in SHOT_SPECS if grp == key]
        groups.append({"key": key, "label": label, "items": items})
    return groups


def derived_items() -> list[dict]:
    """脚本自动派生、不可直接勾选生成的产物。"""
    info = {x["name"]: x for x in artifacts()}
    out = []
    for name, label, hint in DERIVED:
        f = info.get(f"{name}.png")
        out.append({"name": name, "file": f"{name}.png", "label": label,
                    "hint": hint, "exists": bool(f),
                    "size_kb": (f or {}).get("size_kb", 0)})
    return out


def has_index() -> bool:
    """脚本生成的锚定图册 index.html 是否存在。"""
    return os.path.isfile(os.path.join(anchor_dir(), "index.html"))


def safe_path(name: str) -> str:
    """把 WebUI 传来的相对文件名解析到 anchor 目录内；越界（如 ../）返回空串。

    复用 `state.safe_under`（已归一化反斜杠）—— 与 views/blocking.py 的
    /api/blocking/file 同一套防护。调用方据此回 400。
    """
    return safe_under(anchor_dir(), name)


__all__ = [
    "DEFAULT_API", "DEFAULT_RESOLUTION", "RESOLUTIONS", "SHOT_SPECS",
    "anchor_dir", "artifacts", "comfy_api", "comfy_ready", "default_names",
    "derived_items", "generatable_names", "has_index", "safe_path", "shot_groups",
]
