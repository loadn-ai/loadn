"""P1-5 模型目录数据化验收。

- 目录内模型：窗口/TTL/输出上限取目录值；[Nm] 变体覆盖基模型窗口
- 目录外模型：保守 128k fallback + 每进程一次 warning（不再静默 200k）
- build._window_of 走目录（端到端窗口来源单一）
- compactor 触发分界：128k/200k/1M 三档各自按 92% 阈值（阈值随窗口缩放）
- check_models：合法目录 0；重复键/坏 schema/量级错误 → 1
"""
from __future__ import annotations

import json

import pytest

from loadn.core import models as models_mod
from loadn.core.build import _window_of
from loadn.core.compactor import COMPACT_THRESHOLD, Compactor
from loadn.core.models import FALLBACK_WINDOW


@pytest.fixture(autouse=True)
def _fresh_cache():
    models_mod._catalog = None
    models_mod._warned.clear()
    yield
    models_mod._catalog = None
    models_mod._warned.clear()


# ---------------------------------------------------------------- lookup
def test_catalog_models_exact_values():
    s = models_mod.lookup("glm-5.3")
    assert s.context_window == 200_000
    assert s.prompt_cache_ttl_s is None               # 未知就是未知
    assert models_mod.cache_ttl("glm-5.3") == 300     # TTL 默认 5min
    s2 = models_mod.lookup("claude-sonnet-4-6")
    assert s2.context_window == 200_000 and s2.max_output == 64_000
    assert s2.prompt_cache_ttl_s == 300


def test_variant_overrides_window():
    assert models_mod.lookup("glm-5.3[1m]").context_window == 1_000_000
    assert models_mod.lookup("mystery[2m]").context_window == 2_000_000
    s = models_mod.lookup("claude-opus-4-8[1m]")
    assert s.context_window == 1_000_000 and s.variant_window == 1_000_000
    # 无变体 → variant_window=None（区分「目录值」与「覆盖值」）
    assert models_mod.lookup("claude-opus-4-8").variant_window is None


def test_unknown_model_fallback_with_warning(caplog):
    with caplog.at_level("WARNING", logger="loadn.core.models"):
        assert models_mod.window_of("totally-unknown-9") == FALLBACK_WINDOW
        assert models_mod.window_of("totally-unknown-9") == FALLBACK_WINDOW
    warns = [r for r in caplog.records if "不在目录" in r.message]
    assert len(warns) == 1                            # 每进程只告警一次
    assert FALLBACK_WINDOW == 128_000                 # 保守档（非旧版 200k）


def test_build_window_of_delegates():
    assert _window_of("glm-5.3") == 200_000
    assert _window_of("glm-5.3[1m]") == 1_000_000
    assert _window_of("nope") == 128_000


def test_broken_catalog_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(models_mod, "_CATALOG_PATH", tmp_path / "bad.json")
    (tmp_path / "bad.json").write_text("{broken")
    assert models_mod.window_of("glm-5.3") == 128_000  # 坏目录全 fallback


# ---------------------------------------------------------------- compactor 分界
@pytest.mark.parametrize("window", [128_000, 200_000, 1_000_000])
async def test_compactor_threshold_scales_with_window(window):
    """92% 阈值随窗口缩放：三档各在阈下不压、阈上压（catalog→loop 链路）。"""
    c = Compactor.__new__(Compactor)          # 只测 maybe_compact 判定，不跑真压缩
    called = []

    async def fake_compact(msgs, context_window):
        called.append(context_window)
        return msgs, True
    c.compact = fake_compact
    msgs = []
    below = int(COMPACT_THRESHOLD * window) - 1
    used = {"input_tokens": below, "cache_read_input_tokens": 0}
    out, did = await c.maybe_compact(msgs, used, window)
    assert not did and not called               # 阈下原样返回
    above = int(COMPACT_THRESHOLD * window) + 1
    out, did = await c.maybe_compact(msgs, {"input_tokens": above}, window)
    assert did and called == [window]           # 阈上压缩且窗口透传正确


# ---------------------------------------------------------------- 校验脚本
def _run_check(tmp_path, content: str) -> int:
    import importlib.util
    f = tmp_path / "models.json"
    f.write_text(content, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cm", "scripts/check_models.py")
    cm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cm)
    cm.CATALOG = f
    return cm.main()


def test_check_models_ok(tmp_path, capsys):
    assert _run_check(tmp_path, json.dumps(
        {"version": 1, "models": {"m": {"context_window": 1000}}})) == 0
    assert "✓" in capsys.readouterr().out


def test_check_models_rejects(tmp_path, capsys):
    # 重复键（后者静默覆盖前者——目录禁止）
    assert _run_check(tmp_path,
                      '{"version":1,"models":{"m":{"context_window":1000,'
                      '"context_window":2000}}}') == 1
    # 缺必填 / 非正整数
    assert _run_check(tmp_path, '{"version":1,"models":{"m":{"max_output":1}}}') == 1
    assert _run_check(tmp_path,
                      '{"version":1,"models":{"m":{"context_window":-5}}}') == 1
    # 量级疑错（<1000 的「窗口」）
    assert _run_check(tmp_path,
                      '{"version":1,"models":{"m":{"context_window":200}}}') == 1
    # 坏 JSON / 空目录
    assert _run_check(tmp_path, "{broken") == 1
    assert _run_check(tmp_path, '{"version":1,"models":{}}') == 1
