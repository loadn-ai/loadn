"""A3 CLI 审批接线契约（S1/S2/S3，2026-11-16 死锁复盘落地）。

钉四层（缺一不可——11-04 生产死锁正是四层同时失守）：
1. 解析级——R_GATED_COMMANDS 每个成员的 parser 都声明审批 flag
   （漏接线=CI 显形；此前 account 缺 flag 定义穿透到生产死锁，
   唯一 roundtrip 测试又主动 warn 绕门，CI 全绿下不可见）；
2. 门函数 fail-fast——缺属性=wiring_error 拒+审计（不再 fail-soft
   getattr 把「未声明」混同「用户没传」——warn 部署下那是审批被静默架空）；
3. 负路径——enforce 无码拒（真实 parser namespace，非 SimpleNamespace
   伪造）；--del 无码拒（此前 --del 完全无门）；
4. roundtrip e2e——无码被拦→request 产码→confirm 写入→读回一致
   （v0.7.74 修复的成功判据，此前只在提交信息人工实证过一次）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio


def _r_parser() -> argparse.ArgumentParser:
    """构建与 main 等价的 r 子树 parser（真实构建——接线缺陷只有真实
    parser 能暴露，SimpleNamespace 会替被测代码补上缺的属性）。"""
    from loadn_webui import cli as cli_mod
    top = argparse.ArgumentParser(prog="loadn-web")
    sub = top.add_subparsers(dest="cmd", required=True)
    cli_mod._build_r_parser(sub)
    return top


# 每个 gated 子命令的典型 argv（新登记成员必须补样例，防「注册表加了
# 测试不知道」的漂移）
_GATED_ARGV = {
    "mail": ["r", "mail", "--to", "x@y.z", "--subject", "s", "--body", "b"],
    "account": ["r", "account", "--platform", "demo", "--set", "password=p1"],
}


# ---------------------------------------------------- 1. 解析级（flag 存在性钉子）
def test_gated_subcommands_have_approval_flags():
    """注册表成员逐一解析：--request-approval/--confirm 可解析且 dest 正确。"""
    from loadn_webui.security.approve import R_GATED_COMMANDS
    top = _r_parser()
    for rcmd in R_GATED_COMMANDS:
        assert rcmd in _GATED_ARGV, f"gated 子命令 {rcmd} 缺解析样例（请补）"
        ns = top.parse_args(_GATED_ARGV[rcmd]
                            + ["--request-approval", "note",
                               "--confirm", "123456"])
        assert ns.request_approval == "note", rcmd
        assert ns.confirm == "123456", rcmd


def test_ungated_subcommand_rejects_approval_flags():
    """非注册表子命令不声明审批 flag（注册表=单一真源对赌）。"""
    top = _r_parser()
    with pytest.raises(SystemExit) as e:
        top.parse_args(["r", "fetch", "https://example.com", "--confirm", "1"])
    assert e.value.code == 2


# ---------------------------------------------------- 2. 门函数 fail-fast
async def test_gate_wiring_error_fail_fast(monkeypatch, capsys):
    """缺 confirm 属性（=parser 未声明）→ wiring_error 拒 + 审计留痕。

    旧 getattr fail-soft 形态：此缺陷在 enforce=死锁、warn=静默绕过，
    都不该被放行——fail-fast 两种部署下即刻显形。
    """
    from loadn_webui import cli as cli_mod
    from loadn_webui.security import audit as audit_mod
    wired = []
    monkeypatch.setattr(audit_mod, "audit",
                        lambda t, d, **kw: wired.append(d))
    args = SimpleNamespace(request_approval="x")   # 缺 confirm（接线缺陷形态）
    gate = await cli_mod._approval_gate(
        args, "account_write", {}, "account --set")
    assert gate == 2
    assert "wiring_error" in capsys.readouterr().err
    assert any(d.get("action") == "wiring_error" for d in wired)


async def test_gate_wiring_error_under_warn_too(monkeypatch, capsys):
    """warn 部署同样 fail-fast——迁移期不再是审批被架空的窗口（S7 收紧）。"""
    from loadn_webui import cli as cli_mod
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "warn")
    args = SimpleNamespace(confirm="")             # 缺 request_approval
    gate = await cli_mod._approval_gate(
        args, "account_write", {}, "account --set")
    assert gate == 2                               # 不再 warn 放行
    assert "wiring_error" in capsys.readouterr().err


# ---------------------------------------------------- 3. 负路径（真实 parser namespace）
async def test_account_set_enforce_rejects_without_code(monkeypatch, capsys):
    """真实 parser namespace（非伪造）：enforce 无码 → 拒 + 提示自证。"""
    from loadn_webui import cli as cli_mod
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "enforce")
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    ns = _r_parser().parse_args(
        ["r", "account", "--platform", "demo", "--set", "password=p"])
    gate = await cli_mod._approval_gate(
        ns, "account_write", {"platform": "demo"},
        "account --platform demo --set …")
    assert gate == 2
    assert "--request-approval" in capsys.readouterr().err


def test_account_del_enforce_rejects_without_code(monkeypatch, capsys):
    """--del 补门对赌：enforce 无码删除被拒（此前完全无门）。"""
    from loadn_webui import cli as cli_mod
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "enforce")
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    rc = cli_mod.main(["r", "account", "--platform", "demo", "--del"])
    assert rc == 2
    assert "--request-approval" in capsys.readouterr().err


def test_account_del_requires_platform(monkeypatch, capsys):
    """--del 无 --platform → 显式报错（此前静默落进列清单分支吞掉删除意图）。"""
    from loadn_webui import cli as cli_mod
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    rc = cli_mod.main(["r", "account", "--del"])
    assert rc == 2
    assert "--platform" in capsys.readouterr().err


# ---------------------------------------------------- 4. 网关告警（G5 回归钉子）
def test_cli_gateway_warns_on_account(monkeypatch):
    """account 子命令命中网关不可逆告警（旧 irreversible_tools 双词汇表
    对 account 永不命中——注册表修复后必须命中）。"""
    from loadn_webui.security import policy
    warns = []
    monkeypatch.setattr(policy, "audit",
                        lambda t, d, **kw: warns.append(d)
                        if "不可逆" in str(d.get("reason", "")) else None)
    policy.cli_gateway(["r", "account", "--platform", "x", "--set",
                        "password=y"], "account")
    assert any(w.get("reason", "").startswith("account=不可逆工具")
               for w in warns)


# ---------------------------------------------------- 5. roundtrip e2e（成功判据固化）
@pytest_asyncio.fixture
async def _sid(client):
    """起一个真实会话（审批 sid 上下文）——与 test_a3 的 sid 同构。"""
    r = await client.post("/api/sessions", json={"title": "a3-wiring-e2e"})
    assert r.status_code == 200, r.text
    return r.json()["session"]["id"]


async def test_account_roundtrip_e2e(server_url, _sid, monkeypatch):
    """成功判据固化（真子进程真 API，vault 落在测试隔离根）：
    ① 无码写入被 enforce 拦 → ② 审批创建+批准取码 → ③ --confirm 写入
    → ④ --field 读回一致。
    """
    from loadn_webui.security import approve as approve_mod
    monkeypatch.setenv("LOADN_API_BASE", server_url)
    monkeypatch.setenv("LOADN_SESSION_ID", _sid)
    env = {**os.environ}
    plat = "e2e-a3w"

    # ① 无码被拦（子进程 config 无测试 yaml → approval_enforce 默认 enforce）
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui", "r", "account",
         "--platform", plat, "--set", "password=old1"],
        capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 2
    assert "--request-approval" in p.stderr

    # ② 审批链：创建 → 批准 → 取一次性码（params 与门上报口径一致）
    a = approve_mod.create(_sid, "account_write",
                           {"platform": plat, "fields": ["password"]})
    d = approve_mod.decide(a["id"], True)
    assert d["ok"] and d.get("code")

    # ③ --confirm 写入
    p2 = subprocess.run(
        [sys.executable, "-m", "loadn_webui", "r", "account",
         "--platform", plat, "--set", "password=new9",
         "--confirm", d["code"]],
        capture_output=True, text=True, timeout=60, env=env)
    assert p2.returncode == 0, p2.stderr

    # ④ 读回一致（单值输出形态）
    p3 = subprocess.run(
        [sys.executable, "-m", "loadn_webui", "r", "account",
         "--platform", plat, "--field", "password"],
        capture_output=True, text=True, timeout=60, env=env)
    assert p3.returncode == 0
    assert p3.stdout.strip() == "new9"
