"""定价模块：模型名归一化、双口径计价、config 整表覆盖。"""
import pytest

from loadn_webui import config as wd_config
from loadn_webui import pricing


def test_normalize_model():
    assert pricing.normalize_model("glm-5.3[1m]") == "glm-5.3"
    assert pricing.normalize_model("GLM-5.3") == "glm-5.3"
    assert pricing.normalize_model("glm-5.3-flash") == "glm-5.3-flash"
    assert pricing.normalize_model("glm-5.3.5-preview") == "glm-5.3"   # 前缀匹配
    assert pricing.normalize_model("totally-unknown") == pricing.DEFAULT_MODEL
    assert pricing.normalize_model("") == pricing.DEFAULT_MODEL


def test_cost_api_usd_numbers():
    # 全库真实量级：42.82M in + 1167.4M cache_read + 4.22M out ≈ $382
    cost = pricing.cost_api_usd("glm-5.3[1m]", input_t=42.82e6,
                                cache_read_t=1167.4e6, output_t=4.22e6)
    assert cost == pytest.approx(382.0, abs=1.0)
    # 1M input + 1M output 精确值
    assert pricing.cost_api_usd("glm-5.3", input_t=1e6, output_t=1e6) == pytest.approx(1.4 + 4.4)
    # cache_write 无溢价，按 input 价
    assert pricing.cost_api_usd("glm-5.3", cache_write_t=1e6) == pytest.approx(1.4)
    # Flash 价
    assert pricing.cost_api_usd("glm-5.3-flash", input_t=1e6) == pytest.approx(0.15)


def test_plan_credits():
    c = pricing.plan_credits("glm-5.3[1m]", input_t=1e6, cache_t=2e6, output_t=1e6)
    assert c == pytest.approx(6.9 + 2 * 1.7 + 24)
    # plan 表缺的模型兜底 DEFAULT（这里造一个 api 有、plan 无的场景）
    assert pricing.plan_credits("unknown-model", input_t=1e6) == pytest.approx(6.9)


def test_pricing_overview_shape():
    ov = pricing.pricing_overview()
    assert ov["api"]["glm-5.3"]["input"] == 1.4
    assert ov["plan"]["glm-5.3"]["output"] == 24
    assert ov["usd_cny"] == pricing.USD_CNY
    assert ov["cli_fallback"]["input"] == 5.0
    assert "note" in ov


def test_config_override_replaces_whole_table(monkeypatch, tmp_path):
    # config.yaml → load_config 整表替换
    yaml_text = ("pricing:\n"
                 "  api:\n"
                 "    my-model: {input: 2.0, cache_read: 0.2, output: 6.0}\n"
                 "  usd_cny: 7.0\n")
    (tmp_path / "config.yaml").write_text(yaml_text)
    monkeypatch.setattr(wd_config, "ROOT", tmp_path)
    cfg = wd_config.load_config()
    monkeypatch.setattr(wd_config, "CONFIG", cfg)
    # 整表替换：内置 glm-5.3 不在了，unknown/my-model 都落到唯一键 my-model
    assert pricing.cost_api_usd("my-model", input_t=1e6) == pytest.approx(2.0)
    assert pricing.cost_api_usd("glm-5.3", input_t=1e6) == pytest.approx(2.0)
    # plan 未覆盖 → 内置表继续生效
    assert pricing.plan_credits("glm-5.3", input_t=1e6) == pytest.approx(6.9)
    assert pricing.pricing_overview()["usd_cny"] == 7.0
