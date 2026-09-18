"""投稿元数据自动生成：标题候选 / 简介 / 标签 / 动态（B 站投稿 + 双语）。

参照开源社区做法：MoneyPrinterTurbo 的「一句主题 -> 自动标题 / 简介 / 标签」、
ShortGPT 的 auto title / description / hashtags。做法一致 —— 把一集的旁白剧本
（`outputs/series_script.json`）喂给本地 LLM，产出可直接投稿的元数据。

**无 LLM 也能用**：`rule_metadata()` 用规则法兜底，确定性强、可单测；有 LLM 时
优先用模型生成，任一环节失败自动回退规则法，绝不阻断。

产出一律经 `enforce_limits()` 收敛到 B 站约束（标题 ≤80、标签 ≤10 且单个 ≤20、
动态 ≤233）内。本模块对既有流程**零侵入**：`agent/preflight.py` 可对产出做静态校验。
"""
from __future__ import annotations

import json
import os
import re

# B 站投稿约束（与 agent/preflight.BILI 对齐；此处独立一份，避免模块间循环依赖）
TITLE_MAX = 80
TAG_MAX = 10
TAG_LEN_MAX = 20
DYNAMIC_MAX = 233

#: 恒定出现的基础标签（保证搜索基本盘）
BASE_TAGS = ("AI短片", "AIGC", "AI电影", "动画短片", "科幻")
#: 题材关键词库：出现在本集旁白里就补为标签（规则法专用；有 LLM 时由模型给标签）
THEME_TAGS = ("记忆", "芯片", "霓虹", "雨", "雨夜", "草地", "阳光", "椅子", "门",
              "城市", "童年", "未来", "光", "店铺", "实验")
#: 断句标点（取「首句钩子」用）
_PUNCT = "，。！？；：、—…,.!?;:—"


