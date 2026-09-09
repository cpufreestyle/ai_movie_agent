"""部署期用环境变量覆盖 config.yaml（Docker / 远程 ComfyUI 场景，免改 yaml）。

支持：
  OLLAMA_URL     LLM 服务地址，如 http://ollama:11434 或 http://192.168.1.10:11434
                 -> 自动改写 config.llm.base_url 与所有 llm 档案的 base_url（补 /v1）
  LLM_MODEL      LLM 模型名，如 qwen2.5:3b
  LLM_API_KEY    LLM api_key（Ollama 默认 ollama）
  COMFYUI_API    视频生成服务地址，如 http://comfyui:8188 或 http://192.168.1.20:8188
                 -> 改写 engine.comfyui_ltx.api / engine.comfyui_mmH3.api /
                    image_prompt.comfyui.api 及所有 comfyui 档案
  ENGINE_BACKEND 默认视频引擎：comfyui_mmH3（MiniMax H3）或 comfyui_ltx（LTX-2.5）
  GPU_BACKEND    显卡后端：nvidia（默认）或 amd。amd 时 NVFP4 不支持，
                 自动把 LTX 精度降为 bf16（视频权重需改用 bf16/GGUF/INT8 变体，见下载脚本）
"""
import os


def apply_env_overrides(cfg: dict) -> dict:
    cfg = cfg or {}

    ollama = (os.environ.get("OLLAMA_URL") or "").rstrip("/")
    if ollama:
        v1 = ollama + "/v1"
        cfg.setdefault("llm", {})["base_url"] = v1
        for p in ((cfg.get("profiles", {}) or {}).get("llm", {}) or {}).values():
            if isinstance(p, dict):
                p["base_url"] = v1

    if os.environ.get("LLM_MODEL"):
        cfg.setdefault("llm", {})["model"] = os.environ["LLM_MODEL"]
    if os.environ.get("LLM_API_KEY"):
        cfg.setdefault("llm", {})["api_key"] = os.environ["LLM_API_KEY"]

    comfy = os.environ.get("COMFYUI_API")
    if comfy:
        cfg.setdefault("engine", {}).setdefault("comfyui_ltx", {})["api"] = comfy
        cfg.setdefault("engine", {}).setdefault("comfyui_mmH3", {})["api"] = comfy
        cfg.setdefault("image_prompt", {}).setdefault("comfyui", {})["api"] = comfy
        for p in ((cfg.get("profiles", {}) or {}).get("comfyui", {}) or {}).values():
            if isinstance(p, dict):
                p["api"] = comfy

    if os.environ.get("ENGINE_BACKEND"):
        cfg.setdefault("engine", {})["backend"] = os.environ["ENGINE_BACKEND"]

    # 显卡后端：amd 时 NVFP4 不支持，把 LTX 精度降为 bf16（bf16 权重跑 ROCm 更稳）
    if os.environ.get("GPU_BACKEND") == "amd":
        cfg.setdefault("engine", {}).setdefault("comfyui_ltx", {})["precision"] = "bf16"
    return cfg
