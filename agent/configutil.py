"""按阶段解析可复用的接口档案。"""
from __future__ import annotations

import copy


def for_stage(config: dict, stage: str) -> dict:
    """返回应用了 stage_profiles 的独立配置副本。"""
    out = copy.deepcopy(config)
    profiles = config.get("profiles", {}) or {}
    selected = (config.get("stage_profiles", {}) or {}).get(stage, {}) or {}
    for section, profile_name in selected.items():
        profile = (profiles.get(section, {}) or {}).get(profile_name)
        if isinstance(profile, dict):
            if section == "comfyui":
                image_prompt = out.get("image_prompt", {}) or {}
                base = image_prompt.get("comfyui", {}) or {}
                image_prompt["comfyui"] = {**base, **copy.deepcopy(profile)}
                out["image_prompt"] = image_prompt
                continue
            base = out.get(section, {}) or {}
            out[section] = {**base, **copy.deepcopy(profile)}
    return out
