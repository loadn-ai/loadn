"""policy_check_hook 工具面守卫对赌（M1-policy 突变存活补测）。

存活变异（93 测试全绿漏杀的三处，均真盲区）：
- L482 `tool in ("write","edit","multiedit")` → not in：hook 写路径
  守卫整体反转——敏感路径拦截/写前快照不再作用于写工具，反而作用于
  非写工具。既有测试只打 check_command/check_path 直连，hook 的
  write/read 分派从无对赌
- L487 `tool == "read"` → !=：读工具敏感路径拦截反转
- L432 `subcommand in irreversible_tools` → not in：不可逆告警门反转

另：L70 net_cmds 存活根因是 policy 窄测试映射漏了 test_security_knobs
——映射已补（真盲区在映射而非测试）。
"""
from __future__ import annotations

import json

import pytest

from loadn_webui import policy
from loadn_webui.config import CONFIG


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(home))
    return home


def _hook(tool: str, inp: dict) -> int:
    return policy.policy_check_hook(json.dumps(
        {"tool_name": tool, "tool_input": inp}))


# ---------------------------------------------------------------- 写工具守卫
def test_hook_write_sensitive_path_blocks():
    """hook 面：Write 敏感路径 → exit 2（check_path 经 hook 分派生效）。"""
    assert _hook("write", {"file_path": "var/vault.enc", "content": "x"}) == 2


def test_hook_edit_sensitive_path_blocks():
    assert _hook("edit", {"file_path": "/home/u/.ssh/id_rsa",
                          "old_string": "a", "new_string": "b"}) == 2


def test_hook_multiedit_sensitive_path_blocks():
    assert _hook("multiedit", {"file_path": "config.yaml",
                               "edits": []}) == 2


def test_hook_write_normal_path_passes(tmp_path):
    assert _hook("write", {"file_path": str(tmp_path / "ok.txt"),
                           "content": "x"}) == 0


def test_hook_write_gate_must_not_apply_to_read():
    """守卫定向：read 不走写分支（写前快照/写黑名单不误伤读）。"""
    # 敏感路径的 read 也拦（read 分支同黑名单），但普通文件 read 放行
    assert _hook("read", {"file_path": "notes/普通.txt"}) == 0


def test_hook_read_sensitive_path_blocks():
    assert _hook("read", {"file_path": "var/loadn.db"}) == 2


def test_hook_unknown_tool_bash_passthrough(monkeypatch):
    """非 read/write 工具（如 bash）走 check_command 支路不炸。"""
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    rc = _hook("bash", {"command": "echo hi"})
    assert rc == 0


# ---------------------------------------------------------------- 不可逆告警
def test_cli_gateway_irreversible_warns(monkeypatch, tmp_path):
    """irreversible_tools 命中 → 审计 permission_decision warn（L432 对赌）。"""
    warns = []
    monkeypatch.setattr(policy, "audit",
                        lambda t, d, **kw: warns.append(d)
                        if "warn" in str(d.get("action", "")) else None)
    argv = ["r", "mail", "--to", "a@b.c"]
    policy.cli_gateway(argv, "mail")
    assert any(w.get("reason", "").startswith("mail=不可逆工具")
               for w in warns)


def test_cli_gateway_reversible_no_warn(monkeypatch):
    """非 irreversible 子命令 → 无不可逆告警。"""
    warns = []
    monkeypatch.setattr(policy, "audit",
                        lambda t, d, **kw: warns.append(d)
                        if "不可逆" in str(d) else None)
    policy.cli_gateway(["r", "fetch", "https://x"], "fetch")
    assert not warns


def test_first_literal_no_attrs_returns_empty():
    """parts 缺失/None 的节点优雅空串（or 回退位——None 迭代=TypeError）。"""
    from types import SimpleNamespace

    from loadn_webui.policy import _first_literal
    assert _first_literal(SimpleNamespace()) == ""
    assert _first_literal(SimpleNamespace(parts=None)) == ""
