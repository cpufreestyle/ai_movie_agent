"""D 阶段 · 关键帧出图（ComfyUI）。

把 image_prompt 生成的英文提示词交给 [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
出关键帧图，随后作为视频引擎 I2V 的起始帧（见 ltx_engine.py / engine.py 的 image），
从而保证各镜角色一致。

对接方式：
- config.image_prompt.comfyui.api 指向 ComfyUI 服务（如 http://localhost:8188）；
- config.image_prompt.comfyui.workflow 指向一个导出好的 LTX/SDXL workflow JSON
  （其某个文本节点会被注入提示词）。
- 若 ComfyUI 未就绪 / 未配 workflow，则 generate() 返回空列表，
  上层退化为纯 T2V（不影响管线）。

底层 HTTP 调用统一走 tools/comfyui_client.ComfyUIClient（与 LTX 视频引擎共用）。
"""
from __future__ import annotations

import os
import shutil

from tools.comfyui_client import ComfyUIClient
from .llmutil import log


class KeyframeGenerator:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("image_prompt", {}) or {}
        self.comfy = self.cfg.get("comfyui", {}) or {}
        self.api = (self.comfy.get("api") or "").rstrip("/")
        self.workflow_path = self.comfy.get("workflow", "")
        self.dir = os.path.join(workdir, "keyframes")
        os.makedirs(self.dir, exist_ok=True)
        self.enabled = bool(self.cfg.get("enabled", True)) and bool(self.api)
        self.client = ComfyUIClient(self.api) if self.api else None

    def is_ready(self) -> bool:
        return bool(self.client) and self.client.is_ready()

    def generate(self, prompts: list[str]) -> list[str]:
        """返回与 prompts 等长的关键帧图片路径列表；无法出图的位置为 None。"""
        if not self.enabled:
            return []
        if not self.is_ready():
            log("  [D] ComfyUI 未就绪，跳过关键帧出图（退化为纯 T2V）。")
            return []
        return [self._one(p, i) for i, p in enumerate(prompts)]

    # ---------- 内部 ----------
    def _one(self, prompt: str, idx: int):
        wf = self._load_workflow(prompt)
        if wf is None:
            log("  [D] 未提供 ComfyUI workflow，无法出图（请配置 image_prompt.comfyui.workflow）。")
            return None
        try:
            pid = self.client.queue_prompt(wf)
            if not pid:
                return None
            item = self.client.wait(pid, timeout=600)
            if not item:
                log("  [D] ComfyUI 出图超时")
                return None
            paths = self.client.download_outputs(item, self.dir)
            imgs = [p for p in paths if p.lower().endswith((".png", ".jpg", ".jpeg"))]
            if not imgs:
                log("  [D] ComfyUI 未产出图片")
                return None
            src = max(imgs, key=os.path.getmtime)
            dest = os.path.join(self.dir, f"keyframe_{idx:03d}.png")
            shutil.move(src, dest)
            return dest
        except Exception as e:
            log(f"  [D] ComfyUI 出图失败: {e}")
        return None

    def _load_workflow(self, prompt: str):
        if not self.workflow_path or not os.path.exists(self.workflow_path):
            return None
        try:
            import json
            with open(self.workflow_path, encoding="utf-8") as f:
                wf = json.load(f)
        except Exception:
            return None
        # best-effort：把 prompt 注入第一个含 "text" 字段的节点（CLIP 文本编码节点常见）
        for node in wf.values():
            if isinstance(node, dict) and "text" in node:
                node["text"] = prompt
                return wf
        return wf