def cn_num(n: int) -> str:
    """阿拉伯数字 -> 中文数字（1..99），用于「第 N 集」。"""
    if n <= 0:
        return str(n)
    d = "零一二三四五六七八九"
    if n < 10:
        return d[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + d[n - 10]
    if n < 100:
        tens, ones = divmod(n, 10)
        return d[tens] + "十" + (d[ones] if ones else "")
    return str(n)


def _first_clause(line: str, limit: int = 22) -> str:
    """取一句话的首个分句（到第一个标点），并限长 —— 用作钩子/看点点。"""
    line = (line or "").strip()
    for i, ch in enumerate(line):
        if ch in _PUNCT and i > 0:
            line = line[:i]
            break
    return line[:limit].strip()


def _theme_tags(lines, k: int = 4) -> list[str]:
    """旁白里命中的题材关键词（规则法标签来源；确定性强，不产垃圾标签）。"""
    text = "".join(lines or [])
    return [t for t in THEME_TAGS if t in text][:k]


def rule_metadata(series: str, ep: int, ep_title: str, lines, lines_en=None) -> dict:
    """规则法（确定性）：无 LLM 或 LLM 失败时的兜底。返回未收敛的原始元数据。"""
    cn = cn_num(ep)
    tail = _first_clause(lines[-1]) if lines else ep_title
    head = _first_clause(lines[0]) if lines else ep_title
    hook2 = _first_clause(lines[len(lines) // 2]) if lines else ep_title

    kw = _theme_tags(lines, k=4)
    tags = list(BASE_TAGS) + [ep_title] + kw

    titles = [
        f"{series}第{cn}集 · {ep_title}",
        f"{tail}｜{series}第{cn}集",
        f"{ep_title}｜{series}第{cn}集 AI 动画短片",
        f"{series}第{cn}集「{ep_title}」{hook2}",
    ]

    points = [_first_clause(x, 30) for x in lines[:3]]
    desc = "\n".join([
        tail,
        "",
        f"{series}第{cn}集《{ep_title}》· 本片由 AI 全流程生成（剧本 → 分镜 → 出片 → 配音字幕）。",
        "",
        "看点：",
        *[f"· {p}" for p in points],
        "",
        " ".join("#" + t for t in tags[:6]),
    ])
    dynamic = f"{ep_title}｜{head} " + " ".join("#" + t for t in tags[:4])

    return {"series": series, "ep": ep, "ep_title": ep_title,
            "titles": titles, "desc": desc, "tags": tags, "dynamic": dynamic,
            "source": "rule"}


def enforce_limits(meta: dict) -> dict:
    """把元数据收敛到 B 站约束内：标题串去重限长、标签去重限量、动态截断。"""
    titles = []
    for t in meta.get("titles") or []:
        t = (t or "").strip()[:TITLE_MAX]
        if t and t not in titles:
            titles.append(t)
    meta["titles"] = titles[:5]
    meta["title"] = titles[0] if titles else ""

    tags, seen = [], set()
    for t in meta.get("tags") or []:
        t = (t or "").strip().lstrip("#").strip()
        if not t or len(t) > TAG_LEN_MAX or t in seen:
            continue
        seen.add(t)
        tags.append(t)
    meta["tags"] = tags[:TAG_MAX]

    meta["dynamic"] = (meta.get("dynamic") or "").strip()[:DYNAMIC_MAX]
    meta["desc"] = (meta.get("desc") or "").strip()
    return meta


def _llm_metadata(client, model, series, ep, ep_title, lines, lines_en=None) -> dict | None:
    """让本地 LLM 产出元数据 JSON；任何异常/结构不符返回 None（交由规则法兜底）。"""
    from . import llmutil
    cn = cn_num(ep)
    sys_prompt = ("你是 B 站投稿运营，为一部 AI 连续短片生成投稿元数据。"
                  "只输出一个 JSON 对象，不要解释、不要 markdown 代码块。")
    user = "\n".join([
        f"系列：{series}",
        f"集数：第{cn}集（ep{ep}）",
        f"本集标题：{ep_title}",
        "本集旁白（中文）：",
        "\n".join(f"- {x}" for x in (lines or [])),
        "",
        ("本集旁白（英文）：\n" + "\n".join(f"- {x}" for x in (lines_en or [])))
        if lines_en else "",
        "",
        "请输出 JSON：{",
        '  "titles": [3-5 个标题，每个含「第' + cn + '集」，<=30 字，有钩子感],',
        '  "desc": "简介：一句钩子 + 3 条看点 + 话题标签",',
        '  "tags": [6-8 个标签，单个 <=10 字],',
        '  "dynamic": "不超过 200 字的动态文案"',
        "}",
    ])
    try:
        raw = llmutil.chat(client, sys_prompt, user, max_tokens=700,
                           temperature=0.8, model=model)
        if not raw:
            return None
        data = json.loads(llmutil.extract_json(raw))
    except Exception:
        return None
    if not isinstance(data, dict) or not data.get("titles"):
        return None
    return {"series": series, "ep": ep, "ep_title": ep_title,
            "titles": data.get("titles") or [],
            "desc": data.get("desc") or "",
            "tags": data.get("tags") or [],
            "dynamic": data.get("dynamic") or "",
            "source": "llm"}


def generate(ep: int, *, script_path: str, config: dict | None = None,
             use_llm: bool = True, out: str | None = None) -> dict:
    """生成一集的投稿元数据。

    优先本地 LLM（config 里 llm 未禁用时），失败或 --no-llm 时回退规则法。
    产出经 enforce_limits 收敛；给定 out 则写 JSON（父目录自动创建）。
    """
    data = json.load(open(script_path, encoding="utf-8"))
    key = f"ep{ep}"
    if key not in data:
        raise SystemExit(f"[metadata] 剧本里没有 {key}：{script_path}")
    epdata = data[key]
    series = data.get("series") or ""

    meta = None
    if use_llm:
        try:
            from . import llmutil
            client = llmutil.make_client(config or {})
            if client is not None:
                model = (config or {}).get("llm", {}).get("model", "qwen2.5:14b")
                meta = _llm_metadata(client, model, series, ep,
                                     epdata.get("title", ""),
                                     epdata.get("narration"),
                                     epdata.get("narration_en"))
        except Exception:
            meta = None
    if meta is None:
        meta = rule_metadata(series, ep, epdata.get("title", ""),
                             epdata.get("narration") or [],
                             epdata.get("narration_en"))

    enforce_limits(meta)
    meta["en_title"] = _en_title(series, ep, epdata.get("title", ""), epdata.get("narration_en"))
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def _en_title(series: str, ep: int, ep_title: str, lines_en) -> str:
    """英文标题（复用已有英文旁白），投稿到 YouTube Shorts 等平台时用。"""
    hook = _first_clause((lines_en or [""])[0], 48) if lines_en else ep_title
    return f"Before I Saw the Future · EP{ep} · {hook}".strip()


def validate(meta: dict) -> dict:
    """对生成的元数据做静态校验（可直接喂给前端/CLI）。返回 {ok, errors, warnings}。"""
    errors, warnings = [], []
    if not (meta.get("title") or "").strip():
        errors.append("标题为空")
    elif len(meta["title"]) > TITLE_MAX:
        errors.append(f"标题超长({len(meta['title'])}>{TITLE_MAX})")
    tags = meta.get("tags") or []
    if len(tags) > TAG_MAX:
        errors.append(f"标签数 {len(tags)} 超过上限 {TAG_MAX}")
    if not tags:
        warnings.append("没有标签，检索面会变窄")
    if len(meta.get("dynamic") or "") > DYNAMIC_MAX:
        warnings.append(f"动态超 {DYNAMIC_MAX} 字（投稿时会被截断）")
    if not re.search(r"第[0-9一二三四五六七八九十百]+集", meta.get("title") or ""):
        warnings.append("标题缺少集数标记(第N集)")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


__all__ = ["BASE_TAGS", "DYNAMIC_MAX", "TAG_LEN_MAX", "TAG_MAX", "TITLE_MAX",
           "cn_num", "enforce_limits", "generate", "rule_metadata", "validate"]
