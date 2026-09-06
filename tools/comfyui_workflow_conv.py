"""把 ComfyUI 的 UI(litegraph) 工作流转换为 /prompt 需要的 API 格式。

背景：ComfyUI 里"另存为"的工作流默认是 UI 格式（nodes/links/pos），
只能通过 /prompt 提交的是另一种 API 格式（{node_id: {class_type, inputs}}）。
官方 LTX-2.5 工作流大量使用 subgraph（蓝图子图），其节点 type 是 UUID，
/object_info 查不到，因此必须先递归把子图摊平、重连跨边界连线，
再按 /object_info 的输入定义映射 widgets_values 生成 API 格式。

实现要点：
- 子图内部 links 是对象，顶层 links 是数组 -> 统一成 {id,origin_id,origin_slot,
  target_id,target_slot}；origin_id==-10 表示子图输入槽，target_id==-20 表示输出槽。
- 连线解析采用**惰性 thunk**：子图之间互相引用且展开有先后顺序，立即求值会
  取到未展开的子图输出。全部展开完成后再统一 force 求值。

用法：
  python tools/comfyui_workflow_conv.py <ui_workflow.json> <out_api.json> \
      --api http://127.0.0.1:8188
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SKIP_TYPES = {"Note", "MarkdownNote", "NoteNode", "PrimitiveNode"}


def _out(text: str) -> None:
    """中文安全输出（PowerShell 下 GBK 会炸）。"""
    sys.stdout.buffer.write(text.encode("utf-8"))


def _force(v):
    """v 可能是 (node_id, slot) 元组，也可能是惰性 thunk，递归求值。"""
    while callable(v):
        v = v()
    return v


def normalize_link(l) -> dict:
    if isinstance(l, list):
        return {
            "id": l[0], "origin_id": l[1], "origin_slot": l[2],
            "target_id": l[3], "target_slot": l[4],
            "type": l[5] if len(l) > 5 else None,
        }
    return l


class Flattener:
    """递归展开 subgraph，把整个图摊平成普通节点 + 已解析的输入来源。"""

    def __init__(self, subgraphs: dict):
        self.subgraphs = subgraphs
        self.flat: dict[str, dict] = {}     # id -> {type, ins:{name:(src,slot)}, wv:[]}
        self.pending: list[tuple] = []      # (node_id, input_name, thunk)
        self.warnings: list[str] = []

    def expand(self, scope: dict, prefix: str, io_provider) -> dict:
        links = [normalize_link(l) for l in (scope.get("links") or [])]
        link_by_id = {l["id"]: l for l in links}
        nodes = scope.get("nodes") or []
        local_out: dict = {}                # (node_id, slot) -> tuple 或 thunk

        def resolve(lk):
            """返回 thunk；真正取值推迟到全部展开完成之后。"""
            l = link_by_id.get(lk)
            if not l:
                return lambda: None
            if l.get("origin_id") == -10:                 # 子图输入槽
                slot = l.get("origin_slot")
                return lambda: _force(io_provider(slot))
            key = (l.get("origin_id"), l.get("origin_slot"))
            return lambda: _force(local_out.get(key))

        for n in nodes:
            ntype = n.get("type")
            if ntype in SKIP_TYPES:
                continue
            nid = f"{prefix}{n.get('id')}"

            if ntype == "Reroute":
                # Reroute 只做转发：把它的输出槽直接指向上游来源，不生成实体节点
                in_link = None
                for inp in (n.get("inputs") or []):
                    if inp.get("link") is not None:
                        in_link = inp.get("link")
                        break
                local_out[(n.get("id"), 0)] = (
                    resolve(in_link) if in_link is not None else (lambda: None))
                continue

            if ntype in self.subgraphs:
                sub = self.subgraphs[ntype]
                sub_inputs = sub.get("inputs") or []
                parent_ins = {i.get("name"): i.get("link")
                              for i in (n.get("inputs") or [])}

                def sub_io(slot, _si=sub_inputs, _pi=parent_ins, _r=resolve):
                    if slot is None or slot >= len(_si):
                        return None
                    name = _si[slot].get("name")
                    lk = _pi.get(name)
                    return _r(lk) if lk is not None else None

                sub_out = self.expand(sub, f"{nid}_", sub_io)
                for slot, thunk in sub_out.items():
                    local_out[(n.get("id"), slot)] = thunk
            else:
                self.flat[nid] = {"type": ntype, "ins": {},
                                  "wv": list(n.get("widgets_values") or [])}
                for oi, _o in enumerate(n.get("outputs") or []):
                    local_out[(n.get("id"), oi)] = (nid, oi)
                for inp in (n.get("inputs") or []):
                    lk = inp.get("link")
                    if lk is None:
                        continue
                    self.pending.append((nid, inp.get("name"), resolve(lk)))

        scope_out: dict = {}
        for l in links:
            if l.get("target_id") == -20:                 # 子图输出槽
                scope_out[l.get("target_slot")] = resolve(l["id"])
        return scope_out

    def finish(self) -> None:
        """全部展开完成后统一求值连线。"""
        for nid, name, thunk in self.pending:
            src = _force(thunk)
            if src:
                self.flat[nid]["ins"][name] = src
            else:
                self.warnings.append(
                    f"{self.flat[nid]['type']}({nid}).{name}: 连线未解析")


def input_order(spec: dict):
    """按 required -> optional 的声明顺序返回 [(name, spec), ...]。"""
    out = []
    ins = spec.get("input", {}) or {}
    for grp in ("required", "optional"):
        for name, v in (ins.get(grp, {}) or {}).items():
            out.append((name, v))
    return out


def has_control_after(spec) -> bool:
    """seed 类输入在 widgets_values 里会额外跟一个 control_after_generate 项。"""
    if isinstance(spec, list) and len(spec) >= 2 and isinstance(spec[1], dict):
        return bool(spec[1].get("control_after_generate"))
    return False


def _cfg0(spec_v):
    return spec_v[0] if isinstance(spec_v, list) else spec_v


def _combo_opts(spec_v):
    if isinstance(spec_v, list) and len(spec_v) > 1 and isinstance(spec_v[1], dict):
        return spec_v[1].get("options")
    return None


def dynamic_subinputs(spec_v, chosen_key):
    """COMFY_DYNAMICCOMBO_V3 选中某 option 后暴露的动态子输入（required 部分）。

    例：ResizeImageMaskNode.resize_type 选 "scale longer dimension" 时需 longer_size。
    这些子输入不在 input_order 里，要单独从剩余 widgets_values 填充。
    """
    if not isinstance(spec_v, list) or len(spec_v) < 2:
        return None
    cfg = spec_v[1]
    if not isinstance(cfg, dict):
        return None
    for opt in (cfg.get("options") or []):
        if opt.get("key") == chosen_key:
            return (opt.get("inputs") or {}).get("required") or {}
    return None


def type_matches(spec_v, val) -> bool:
    """按 /object_info 的输入类型判断 saved widget 值能否落在该输入上。

    这是修复“示例工作流与已装节点版本漂移（widget 顺序/数量变化）导致按位置
    错位”的关键：匹配失败时该值会被留给其它同类型输入，本输入取默认值。
    """
    c0 = _cfg0(spec_v)
    if c0 == "COMBO":
        opts = _combo_opts(spec_v)
        if opts:
            return isinstance(val, str) and val in opts
        return isinstance(val, str)
    if c0 == "INT":
        return isinstance(val, int) and not isinstance(val, bool)
    if c0 == "FLOAT":
        return isinstance(val, (int, float)) and not isinstance(val, bool)
    if c0 == "BOOLEAN":
        return isinstance(val, bool)
    if c0 in ("STRING", "COMFY_DYNAMICCOMBO_V3", "COMFY_MATCHTYPE_V3"):
        return isinstance(val, str)
    if isinstance(c0, list):  # 选项列表
        return isinstance(val, str) and val in c0
    return True  # 未知类型宽松放行


def default_value(spec_v):
    if isinstance(spec_v, list) and len(spec_v) > 1 and isinstance(spec_v[1], dict) \
            and "default" in spec_v[1]:
        return spec_v[1]["default"]
    c0 = _cfg0(spec_v)
    if isinstance(c0, list):  # 纯选项列表格式，如 VAELoader.vae_name=["pixel_space"]
        return c0[0] if c0 else None
    if c0 == "COMBO":
        opts = _combo_opts(spec_v)
        if opts:
            return opts[0]
        return ""
    if c0 == "INT":
        return 0
    if c0 == "FLOAT":
        return 0.0
    if c0 == "BOOLEAN":
        return False
    if c0 in ("STRING", "COMFY_DYNAMICCOMBO_V3"):
        return ""
    return None


def build_api(flat: dict, obj_info: dict, warnings: list[str]) -> dict:
    api: dict = {}
    for nid, node in flat.items():
        ctype = node["type"]
        spec = obj_info.get(ctype)
        if spec is None:
            warnings.append(f"未知节点类型: {ctype}")
            continue
        wq = list(node["wv"])
        used = [False] * len(wq)
        inputs: dict = {}

        # 1) 连线输入（来自其它节点）
        for name, src in node["ins"].items():
            inputs[name] = [str(src[0]), src[1]]

        # 2) widget 输入：按 /object_info 声明顺序，对 saved 值做“类型贪婪匹配”，
        #    匹配不到（类型不符/缺失）则取默认值。这样即使示例工作流与已装节点
        #    版本漂移也能正确落位，而不是按位置把 1536 错塞进 scale_method。
        widget_specs = [(n, v) for n, v in input_order(spec) if n not in node["ins"]]
        for name, spec_v in widget_specs:
            chosen = None
            for i, v in enumerate(wq):
                if used[i]:
                    continue
                if type_matches(spec_v, v):
                    chosen = i
                    break
            if chosen is not None:
                inputs[name] = wq[chosen]
                used[chosen] = True
                if has_control_after(spec_v) and chosen + 1 < len(wq):
                    used[chosen + 1] = True  # 吃掉 control_after_generate 项
            else:
                inputs[name] = default_value(spec_v)

        # 2b) 动态组合输入（COMFY_DYNAMICCOMBO_V3）的子输入：
        #     如 ResizeImageMaskNode 选 "scale longer dimension" 时需要 longer_size。
        #     这些子输入不在 input_order 里，要从剩余 widgets_values 按类型补上。
        for name, spec_v in widget_specs:
            if _cfg0(spec_v) == "COMFY_DYNAMICCOMBO_V3":
                sub = dynamic_subinputs(spec_v, inputs.get(name))
                if not sub:
                    continue
                for sname, sdef in sub.items():
                    schosen = None
                    for i, v in enumerate(wq):
                        if used[i]:
                            continue
                        if type_matches(sdef, v):
                            schosen = i
                            break
                    if schosen is not None:
                        inputs[f"{name}.{sname}"] = wq[schosen]
                        used[schosen] = True
                        if has_control_after(sdef) and schosen + 1 < len(wq):
                            used[schosen + 1] = True
                    else:
                        inputs[f"{name}.{sname}"] = default_value(sdef)

        # 3) COMFY_AUTOGROW 动态子输入（ComfyMathExpression 的 values.a/b...）
        for name, src in node["ins"].items():
            if name not in inputs:
                inputs[name] = [str(src[0]), src[1]]

        required = (spec.get("input", {}) or {}).get("required", {}) or {}
        missing = [n for n in required
                   if n not in inputs and not any(k.startswith(n + ".") for k in inputs)]
        if missing:
            warnings.append(f"{ctype}({nid}): 缺少必填输入 {missing}")
        api[str(nid)] = {"class_type": ctype, "inputs": inputs}
    return api


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ComfyUI UI 工作流 -> API 格式（支持 subgraph 展开）")
    ap.add_argument("src", help="UI 格式工作流 JSON")
    ap.add_argument("dst", help="输出的 API 格式 JSON")
    ap.add_argument("--api", default="http://127.0.0.1:8188", help="ComfyUI 地址")
    args = ap.parse_args()

    with open(args.src, encoding="utf-8") as f:
        ui = json.load(f)

    try:
        import requests
        obj_info = requests.get(
            args.api.rstrip("/") + "/object_info", timeout=120).json()
    except Exception as e:
        _out(f"[conv] 无法连接 ComfyUI {args.api}: {e}\n")
        return 1

    subgraphs = {s["id"]: s for s in ((ui.get("definitions") or {}).get("subgraphs") or [])}
    fl = Flattener(subgraphs)
    fl.expand(ui, "", lambda slot: None)   # 顶层无输入槽
    fl.finish()
    api = build_api(fl.flat, obj_info, fl.warnings)

    os.makedirs(os.path.dirname(os.path.abspath(args.dst)), exist_ok=True)
    with open(args.dst, "w", encoding="utf-8") as f:
        json.dump(api, f, ensure_ascii=False, indent=2)

    _out(f"[conv] 子图定义: {len(subgraphs)} 个 -> 摊平后节点数: {len(api)}\n")
    types = sorted({v["class_type"] for v in api.values()})
    _out(f"[conv] 节点类型({len(types)}): {', '.join(types)}\n")
    _out(f"[conv] 已写入: {args.dst}\n")
    if fl.warnings:
        _out(f"[conv] 警告 {len(fl.warnings)} 条:\n")
        for w in fl.warnings[:40]:
            _out(f"  - {w}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
