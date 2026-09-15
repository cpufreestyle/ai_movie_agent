"""硬件档位 `amd395-128g`（AMD Ryzen AI Max+ 395 / Strix Halo 128GB 统一内存）：行为锁。

为什么值得单独一档、单独一组测试：

  - 该机的「显存」是**从 128GB 统一内存里切出来的**（BIOS 的 UMA Frame Buffer）。
    Windows WMI 的 `AdapterRAM` 是 32 位字段、iGPU 常被报成 512MB~4GB；Linux `lspci`
    路径更是直接给 0 → 原来的 `pick_tier()` 会把这台顶级机器判成 `cpu` 档。
  - 所以识别改用「AMD + 内存 ≥96GB + 型号线索」，并用「显存被低估」兜底；
    但**不能误伤真独显**（如 RX 7900 XTX 24GB + 128GB 内存，那本就该是 high 档）。
  - 档位名对外是「部署选择」，用户会写各种口语别名（395 / strix-halo / ai-max-395-128g），
    统一由 `normalize_tier()` 归一。

覆盖：识别 / 不误伤 / 别名归一 / 档位内容 / 覆盖生效 / 未知档位不改行为 / deploy 选档入口。
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config_env as ce  # noqa: E402

AMD395 = "amd395-128g"

# (ascii_id, 说明, hw dict, 期望档位)
HW_CASES = [
    ("strix_halo_wmi_low_vram",
     "Strix Halo 395 128G：WMI 报 8060S、显存被低估",
     {"vendor": "AMD", "gpu_name": "AMD Radeon(TM) 8060S Graphics",
      "vram_gb": 0.5, "ram_gb": 125.5}, AMD395),
    ("strix_halo_nameless_fallback",
     "Strix Halo 395 128G：型号无线索，靠『显存极小 + 大内存』兜底",
     {"vendor": "AMD", "gpu_name": "AMD Radeon Graphics",
      "vram_gb": 0.0, "ram_gb": 128.0}, AMD395),
    ("strix_halo_rocm_uma",
     "Strix Halo 395 128G：rocm-smi 报出 UMA 96GB，但型号有线索",
     {"vendor": "AMD", "gpu_name": "AMD Ryzen AI Max+ 395",
      "vram_gb": 96.0, "ram_gb": 128.0}, AMD395),
    ("amd_discrete_not_395",
     "AMD 真独显 24GB + 128G 内存 —— 不该被误判成 395",
     {"vendor": "AMD", "gpu_name": "Navi 31 [Radeon RX 7900 XTX]",
      "vram_gb": 24.0, "ram_gb": 128.0}, "high"),
    ("amd_small_igpu",
     "AMD 小 iGPU 机器（内存也不大）—— 仍应是 cpu 档",
     {"vendor": "AMD", "gpu_name": "Radeon 780M", "vram_gb": 0.5, "ram_gb": 32.0}, "cpu"),
    ("nvidia_mid_unchanged",
     "NVIDIA 16GB / 32G —— 保持原 mid 判定",
     {"vendor": "NVIDIA", "gpu_name": "RTX 5070 Ti", "vram_gb": 15.9, "ram_gb": 31.8}, "mid"),
    ("nvidia_high_unchanged",
     "NVIDIA 24GB / 64G —— 保持原 high 判定",
     {"vendor": "NVIDIA", "gpu_name": "RTX 4090", "vram_gb": 24.0, "ram_gb": 64.0}, "high"),
    ("no_gpu",
     "无显卡 —— cpu",
     {"vendor": None, "gpu_name": None, "vram_gb": 0.0, "ram_gb": 64.0}, "cpu"),
]


@pytest.mark.parametrize("hw,expected", [(c[2], c[3]) for c in HW_CASES],
                         ids=[c[0] for c in HW_CASES])
def test_pick_tier_covers_amd395_without_misfiring(hw, expected):
    assert ce.pick_tier(hw) == expected


@pytest.mark.parametrize("alias", [
    "amd395", "amd-395", "amd_395", "amd395-128g", "amd395128g", "amd-395-128g",
    "395", "395-128g", "ai-max-395", "ai-max-395-128g", "ryzen-ai-max-395",
    "strix-halo", "strixhalo", "halo",
    "AMD395", "  STRIX-HALO  ",      # 大小写 / 空白
])
def test_normalize_tier_accepts_aliases(alias):
    assert ce.normalize_tier(alias) == AMD395


@pytest.mark.parametrize("name,expected", [
    ("high", "high"), ("mid", "mid"), ("low", "low"), ("cpu", "cpu"),
    ("HIGH", "high"),
    ("", ""), ("   ", ""), ("bogus", ""), (None, ""), (123, ""),
])
def test_normalize_tier_passthrough_and_rejects(name, expected):
    assert ce.normalize_tier(name) == expected


def test_amd395_profile_content():
    """档位内容：bf16 钉死 + 不 offload + 两遍采样 + 高档采样（128G 有余量）。"""
    prof = ce.HW_TIER_PROFILES[AMD395]
    high = ce.HW_TIER_PROFILES["high"]
    assert prof["engine.comfyui_ltx.precision"] == "bf16"
    assert prof["engine.offload"] is False
    assert prof["engine.comfyui_mmH3.two_pass.enable"] is True
    assert prof["engine.comfyui_mmH3.block_cache.enable"] is True
    assert (prof["engine.comfyui_mmH3.num_frames"]
            >= high["engine.comfyui_mmH3.num_frames"])
    assert prof["blender.samples"] >= high["blender.samples"]
    assert prof["qa.max_rerolls"] >= high["qa.max_rerolls"]


def test_amd395_is_a_registered_tier():
    assert AMD395 in ce.HW_TIER_PROFILES
    assert ce.AMD395_TIER == AMD395


def test_apply_hw_overrides_via_env_alias(monkeypatch):
    """HW_TIER 用别名（395）也要能生效。"""
    monkeypatch.setenv("HW_TIER", "395")
    monkeypatch.delenv("GPU_BACKEND", raising=False)
    cfg = ce.apply_hw_overrides({})
    assert cfg["engine"]["comfyui_ltx"]["precision"] == "bf16"
    assert cfg["engine"]["offload"] is False
    assert cfg["engine"]["comfyui_mmH3"]["num_frames"] == 90


def test_apply_hw_overrides_via_config_key(monkeypatch):
    monkeypatch.delenv("HW_TIER", raising=False)
    cfg = ce.apply_hw_overrides({"hw_tier": "STRIX-HALO"})
    assert cfg["engine"]["comfyui_mmH3"]["resolution"] == "1024x576"
    assert cfg["engine"]["comfyui_ltx"]["precision"] == "bf16"


def test_unknown_tier_leaves_config_untouched(monkeypatch):
    """未知档位不能改任何东西（向后兼容：老配置不该被新代码动）。"""
    monkeypatch.delenv("HW_TIER", raising=False)
    monkeypatch.delenv("AUTO_HW", raising=False)
    cfg = ce.apply_hw_overrides({"engine": {"backend": "comfyui_mmH3"}, "hw_tier": "nope"})
    assert cfg == {"engine": {"backend": "comfyui_mmH3"}, "hw_tier": "nope"}


# ---------------------------------------------------------------------------
# deploy.py 的选档入口（--tier）
# ---------------------------------------------------------------------------
def _load_deploy():
    spec = importlib.util.spec_from_file_location(
        "_wb_deploy_under_test", os.path.join(ROOT, "deploy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def deploy():
    return _load_deploy()


class _Args:
    """够 resolve_scheme / resolve_tier_choice 用的最小 args（模拟 argparse 结果）。"""

    def __init__(self, tier=None, gpu="auto", engine=None, method="auto"):
        self.tier = tier
        self.gpu = gpu
        self.engine = engine
        self.method = method


def test_deploy_resolve_tier_arg_normalizes_alias(deploy):
    assert deploy.resolve_tier_arg("395") == AMD395
    assert deploy.resolve_tier_arg("amd395-128g") == AMD395
    assert deploy.resolve_tier_arg("") == ""


def test_deploy_resolve_tier_arg_exits_on_unknown(deploy):
    with pytest.raises(SystemExit) as ei:
        deploy.resolve_tier_arg("definitely-not-a-tier")
    assert ei.value.code == 2


def test_deploy_tier_choice_priority(deploy, monkeypatch):
    """优先级：--tier > config.hw_tier > 自动识别（且自动只认 395）。"""
    # 1) --tier 最优先
    assert deploy.resolve_tier_choice(_Args("395"), {"hw_tier": "high"}, "amd") == (
        AMD395, "手动 --tier")
    # 2) config.hw_tier 次之
    assert deploy.resolve_tier_choice(_Args(None), {"hw_tier": "strix-halo"}, "amd") == (
        AMD395, "config.hw_tier")
    # 3) 自动识别：仅当命中 395 才采用
    monkeypatch.setattr(deploy, "detect_hw_tier", lambda: AMD395)
    assert deploy.resolve_tier_choice(_Args(None), {}, "amd") == (
        AMD395, "自动识别 AMD 395 128G")
    # 4) 自动识别出 high/mid/low/cpu 时不写（避免改变既有部署行为）
    monkeypatch.setattr(deploy, "detect_hw_tier", lambda: "high")
    assert deploy.resolve_tier_choice(_Args(None), {}, "amd") == ("", "")
    # 5) 非 AMD 后端不自动套 395
    monkeypatch.setattr(deploy, "detect_hw_tier", lambda: AMD395)
    assert deploy.resolve_tier_choice(_Args(None), {}, "nvidia") == ("", "")


def test_deploy_writes_hw_tier_into_env(deploy, monkeypatch):
    """--tier 选中后必须出现在 .env 内容里，否则容器拿不到档位。"""
    monkeypatch.setattr(deploy, "detect_gpu", lambda: "amd")
    monkeypatch.setattr(deploy, "detect_docker", lambda: False)
    sch = deploy.resolve_scheme({}, _Args("395"))
    assert sch["tier"] == AMD395
    assert sch["env"]["HW_TIER"] == AMD395
    assert sch["tier_source"] == "手动 --tier"


def test_deploy_env_has_empty_hw_tier_when_unset(deploy, monkeypatch):
    """没选档位时写空串（容器内 falsy → 回落到 config 默认），不能写成 'None'。"""
    monkeypatch.setattr(deploy, "detect_gpu", lambda: "nvidia")
    monkeypatch.setattr(deploy, "detect_docker", lambda: False)
    monkeypatch.setattr(deploy, "detect_hw_tier", lambda: "mid")
    sch = deploy.resolve_scheme({}, _Args(None))
    assert sch["tier"] == ""
    assert sch["env"]["HW_TIER"] == ""
