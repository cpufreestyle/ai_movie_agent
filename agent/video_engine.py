"""视频引擎公共契约：统一 `generate()` 接口 + 配置段约定。

## 为什么需要这个模块

原先 `agent/agent.py` 用 `inspect.signature(self.engine.generate)` **反射探测**
「这个引擎支不支持 ref_video / control_video」，再据此拼 `**extra` 动态传参。
后果：

* 补全、类型检查、静态分析全部失效（参数是运行时才拼出来的）；
* 新增一项能力要改调用点（agent.py 的探测分支）；
* 各引擎的 `generate()` 签名不一致，"统一接口"只靠注释和"保留同名参数以维持一致"
  这种约定，加参数容易漏。

现在把契约显式写下来：

1. 所有引擎继承 `VideoEngine`，`generate()` 接受**同一套可选参数**（不支持的就地
   忽略并记一条日志，`_warn_ignored`）；
2. 能力用 `CAPABILITIES` 声明（能力名 == 关键字参数名），调用方按声明过滤，
   不再反射，也不靠猜；
3. 配置段别名（历史遗留）收敛到 `pick_engine_section()`，避免"某个模块只认
   `comfyui_mmH3`、另一个只认 `minimax_h3`"这类静默不一致。

## 各引擎能力矩阵

| 引擎 | ref_images | ref_video | control_video | fc_strength |
|---|---|---|---|---|
| MMH3Engine（ComfyUI H3） | ✓ | ✓ | ✓ | ✓ |
| SolH3Engine（远程 H3） | ✓ | ✓ | — | — |
| LTXEngine / SkyReelsEngine | — | — | — | — |
"""
from __future__ import annotations

from .llmutil import log

#: H3 配置段的历史别名（按优先级）。`comfyui_mmH3` 是新名，其余为兼容旧 config。
H3_SECTION_ALIASES = ("comfyui_mmH3", "minimax_h3", "comfyui_h3")
#: Sol-H3 配置段别名。
SOL_H3_SECTION_ALIASES = ("sol_h3", "sol_h3_spark")


def pick_engine_section(config: dict, *aliases: str) -> dict:
    """按优先级取第一个**非空**的 engine 子配置段（兼容历史别名）。

    空段视为未配置 —— 与旧写法 `a or b or c` 语义一致，避免
    `comfyui_mmH3: {}` 把后面有内容的 `minimax_h3` 挡掉。
    """
    eng = (config or {}).get("engine", {}) or {}
    for name in aliases:
        sec = eng.get(name)
        if sec:
            return sec
    return {}


def filter_engine_kwargs(engine, **candidates) -> tuple[dict, dict]:
    """按 `engine.CAPABILITIES` 过滤可选条件，返回 `(可传的, 被忽略的)`。

    调用方（agent.py）用这个替代 `inspect.signature` 反射：能传什么由引擎**显式
    声明**，而不是运行时猜签名。鸭子类型即可（不强制继承 VideoEngine），但必须
    有 `CAPABILITIES`；未声明的引擎一律不传条件 —— 与其猜，不如让它自己说清楚。
    """
    caps = getattr(engine, "CAPABILITIES", None)
    if caps is None:
        return {}, {k: v for k, v in candidates.items() if v is not None}
    keep, drop = {}, {}
    for key, value in candidates.items():
        if value is None:
            continue
        if key in caps:
            keep[key] = value
        else:
            drop[key] = value
    return keep, drop


class VideoEngine:
    """视频引擎基类（见模块 docstring）。

    子类必须提供 `is_ready()` / `generate()`，并声明 `CAPABILITIES`；
    `generate()` 的可选参数请与基类保持一致，多余的不支持项交给
    `_warn_ignored()` 处理，不要写 "保留同名参数以维持接口一致" 的注释了。
    """

    #: 日志前缀，如 "mmh3" / "ltx"。
    TAG = "engine"

    #: 支持的可选条件参数名（== generate 的关键字参数）。空集 = 只支持 T2V/I2V。
    CAPABILITIES: frozenset[str] = frozenset()

    # ---- 引擎必须提供的属性（仅作类型标注，子类实际赋值）----
    num_frames: int
    fps: int
    seed: int
    two_pass: bool
    resolution: str

    # ---------- 契约 ----------
    def is_ready(self) -> bool:
        raise NotImplementedError

    def generate(self, prompt: str, out_path: str, prev_clip: str | None = None,
                 seed: int | None = None, image: str | None = None,
                 two_pass: bool | None = None,
                 ref_video: str | None = None,
                 ref_images: list | None = None,
                 control_video: str | None = None,
                 fc_strength: float | None = None) -> str:
        """生成一段视频。各引擎必须覆写。"""
        raise NotImplementedError

    # ---------- 能力声明 ----------
    @classmethod
    def supported(cls, name: str) -> bool:
        """本引擎是否支持某个可选条件。"""
        return name in cls.CAPABILITIES

    def _warn_ignored(self, **candidates) -> dict:
        """记录被忽略的入参，返回忽略清单（供直接调用方如 run_series 复用）。

        静默丢弃是最坏的选项：「白模走位没生效」这类问题会变得极难定位，
        所以这里一定留一条日志。
        """
        _, drop = filter_engine_kwargs(self, **candidates)
        if drop:
            log(f"  [{self.TAG}] 忽略不支持的入参: {', '.join(sorted(drop))}"
                f"（该能力仅 MiniMax H3 支持）")
        return drop
