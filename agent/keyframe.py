"""D 阶段 · 关键帧出图（ComfyUI）。

把 image_prompt 生成的英文提示词交给 [ComfyUI](https://github.com/comfyanonymous/ComfyUI)
出关键帧图，随后作为 SkyReels I2V 的起始帧（见 engine.py 的 --image），
从而保证各镜角色一致。

对接方式：
- config.image_prompt.comfyui.api 指向 ComfyUI 服务（如 http://localhost:8188）；
- config.image_prompt.comfyui.workflow 指向一个导出好的 workflow JSON
  （其某个节点含 "text" 字段会被注入提示词）。
- 若 ComfyUI 未就绪 / 未配 workflow，则 generate() 返回空列表，
  上层退化为纯 T2V/DF（不影响管线）。

Skill 方法论见 skills/D_image_prompt.md。
"""
from __future__ import annotations

import json
import os
import time


class KeyframeGenerator:
    def __init__(self, config: dict, workdir: str):
        self.cfg = config.get("image_prompt", {}) or {}
        self.comfy = self.cfg.get("comfyui", {}) or {}
        self.api = (self.comfy.get("api") or "").rstrip("/")
        self.workflow_path = self.comfy.get("workflow", "")
        self.dir = os.path.join(workdir, "keyframes")
        os.makedirs(self.dir, exist_ok=True)
        self.enabled = bool(self.cfg.get("enabled", True)) and bool(self.api)

    def is_ready(self) -> bool:
        if not self.api:
            return False
        try:
            import requests
            return requests.get(self.api + "/", timeout=3).status_code == 200
        except Exception:
            return False

    def generate(self, prompts: list[str]) -> list[str]:
        """返回与 prompts 等长的关键帧图片路径列表；无法出图的位置为 None。"""
        if not self.enabled:
            return []
        if not self.is_ready():
            print("  [D] ComfyUI 未就绪，跳过关键帧出图（退化为纯 T2V/DF）。")
            return []
        return [self._one(p, i) for i, p in enumerate(prompts)]

    # ---------- 内部 ----------
    def _one(self, prompt: str, idx: int):
        wf = self._load_workflow(prompt)
        if wf is None:
            print("  [D] 未提供 ComfyUI workflow，无法出图（请配置 image_prompt.comfyui.workflow）。")
            return None
        try:
            import requests
            r = requests.post(self.api + "/prompt", json={"prompt": wf}, timeout=60)
            r.raise_for_status()
            pid = r.json().get("prompt_id")
            if not pid:
                return None
            for _ in range(180):
                h = requests.get(self.api + "/history/" + pid, timeout=10).json()
                if pid in h and h[pid].get("outputs"):
                    img = self._extract_image(h[pid])
                    if img:
                        return self._save(img, idx)
                time.sleep(2)
            print("  [D] ComfyUI 出图超时")
        except Exception as e:
            print(f"  [D] ComfyUI 出图失败: {e}")
        return None

    def _load_workflow(self, prompt: str):
        if not self.workflow_path or not os.path.exists(self.workflow_path):
            return None
        try:
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

    @staticmethod
    def _extract_image(output: dict):
        for node_out in output.get("outputs", {}).values():
            imgs = node_out.get("images") if isinstance(node_out, dict) else None
            if imgs:
                return imgs[0]
        return None

    def _save(self, img: dict, idx: int) -> str:
        import requests
        params = {"filename": img["filename"], "subfolder": img.get("subfolder", ""),
                  "type": img.get("type", "output")}
        r = requests.get(self.api + "/view", params=params, timeout=30)
        r.raise_for_status()
        path = os.path.join(self.dir, f"keyframe_{idx:03d}.png")
        with open(path, "wb") as f:
            f.write(r.content)
        return path
