"""系列连贯出片（run_series.py）：表单 → argv 翻译 + 系列成片扫描。

尾帧续写在本项目里有**两条独立机制**，都是「拿上一镜的末帧当下一镜的起始帧」：

  1. ``--i2v``：每一镜都接上一镜末帧（泛化，含**集间** —— 第 N 集首镜会去抽
     第 N-1 集成片的末帧）。脚本里默认**关闭**。
  2. ``--anchor-mode chain``：只作用于**角色镜**（含 Mira 的镜）—— 首镜用锚定图，
     后续接上一个角色镜的末帧，兼顾连贯与人物自然演变。

为什么默认关闭 ``--i2v``：2026-09-07 实测教训 —— I2V 起始图权重过高，模型会忽略
新 prompt 的场景变化，18 镜画面趋同、与旁白脱节。所以 WebUI 把它做成显式勾选，
并把这段警告写进 tooltip，而不是默认打开。

**为什么不 import run_series**：它顶层 `import run_series` 会建目录 + 探测 ffmpeg，
不该发生在 Web 服务启动路径上（与 `services/anchor.py` 同一个理由）。这里只拼 argv，
参数语义由 `tests/test_series_run.py` 锁住。
"""
from __future__ import annotations

import glob
import os

from ..state import HERE, WORKDIR, load_config
from .pipeline import normalize_shot_indices

#: 出片脚本（仓库根目录）
SCRIPT = "run_series.py"

#: (值, 标签) —— 0 表示按 series_shots.json 依次出全部集
EPISODES = ((0, "全部集"), (1, "第 1 集"), (2, "第 2 集"), (3, "第 3 集"))
ENGINES = (("mmh3", "mmh3 · MiniMax H3（Turbo 4 步，自带立体声）"),
           ("ltx", "ltx · LTX-2.5"))
ANCHOR_MODES = (("first", "first · 每个角色镜都用锚定图（一致性最强）"),
                ("chain", "chain · 首镜锚定 + 后续接上一角色镜末帧"))
ANCHORS = (("", "不锚定"), ("auto", "auto · 自动查找角色卡"))

DEFAULT_ENGINE = "mmh3"
DEFAULT_ANCHOR_MODE = "first"

#: 系列成片产物：outputs/ep{N}_series_film.mp4
FILM_GLOB = "ep*_series_film.mp4"


def out_dir() -> str:
    """成片输出目录（run_series --out-dir 的默认值）。"""
    return WORKDIR


def series_films() -> list[dict]:
    """已有的系列成片，按文件名排序（ep1/ep2/ep3）。"""
    items = []
    for path in sorted(glob.glob(os.path.join(WORKDIR, FILM_GLOB))):
        items.append({"name": os.path.basename(path),
                      "size_mb": round(os.path.getsize(path) / 2 ** 20, 2)})
    return items


def default_anchor() -> str:
    """锚定图默认值：config.series.character_anchor 配了就用 auto，否则不锚定。

    不硬编码成 auto，是因为 `--anchor` 会改变出片行为（I2V 起始图会压过 prompt），
    只有用户显式配置过角色卡时才默认开启。
    """
    try:
        val = str((load_config().get("series") or {}).get("character_anchor") or "").strip()
    except Exception:                 # noqa: BLE001 - 配置坏了不该让页面 500
        return ""
    return "auto" if val else ""


def _values(pairs) -> set:
    """选项值集合（**不做 str() 转换** —— EPISODES 的值是 int，转成字符串会永远匹配不上）。"""
    return {v for v, _ in pairs}


def _as_ep(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def validate(body: dict) -> list[str]:
    """校验表单；返回错误消息列表（空 = 通过）。"""
    errors: list[str] = []
    engine = str(body.get("engine") or DEFAULT_ENGINE)
    if engine not in _values(ENGINES):
        errors.append(f"未知引擎 {engine}（可选 {'/'.join(sorted(_values(ENGINES)))}）")
    mode = str(body.get("anchor_mode") or DEFAULT_ANCHOR_MODE)
    if mode not in _values(ANCHOR_MODES):
        errors.append(f"未知接续模式 {mode}（可选 first/chain）")
    anchor = str(body.get("anchor") or "").strip()
    if anchor and anchor != "auto" and not os.path.exists(
            anchor if os.path.isabs(anchor) else os.path.join(HERE, anchor)):
        errors.append(f"锚定图路径不存在：{anchor}")
    ep = _as_ep(body.get("ep"), 0)
    if ep not in _values(EPISODES):
        errors.append(f"集号只能是 0~3（收到 {ep}）")
    if body.get("concat"):
        c = _as_ep(body.get("concat"), 0)
        if c not in (1, 2, 3):
            errors.append(f"拼接集号只能是 1~3（收到 {c}）")
        if str(body.get("only") or "").strip() or body.get("force") or body.get("i2v"):
            errors.append("「只拼接」与 镜号/强制重出/尾帧续写 互斥 —— "
                          "--concat 会直接跳过这些开关，请分开执行")
    only_raw = str(body.get("only") or "").strip()
    if only_raw and not normalize_shot_indices(only_raw):
        errors.append(f"只重出镜号里有非法值：{only_raw}（要 1 起的整数，如 14,15,16）")
    return errors


def build_argv(body: dict) -> list[str]:
    """把 WebUI 表单体翻译成 run_series.py 的 argv（纯函数，便于单测）。

    argv[0] 是脚本名；调用方补 `[sys.executable, "-u"]` 前缀。
    """
    argv = [SCRIPT]
    if body.get("concat"):
        argv += ["--concat", str(_as_ep(body.get("concat"), 1))]
    else:
        argv += ["--ep", str(_as_ep(body.get("ep"), 0))]
    argv += ["--engine", str(body.get("engine") or DEFAULT_ENGINE)]
    if body.get("i2v"):
        argv.append("--i2v")
    argv += ["--anchor-mode", str(body.get("anchor_mode") or DEFAULT_ANCHOR_MODE)]
    anchor = str(body.get("anchor") or "").strip()
    if anchor:
        argv += ["--anchor", anchor]
    idx = normalize_shot_indices(body.get("only"))
    if idx:
        argv += ["--only", ",".join(str(i) for i in idx)]
    if body.get("force"):
        argv.append("--force")
    return argv


__all__ = [
    "ANCHORS", "ANCHOR_MODES", "DEFAULT_ANCHOR_MODE", "DEFAULT_ENGINE", "ENGINES",
    "EPISODES", "SCRIPT", "build_argv", "default_anchor", "out_dir", "series_films",
    "validate",
]
