"""config.yaml 裸键（null）不得打穿嵌套 dataclass 默认实例。

实测坑：loadn-web init 生成的模板 `engines.loadn:` 子项全注释——YAML
解析为显式 null，_apply_section 直接 setattr 把 default_factory 实例
覆盖为 None，新装实例 /api/health 500（resolve_bin 取 CONFIG.engines.
loadn.bin → AttributeError）。语义：裸键=未配置，保留默认实例。
"""
from __future__ import annotations

from loadn_webui.config import EnginesConfig, PerEngineConfig, _apply_section


def test_bare_key_keeps_default_instance():
    cfg = EnginesConfig()
    _apply_section(cfg, {"default": "loadn", "loadn": None})
    assert cfg.default == "loadn"
    # 裸键（无子键）= 未配置：保留默认实例，不得变 None
    assert isinstance(cfg.loadn, PerEngineConfig)
    assert cfg.loadn.bin is None and cfg.loadn.enabled is True


def test_nested_dict_still_overrides():
    cfg = EnginesConfig()
    _apply_section(cfg, {"loadn": {"model": "glm-4.6", "base_url": "https://x"}})
    assert cfg.loadn.model == "glm-4.6"
    assert cfg.loadn.base_url == "https://x"
    assert cfg.loadn.api_key is None       # 未给的键保持默认


def test_scalar_none_unchanged():
    # 标量字段裸键 → None 是合法值（model: str|None），行为不变
    cfg = EnginesConfig()
    _apply_section(cfg, {"opencode_provider": None})
    assert cfg.opencode_provider is None


def test_engines_loadn_resolvable_after_bare_key():
    # 对赌：模板形态构造后 resolve_bin 可走（修复前 AttributeError）
    from loadn_webui.engines.loadn import LoadnSpec
    cfg = EnginesConfig()
    _apply_section(cfg, {"default": "loadn", "loadn": None})
    spec = LoadnSpec()
    bin_ = spec.resolve_bin()
    assert isinstance(bin_, (str, list))   # 不抛即过（兜底 python -m loadn）
