"""ComfyUI 工作流节点 ID 分配器。

背景：H3 工作流的节点 ID 原先完全靠**注释互相避让**（1~14 / 20~28 / 30~32 /
40 / 41~43 / 50~57），代码里到处写着「避开已占用的节点 ID」。这种「注释级约定」
已经踩过一次坑：ref_images 用 ``"2%d" % i`` 生成 20~28，与后处理的 30/31/32
撞号会把 ``ref_image_i`` 指向后处理输出、形成**依赖环**（详见
agent/comfyui_post.py 开头）。

NodeAllocator 把约定升级为机器保证：

* **名字 → ID 登记**：调用方只说语义名（``alloc("fc_apply")``），不再出现裸数字，
  阅读 ``_build_workflow`` 时不必再心算「哪个号还空着」；
* **冲突断言**：同一名字分配两次、或同一 ID 分配给两个名字，当场 AssertionError；
* **区间预留**：``alloc_range("ref_image", i)`` 从预留区间顺序取号，越界即报错；
* **构建后审计**：``audit(nodes)`` 校验工作流里的节点全部来自本分配器，同时没有
  「分配了却没用上」的悬空号 —— 杜绝有人再偷偷塞一个硬编码 ID。

``PLAN`` 刻意与历史工作流**逐字节一致**（ID 变了就等于已出片的复现性发生变化），
因此改 PLAN 必须同步重跑 `tests/test_node_ids.py` 里的黄金样本。
"""
from __future__ import annotations


class NodeAllocator:
    """名字 → 节点 ID 的分配器（见模块 docstring）。"""

    #: 默认规划：语义名 -> 固定 ID（与历史工作流逐字节一致）。
    PLAN: dict[str, str] = {
        # 1~14 主链
        "unet": "1", "lora": "2", "clip": "3",
        "vae_video": "4", "vae_audio": "5",
        "cond": "6", "sampler": "7",
        "guider": "8", "noise": "9", "sampler_final": "10",
        "decode": "11", "combine": "12",
        "first_frame": "13", "ref_video": "14",
        # 30~32 后处理（名字由 agent/comfyui_post.py 传入）
        "upscale_loader": "30", "upscale_apply": "31", "sharpen": "32",
        # 40~43 缓存加速 / Fun Control
        "block_cache": "40",
        "fc_loader": "41", "fc_video": "42", "fc_apply": "43",
        # 50~57 二采（Latent 放大 → Reconcile → DetailMixer → 二采采样）
        "cond2": "50", "tp_upscale": "51", "tp_reconcile": "52",
        "tp_mixer": "53", "tp_guider": "54", "tp_noise": "55",
        "tp_sampler": "56", "tp_parity": "57",
    }

    #: 默认区间预留：组名 -> (起始 ID, 容量)。ref_images 最多 9 张 → 20..28。
    RANGES: dict[str, tuple[int, int]] = {"ref_image": (20, 9)}

    def __init__(self, plan: dict | None = None, ranges: dict | None = None,
                 auto_from: int = 15):
        self._plan = {str(k): str(v) for k, v in
                      (self.PLAN if plan is None else plan).items()}
        self._ranges = {str(k): (int(v[0]), int(v[1])) for k, v in
                        (self.RANGES if ranges is None else ranges).items()}
        self._owner: dict[str, str] = {}       # 节点 ID -> 语义名
        self._issued: set[str] = set()         # 已分配过的语义名
        self._auto_next = int(auto_from)
        # 规划内 / 预留区间内的 ID 一律视为「已占位」，自动分配不得取用
        self._reserved = set(self._plan.values())
        for start, count in self._ranges.values():
            self._reserved.update(str(start + i) for i in range(count))

    # ---------- 分配 ----------
    def alloc(self, name: str) -> str:
        """按语义名取 ID。名字未登记在 ``PLAN`` 时，自动分配一个空闲号。"""
        if name in self._issued:
            raise AssertionError(f"[workflow] 节点名重复分配: {name!r}")
        nid = self._plan.get(name)
        if nid is None:
            nid = self._auto()
        self._claim(nid, name)
        self._issued.add(name)
        return nid

    def alloc_range(self, group: str, index: int) -> str:
        """从预留区间顺序取号，如 ``alloc_range("ref_image", 0) -> "20"``。"""
        try:
            start, count = self._ranges[group]
        except KeyError:
            raise KeyError(f"[workflow] 未定义 ID 区间: {group!r}") from None
        if not 0 <= index < count:
            raise AssertionError(
                f"[workflow] 区间 {group} 越界：index={index}，容量={count}"
                f"（ID 会溢出到相邻分段，必然撞号）")
        name = f"{group}[{index}]"
        nid = str(start + index)
        self._claim(nid, name)
        self._issued.add(name)
        return nid

    # ---------- 自检 ----------
    def audit(self, nodes: dict) -> None:
        """构建完成后校验：工作流的节点必须全部来自本分配器，且无悬空分配。"""
        got = {str(k) for k in nodes}
        unknown = got - set(self._owner)
        if unknown:
            raise AssertionError(
                "[workflow] 存在未经分配器登记的节点 ID: "
                f"{sorted(unknown, key=int)} —— 禁止硬编码节点 ID")
        unused = set(self._owner) - got
        if unused:
            raise AssertionError(
                "[workflow] 已分配却未出现在工作流里的节点: "
                f"{sorted(unused, key=int)}（说明分配与写入不成对）")

    @property
    def layout(self) -> dict[str, str]:
        """当前「语义名 -> ID」分配表（调试 / 测试用）。"""
        return dict(self._owner)

    # ---------- 内部 ----------
    def _auto(self) -> str:
        while str(self._auto_next) in self._reserved:
            self._auto_next += 1
        nid = str(self._auto_next)
        self._auto_next += 1
        return nid

    def _claim(self, nid: str, name: str) -> None:
        nid = str(nid)
        prev = self._owner.get(nid)
        if prev is not None:
            raise AssertionError(
                f"[workflow] 节点 ID 冲突: {nid} 已被 {prev!r} 占用，不能给 {name!r}"
                "（历史上 ref_images 的 20~28 与后处理 30/31/32 撞号会形成依赖环）")
        self._owner[nid] = name
