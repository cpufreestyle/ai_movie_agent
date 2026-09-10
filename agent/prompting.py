"""提示词相关的默认值与工具：负向提示词库 + 引擎能力判定。

为什么独立成模块：

- 负向提示词原先是空的（`engine.comfyui_ltx.negative: ''`），而「把变形 / 多手 /
  水印 / 文字写全」是投入产出比最高的画质改善手段之一
  （见 `docs/3d_control_pipeline_plan.md` §七.4）。
- 各引擎**是否真的有负向提示词输入并不一致**：
    · LTX-2.5：工作流里有 negative 节点，可注入；
    · MiniMax H3：T8 采样器走 flow matching / shift，**没有 negative 输入**
      （BasicGuider 只有 model + conditioning），配了也不会生效。
  集中在这里判定，省得以后再有人给 H3 配了负向、却因"改了没反应"排查半天。
"""
from __future__ import annotations

# 负向提示词预设。config.prompting.negative 可以写预设名（如 "quality"），
# 也可以直接写一整串（不以预设名开头时才按字面处理）。
NEGATIVE_PRESETS: dict = {
    # 通用画质：抓形变 / 多肢 / 糊 / 水印文字
    "quality": (
        "deformed face, mutated hands, extra limbs, extra fingers, fused fingers, "
        "distorted anatomy, morphing, warped features, blurry, lowres, jpeg artifacts, "
        "watermark, signature, text, logo, oversaturated"
    ),
    # 身份稳定：抓"换脸 / 换发型换衣服"，配合角色锚定图使用
    "identity": (
        "different face, different hairstyle, changing clothes, changing appearance, "
        "face swap, inconsistent identity, multiple people, crowd of faces"
    ),
    # 动漫风格（本项目默认风格）：压住真人/3D 质感的漂移
    "anime": (
        "photorealistic, 3d render, live action, realistic skin texture, "
        "western cartoon style, chibi, low quality, sketch lines, unfinished"
    ),
}

DEFAULT_PRESET = "quality"

# 没有负向提示词输入的引擎（H3 走 shift/flow matching，BasicGuider 无 negative 端）
_NO_NEGATIVE_ENGINES = frozenset({
    "h3", "mmh3", "minimax_h3", "comfyui_mmh3", "comfyui_h3",
})


def supports_negative(engine: str) -> bool:
    """该引擎是否真的会把负向提示词喂进去。

    H3 返回 False：给它配负向不会报错，但也**不会起任何作用**，别浪费时间调。
    """
    return str(engine or "").strip().lower() not in _NO_NEGATIVE_ENGINES


def _dedup_join(*chunks: str) -> str:
    """按逗号切分后去重（保序），再拼回字符串。"""
    toks: list = []
    for chunk in chunks:
        for t in (chunk or "").split(","):
            t = t.strip()
            if t and t not in toks:
                toks.append(t)
    return ", ".join(toks)


def resolve_negative(config: dict | None = None, *, engine_negative: str = "",
                     extra: str = "") -> str:
    """决定最终使用的负向提示词，优先级：

    1. 引擎级显式配置（如 `engine.comfyui_ltx.negative`）—— 最具体，写了就用
    2. `config.prompting.negative` —— 可以是预设名（"quality"/"identity"/"anime"）
       或字面串（不是预设名时按字面处理）
    3. 默认预设 `DEFAULT_PRESET`

    extra 用于调用方临时追加（如按引擎追加"文字/水印"等），会与前面的去重合并。
    """
    cfg = config or {}
    prompting = cfg.get("prompting") or {}

    if engine_negative:
        base = engine_negative
    elif prompting.get("negative"):
        base = str(prompting["negative"]).strip()
        if base in NEGATIVE_PRESETS:      # 写的是预设名
            base = NEGATIVE_PRESETS[base]
    else:
        base = NEGATIVE_PRESETS[DEFAULT_PRESET]

    return _dedup_join(base, extra)
