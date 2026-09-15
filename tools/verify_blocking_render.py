"""白模 4 图渲染的真机验证工具（P2-⑨ 的验证手段，需 Blender MCP 在跑）。

CI 里只能测「生成的 bpy 代码结构」（tests/test_blocking_render_engine.py）；
产物到底对不对必须真机渲染，所以校验单独放这里，供改模板后手动跑一遍。

用法：
    # 渲染并逐 pass 计时（默认就是当前配置行为）
    python tools/verify_blocking_render.py render new --size 1280x720
    # 旧行为（depth/normal 继承 CYCLES）作基准
    python tools/verify_blocking_render.py render legacy --size 1280x720 --legacy
    # 试 line pass 换 EEVEE
    python tools/verify_blocking_render.py render eevee --line-engine BLENDER_EEVEE
    # 比对（跨引擎像素差是否只落在边缘 + 同一次内 4 张图是否互不相同）
    python tools/verify_blocking_render.py compare legacy new

产物落在 outputs/_blocking_verify/<tag>/，不进版本库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

NAMES = ("previs.png", "line.png", "depth.png", "normal.png")
LABELS = ("previs", "line", "depth", "normal")
OUTBASE = os.path.join(ROOT, "outputs", "_blocking_verify")
SPEC = {"characters": 2, "props": ["桌子"], "shot": "medium",
        "camera": "static", "height": "eye"}


def _instrument(code: str) -> str:
    """在每处 write_still 渲染后插入一条计时打印（按 previs→line→depth→normal 顺序）。"""
    code = "import time as _vt\n_vt0 = [_vt.time()]\n" + code
    state = {"i": 0}

    def _stamp(m):
        i = state["i"]
        state["i"] += 1
        label = LABELS[i] if i < len(LABELS) else f"pass{i}"
        return (m.group(0)
                + f"\nprint('VTIME',{label!r},round(_vt.time()-_vt0[0],3));"
                  f"_vt0[0]=_vt.time()")

    code = re.sub(r"bpy\.ops\.render\.render\(write_still=True, scene=scn\.name\)",
                  _stamp, code)
    assert state["i"] == len(LABELS), f"渲染次数异常：{state['i']}"
    return code


def _clean_dir(out: str) -> None:
    """清空输出目录里的旧产物（失败忽略）。"""
    for f in os.listdir(out):
        try:
            os.remove(os.path.join(out, f))
        except OSError:
            pass


def _parse_vtimes(stdout: str) -> dict:
    """从渲染 stdout 里抽取每张图的耗时（VTIME 行）。"""
    times = {}
    for ln in (stdout or "").splitlines():
        if ln.startswith("VTIME"):
            _, name, sec = ln.split()[:3]
            times[name] = float(sec)
            print(f"[verify]   {name:<7} {sec:>6}s")
    return times


def _check_artifacts(out: str) -> list:
    """校验 4 张产物存在且非空，返回缺失列表（打印大小与 sha256）。"""
    bad = []
    for nm in NAMES:
        p = os.path.join(out, nm)
        if not os.path.exists(p) or os.path.getsize(p) < 512:
            bad.append(nm)
            print(f"[verify]   {nm:<11} MISSING/EMPTY")
            continue
        with open(p, "rb") as f:
            b = f.read()
        print(f"[verify]   {nm:<11} {len(b):>8}B  "
              f"sha256={hashlib.sha256(b).hexdigest()[:16]}")
    return bad


def cmd_render(a) -> int:
    from agent.blocking import BlockingGenerator
    from tools.blender_mcp import BlenderMCP

    out = os.path.join(OUTBASE, a.tag)
    os.makedirs(out, exist_ok=True)
    _clean_dir(out)

    w, h = (int(v) for v in a.size.lower().split("x"))
    cfg = {"blender": {"enabled": True, "engine": a.engine, "width": w, "height": h,
                       "samples": a.samples, "reuse_scene": False,
                       "fast_control_passes": not a.legacy}}
    if a.line_engine:
        # 取值与 config.blender.line_engine 一致（cycles / eevee）
        cfg["blender"]["line_engine"] = a.line_engine
    bg = BlockingGenerator(cfg, out)

    code = _instrument(bg._build_block_code(SPEC, out, "block", a.engine))
    mcp = BlenderMCP(timeout=a.timeout)
    if not mcp.is_ready():
        print(f"[verify] Blender MCP 未就绪（{bg.host}:{bg.port}）——"
              f"请在 Blender 内启动 MCP Server", file=sys.stderr)
        return 2
    res = mcp.exec_code_ex(code)
    print(f"[verify] tag={a.tag} size={w}x{h} samples={a.samples} "
          f"fast_control_passes={not a.legacy} line_engine={bg.line_engine}")
    print(f"[verify] blender ok={res['ok']}")
    if not res["ok"]:
        print("[verify] error =", (res["error"] or "")[:2000], file=sys.stderr)
        return 1
    times = _parse_vtimes(res["stdout"])
    if times:
        print(f"[verify]   {'TOTAL':<7} {sum(times.values()):>6.2f}s")
    print("[verify] --- 产物 ---")
    bad = _check_artifacts(out)
    if bad:
        print(f"[verify] 失败：这些产物缺失或过小 {bad}", file=sys.stderr)
        return 1
    print(json.dumps({"tag": a.tag, "seconds": times,
                      "total_sec": round(sum(times.values()), 3),
                      "line_engine": bg.line_engine}, ensure_ascii=False))
    return 0


def _load(tag: str, name: str):
    import cv2
    p = os.path.join(OUTBASE, tag, name)
    return cv2.imread(p, cv2.IMREAD_UNCHANGED) if os.path.exists(p) else None


def _diff(a, b) -> dict:
    import numpy as np
    if a is None or b is None:
        return {"error": "missing"}
    if a.shape != b.shape:
        return {"error": "shape_mismatch", "shape": [list(a.shape), list(b.shape)]}
    d = np.abs(a[:, :, :3].astype(np.int32) - b[:, :, :3].astype(np.int32))
    return {"mean_abs": round(float(d.mean()), 4), "max_abs": int(d.max()),
            "pct_pixels_gt2": round(float((d.max(axis=2) > 2).mean() * 100), 4)}


def _edge_only(a, b) -> dict:
    """差异是否只落在几何边缘（抗锯齿级）。"""
    import cv2
    import numpy as np
    if a is None or b is None:
        return {"error": "missing"}
    d = np.abs(a[:, :, :3].astype(np.int32) - b[:, :, :3].astype(np.int32)).max(axis=2)
    mask = d > 2
    n = int(mask.sum())
    if not n:
        return {"differing_pixels": 0}
    g = cv2.cvtColor(b[:, :, :3].astype(np.uint8), cv2.COLOR_BGR2GRAY)
    grad = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3),
                    cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
    thr = float(np.percentile(grad, 90))
    return {"differing_pixels": n,
            "diff_pct": round(n / mask.size * 100, 4),
            "on_edge_pct": round(float((grad[mask] > thr).mean()) * 100, 2),
            "interior_pixels": int((grad[mask] <= thr).sum())}


def cmd_compare(a) -> int:
    import itertools
    rep: dict = {"cross": {}, "within": {}}
    for nm in NAMES:
        i0, i1 = _load(a.tag_a, nm), _load(a.tag_b, nm)
        rep["cross"][nm] = {**_diff(i0, i1), "edge_analysis": _edge_only(i0, i1)}
    for tag in (a.tag_a, a.tag_b):
        imgs = {n: _load(tag, n) for n in NAMES}
        rep["within"][tag] = {f"{x} vs {y}": _diff(imgs[x], imgs[y])
                              for x, y in itertools.combinations(NAMES, 2)}
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="白模 4 图渲染真机验证")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="渲染并逐 pass 计时")
    r.add_argument("tag")
    r.add_argument("--size", default="1280x720")
    r.add_argument("--samples", type=int, default=16)
    r.add_argument("--engine", default="eevee")
    r.add_argument("--timeout", type=float, default=900)
    r.add_argument("--legacy", action="store_true",
                   help="旧行为：depth/normal 继承 CYCLES")
    r.add_argument("--line-engine", choices=("cycles", "eevee"), default="",
                   help="line pass 引擎（同上配置键；eevee 实测快 12x，线条略有差异）")
    r.set_defaults(func=cmd_render)

    c = sub.add_parser("compare", help="比对两个 tag 的产物")
    c.add_argument("tag_a")
    c.add_argument("tag_b")
    c.set_defaults(func=cmd_compare)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
