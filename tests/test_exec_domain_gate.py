"""直跑档位 MCP 执行域门（四起生产实证：2026-10-08 7227×2 + c45d；2026-10-10 6f12）。

冷启动模型被 sandbox 容器 bash 的「在场感」吸进外置容器，把「容器里看不到
宿主路径」误诊成「工具未注入/执行域降级」——宪法与轮换 anchor 的文字路标
拦不住（第三起 thinking 引用路标原文仍进容器）。结构性门：off 档把
mcp__sandbox__sandbox_execute_bash 写进会话 settings 的 deny，调用即
「权限规则拒绝」回填、模型改道内建 Bash（WebSearch/WebFetch 同机制先例）。

第 4 起（6f12）暴露 settings-only 的缺口：模型仍被 ToolSearch enum 广告吸走，
deny 后**原地重试 3 次同一被拒调用**、从未试内建 Bash——门拦住了人没指路。
补层：TurnCall.disallowed_tools（profile+门合流）→ loadn/claude 引擎
--disallowedTools argv → 引擎四层同拒（面不注入/索引不广告/物化即拒带改道
路标/调用兜底），拒绝文案按活面点名内建工具。

覆盖：
- _off_tier_mcp_gate 按档位分形（off=清单 / bwrap=空 / 渲染异常=空）
- write_settings 双落点（.claude disallow + .loadn/.agent deny）
- config 清空门否定路径（off 档 + 空清单 → 不注入）
- 引擎 PermissionEngine 吃 settings.deny 的端到端面（deny 条目命中即拒）
- turn_disallow 合流（profile 禁用 + 档位门）与档位分形
- loadn/claude build_argv 的 --disallowedTools 直通（-- 终结符之前）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadn_webui import workspace as ws_mod
from loadn_webui.workspace import _off_tier_mcp_gate


def _gate_via_tier(monkeypatch, eff: str):
    from loadn_webui.security import sandbox as sbx
    monkeypatch.setattr(sbx, "resolve_tier", lambda req=None: (eff, ""))


def test_gate_off_tier_returns_configured_list():
    """off 档（默认部署形态）：返回类级默认清单（DG-2：通配整族，
    fail-closed 出厂即拦——名字级清单第 5 起被 execute_code 绕过）。"""
    assert _off_tier_mcp_gate() == [
        "mcp__sandbox__sandbox_execute_*",
        "mcp__sandbox__sandbox_file_operations",
        "mcp__sandbox__sandbox_str_replace_editor"]


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
    三处都带门（引擎侧 PermissionEngine 读 deny，claude 侧读 disallow）。
    DG-2 起为类级模式串。"""
    _gate_via_tier(monkeypatch, "off")
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("assistant")
    ws_mod.write_settings(tmp_path, "sid-gate", prof)
    st = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "mcp__sandbox__sandbox_execute_*" in st["permissions"]["disallow"]
    for rel in (".loadn", ".agent"):
        ag = json.loads((tmp_path / rel / "settings.json").read_text())
        assert "mcp__sandbox__sandbox_execute_*" in ag["permissions"]["deny"]


def test_write_settings_bwrap_no_gate(tmp_path: Path, monkeypatch):
    """否定路径：隔离档写出的 settings 不带门（档位反转不得串台）。"""
    _gate_via_tier(monkeypatch, "bwrap")
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("assistant")
    ws_mod.write_settings(tmp_path, "sid-bwrap", prof)
    st = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "mcp__sandbox__sandbox_execute_*" not in st["permissions"]["disallow"]
    ag = json.loads((tmp_path / ".loadn" / "settings.json").read_text())
    assert "mcp__sandbox__sandbox_execute_*" not in ag["permissions"]["deny"]


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


# ---------------------------------------------------------------- argv 直通层（第 4 起补层）
def test_turn_disallow_merges_profile_and_gate(monkeypatch):
    """turn_disallow 合流真源：profile 禁用 + off 档门——engine.py 组
    TurnCall.disallowed_tools 的唯一入口，漏一层=引擎面留洞。"""
    _gate_via_tier(monkeypatch, "off")

    class _Prof:
        disallowed_tools = ["WebSearch", "WebFetch"]
    assert ws_mod.turn_disallow(_Prof()) == [
        "WebSearch", "WebFetch",
        "mcp__sandbox__sandbox_execute_*",
        "mcp__sandbox__sandbox_file_operations",
        "mcp__sandbox__sandbox_str_replace_editor"]
    # 否定路径：隔离档门空——只剩 profile 面（档位反转不得串台）
    _gate_via_tier(monkeypatch, "bwrap")
    assert ws_mod.turn_disallow(_Prof()) == ["WebSearch", "WebFetch"]
    # 无 disallowed_tools 属性的 profile 形态：不炸、只出门清单
    _gate_via_tier(monkeypatch, "off")
    assert ws_mod.turn_disallow(type("P", (), {"disallowed_tools": []})()) == [
        "mcp__sandbox__sandbox_execute_*",
        "mcp__sandbox__sandbox_file_operations",
        "mcp__sandbox__sandbox_str_replace_editor"]


def _argv_call(**kw):
    import uuid as _u
    from pathlib import Path as _P

    from loadn_webui.claude_runner import TurnCall
    base = dict(prompt="PROMPT", cwd=_P("/tmp/ws"), session_id=str(_u.uuid4()),
                resume=False, effort="high", model=None, engine="loadn")
    base.update(kw)
    return TurnCall(**base)


@pytest.mark.parametrize("engine_name", ["loadn", "claude"])
def test_engine_argv_carries_disallowed_tools(engine_name):
    """--disallowedTools 直通（claude CLI 同名同义）：TurnCall 携带即成对
    出现、且在 `--` 终结符之前（argv[-1]=PROMPT 契约不破）。"""
    from loadn_webui.engines import ENGINES
    spec = ENGINES[engine_name]
    call = _argv_call(disallowed_tools=["mcp__sandbox__sandbox_execute_bash",
                                        "WebSearch"])
    cmd, _ = spec.build_argv(call)
    assert cmd[-1] == "PROMPT"
    i = cmd.index("--disallowedTools")
    assert cmd[i + 1] == "mcp__sandbox__sandbox_execute_bash"
    assert cmd[i + 2] == "--disallowedTools" \
        and cmd[i + 3] == "WebSearch"          # 可重复成对
    assert cmd.index("--disallowedTools") < cmd.index("--")


def test_engine_argv_no_disallowed_tools_flag_absent():
    """否定路径：空清单不产 flag（argv 面零噪声——旧形态逐字节不变）。"""
    from loadn_webui.engines import ENGINES
    cmd, _ = ENGINES["loadn"].build_argv(_argv_call())
    assert "--disallowedTools" not in cmd
