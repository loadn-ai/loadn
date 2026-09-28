"""W2 档位统一枚举：resolve_tier 解析矩阵 / 降档 fail-closed 遥测口径 /
sandbox_tier 审计 / posture+health API 面 / load_config 枚举校验。"""
from __future__ import annotations

import json

import pytest

from loadn_webui.config import CONFIG, SANDBOX_TIERS
from loadn_webui.security import sandbox as sandbox_mod


@pytest.fixture(autouse=True)
def _fresh_tier_cache():
    sandbox_mod._reset_tier_cache()
    yield
    sandbox_mod._reset_tier_cache()


# ---------------- resolve_tier 解析矩阵 ----------------

def test_resolve_off_is_honest_direct():
    assert sandbox_mod.resolve_tier("off") == ("off", "")


def test_resolve_bwrap_when_available(monkeypatch):
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: True)
    assert sandbox_mod.resolve_tier("bwrap") == ("bwrap", "")


def test_resolve_bwrap_unavailable_fail_closed(monkeypatch):
    """探测失败 → 降 off + 原因（bwrap 与 vm-bwrap 同走 bwrap 探测）。"""
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: False)
    assert sandbox_mod.resolve_tier("bwrap") == ("off", "bwrap-unavailable")
    assert sandbox_mod.resolve_tier("vm-bwrap") == ("off", "bwrap-unavailable")


def test_resolve_vm_bwrap_exec_semantics_is_bwrap(monkeypatch):
    """vm-bwrap：执行语义=bwrap（requested 值即「桌面 VM 执行域」报告标记）。"""
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: True)
    eff, reason = sandbox_mod.resolve_tier("vm-bwrap")
    assert eff == "vm-bwrap" and reason == ""


@pytest.mark.parametrize("tier,reason", [
    ("seatbelt", "seatbelt-not-implemented"),
    ("appcontainer", "appcontainer-not-implemented"),
    ("remote", "remote-not-implemented"),
])
def test_resolve_placeholder_tiers_fail_closed(tier, reason):
    """B/C 方案枚举占位：接受配置但未实现 → 降 off + 原因（不静默装作隔离）。"""
    eff, why = sandbox_mod.resolve_tier(tier)
    assert eff == "off" and why == reason


def test_resolve_unknown_tier_defensive():
    eff, why = sandbox_mod.resolve_tier("bwrapx")
    assert eff == "off" and why.startswith("unknown-tier:")


def test_probe_runs_once_per_process(monkeypatch):
    """bwrap_available() 真跑子进程——探测进程内只允许一次。"""
    calls = []

    def fake_probe():
        calls.append(1)
        return True
    monkeypatch.setattr(sandbox_mod, "bwrap_available", fake_probe)
    for _ in range(3):
        sandbox_mod.resolve_tier("bwrap")
    assert len(calls) == 1


def test_resolution_cached_per_requested(monkeypatch):
    """缓存按 requested 值键控：切档重解析（sandbox 是重启语义配置）。"""
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: True)
    assert sandbox_mod.resolve_tier("bwrap") == ("bwrap", "")
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: False)
    assert sandbox_mod.resolve_tier("bwrap") == ("bwrap", "")   # 命中缓存
    assert sandbox_mod.resolve_tier("off") == ("off", "")        # 新值重解析


# ---------------- wrap_engine 档位接线 ----------------

def test_wrap_engine_explicit_off_is_direct(monkeypatch, tmp_path):
    monkeypatch.setattr(CONFIG.security, "sandbox", "off")
    cmd, mode = sandbox_mod.wrap_engine(["echo"], {}, engine="claude",
                                        sid="s", cwd=str(tmp_path))
    assert mode == "direct"


def test_wrap_engine_downgrade_reports_fallback(monkeypatch, tmp_path):
    """降档直跑必须报 direct-fallback（复用 sandbox_violation 遥测口径），
    与显式 off 的 direct 区分——安全预期不错配。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "seatbelt")
    cmd, mode = sandbox_mod.wrap_engine(["echo"], {}, engine="claude",
                                        sid="s", cwd=str(tmp_path))
    assert mode == "direct-fallback" and cmd == ["echo"]


def test_wrap_engine_vm_bwrap_routes_to_bwrap(monkeypatch, tmp_path):
    """vm-bwrap 生效时与 bwrap 同路（走引擎包裹分支）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "vm-bwrap")
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: True)
    monkeypatch.setattr(sandbox_mod, "_wrap_generic",
                        lambda cmd, env, cwd, extra_binds, project_root=None:
                        ["wrapped"])
    _, mode = sandbox_mod.wrap_engine(["bin"], {}, engine="claude",
                                      sid="s", cwd=str(tmp_path))
    assert mode == "bwrap"


# ---------------- 审计 + API 面 ----------------

def test_resolve_and_audit_writes_event(monkeypatch):
    from loadn_webui.security import audit as audit_mod
    monkeypatch.setattr(CONFIG.security, "sandbox", "seatbelt")
    st = sandbox_mod.resolve_and_audit()
    assert st == {"requested": "seatbelt", "effective": "off",
                  "reason": "seatbelt-not-implemented"}
    rows = audit_mod.tail(5, "sandbox_tier")
    assert rows, "启动解析必须落 sandbox_tier 审计行"
    assert json.loads(rows[0]["detail_json"])["requested"] == "seatbelt"
    assert audit_mod.verify() == []       # 链完整性不受新事件类型影响


def test_tier_status_reads_config(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(sandbox_mod, "bwrap_available", lambda: True)
    assert sandbox_mod.tier_status() == {
        "requested": "bwrap", "effective": "bwrap", "reason": ""}


async def test_posture_and_health_expose_tier(client, monkeypatch):
    """/api/admin/security 与 /api/health 均暴露 requested/effective/reason。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "seatbelt")
    d = (await client.get("/api/admin/security")).json()["sandbox"]
    assert d["requested"] == "seatbelt" and d["effective"] == "off"
    assert d["reason"] == "seatbelt-not-implemented"
    assert d["mode"] == "seatbelt"              # 兼容旧字段
    h = (await client.get("/api/health")).json()["sandbox"]
    assert h["requested"] == "seatbelt" and h["effective"] == "off"


# ---------------- load_config 枚举校验（fail-closed） ----------------

def test_load_config_rejects_unknown_tier(tmp_path, monkeypatch):
    """安全关键配置笔误拒绝启动（不静默降级直跑）。"""
    from loadn_webui import config as config_mod
    (tmp_path / "config.yaml").write_text(
        "security:\n  sandbox: bwrapx\n")
    monkeypatch.setattr(config_mod, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="不在档位枚举"):
        config_mod.load_config()


def test_load_config_accepts_all_enum(tmp_path, monkeypatch):
    from loadn_webui import config as config_mod
    for tier in SANDBOX_TIERS:
        (tmp_path / "config.yaml").write_text(f"security:\n  sandbox: {tier}\n")
        monkeypatch.setattr(config_mod, "ROOT", tmp_path)
        assert config_mod.load_config().security.sandbox == tier
