"""直跑档位 MCP 执行域门（三起生产实证 2026-10-08：7227×2 + c45d）。

冷启动模型被 sandbox 容器 bash 的「在场感」吸进外置容器，把「容器里看不到
宿主路径」误诊成「工具未注入/执行域降级」——宪法与轮换 anchor 的文字路标
拦不住（第三起 thinking 引用路标原文仍进容器）。结构性门：off 档把
mcp__sandbox__sandbox_execute_bash 写进会话 settings 的 deny，调用即
「权限规则拒绝」回填、模型改道内建 Bash（WebSearch/WebFetch 同机制先例）。

覆盖：
- _off_tier_mcp_gate 按档位分形（off=清单 / bwrap=空 / 渲染异常=空）
- write_settings 双落点（.claude disallow + .loadn/.agent deny）
- config 清空门否定路径（off 档 + 空清单 → 不注入）
- 引擎 PermissionEngine 吃 settings.deny 的端到端面（deny 条目命中即拒）
"""
from __future__ import annotations

import json
from pathlib import Path

from loadn_webui import workspace as ws_mod
from loadn_webui.workspace import _off_tier_mcp_gate


def _gate_via_tier(monkeypatch, eff: str):
    from loadn_webui.security import sandbox as sbx
    monkeypatch.setattr(sbx, "resolve_tier", lambda req=None: (eff, ""))


def test_gate_off_tier_returns_configured_list():
    """off 档（默认部署形态）：返回默认清单（fail-closed 出厂即拦）。"""
    assert _off_tier_mcp_gate() == ["mcp__sandbox__sandbox_execute_bash"]


def test_gate_bwrap_tier_empty(monkeypatch):
    """隔离档：内建 Bash 本就在平台沙箱内，容器 bash 无「错域」危害——门空。"""
    _gate_via_tier(monkeypatch, "bwrap")
    assert _off_tier_mcp_gate() == []


def test_gate_resolve_failure_empty(monkeypatch):
    """resolve_tier 抛异常 → 空清单（宪法渲染的降级语义同源，不炸 settings）。"""
    from loadn_webui.security import sandbox as sbx
    def _boom(req=None):
        raise RuntimeError("tier probe down")
    monkeypatch.setattr(sbx, "resolve_tier", _boom)
    assert _off_tier_mcp_gate() == []


def test_gate_config_empty_disables(monkeypatch):
    """否定路径：off 档但配置显式清空清单 → 不注入（合法放开面）。"""
    _gate_via_tier(monkeypatch, "off")
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "off_tier_mcp_disallow", [])
    assert _off_tier_mcp_gate() == []


def test_write_settings_gate_both_channels(tmp_path: Path, monkeypatch):
    """write_settings 双落点对赌：.claude disallow 与 .loadn/.agent deny
    三处都带门（引擎侧 PermissionEngine 读 deny，claude 侧读 disallow）。"""
    _gate_via_tier(monkeypatch, "off")
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("assistant")
    ws_mod.write_settings(tmp_path, "sid-gate", prof)
    st = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "mcp__sandbox__sandbox_execute_bash" in st["permissions"]["disallow"]
    for rel in (".loadn", ".agent"):
        ag = json.loads((tmp_path / rel / "settings.json").read_text())
        assert "mcp__sandbox__sandbox_execute_bash" in ag["permissions"]["deny"]


def test_write_settings_bwrap_no_gate(tmp_path: Path, monkeypatch):
    """否定路径：隔离档写出的 settings 不带门（档位反转不得串台）。"""
    _gate_via_tier(monkeypatch, "bwrap")
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("assistant")
    ws_mod.write_settings(tmp_path, "sid-bwrap", prof)
    st = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "mcp__sandbox__sandbox_execute_bash" not in st["permissions"]["disallow"]
    ag = json.loads((tmp_path / ".loadn" / "settings.json").read_text())
    assert "mcp__sandbox__sandbox_execute_bash" not in ag["permissions"]["deny"]


def test_engine_permissions_deny_gates_call(tmp_path: Path):
    """端到端面：引擎 PermissionEngine 加载 .loadn/settings.json 的 deny，
    对 mcp__sandbox__sandbox_execute_bash 的调用判定拒绝（回填文案点名规则）
    ——这是「模型改道内建 Bash」的执行点，deny 写了但不生效=门是摆设。"""
    from loadn.core.permissions import PermissionEngine
    d = tmp_path / ".loadn"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps(
        {"permissions": {"deny": ["mcp__sandbox__sandbox_execute_bash"]}}))
    eng = PermissionEngine.load(cwd=tmp_path, mode="bypassPermissions")
    decision = eng.check("mcp__sandbox__sandbox_execute_bash", {"cmd": "ls"})
    assert not decision.allowed
    assert "权限规则拒绝" in decision.reason \
        and "mcp__sandbox__sandbox_execute_bash" in decision.reason
    # 内建 Bash 不受门影响（改道目标必须畅通）
    assert eng.check("Bash", {"command": "ls"}).allowed
