"""A3/A3b 对抗用例（§8）：不可逆动作确认码门 + 审批摘要防伪造。

A3  注入驱动 r mail 发信无确认码 → 挂起/拒绝；TTL 过期 expired（fail-closed）
A3b 注入提供假 note 试图让用户批准真实目标为攻击者的发信
    → 卡片目标地址由平台从 params 渲染，note 无法覆盖；params_hash 不符拒执行
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("sec.a3")]

import os
import subprocess
import sys
import time as time_mod

import pytest_asyncio

from loadn_webui.security import approve


@pytest_asyncio.fixture()
async def sid(client):
    r = await client.post("/api/sessions", json={"title": "A3 审批"})
    return r.json()["session"]["id"]


# ---------------------------------------------------- 单元：平台渲染防伪造（A3b 核心）

def test_a3b_summary_rendered_from_params_not_note():
    """summary=平台渲染（to 取 params 值）；note 只进 agent_note 弱化字段。"""
    out = approve.create("s1", "mail_send",
                         {"to": "attacker@evil.com", "subject": "发票"},
                         note="发给客户 John 的正常邮件")
    assert "attacker@evil.com" in out["summary"]          # 用户看到真目标
    assert "John" not in out["summary"]                   # note 污染不进 summary


def test_a3b_params_hash_gates_execution(sid):
    """批准 A 不能执行 B：consume 的 params 与批准时不同 → 拒。"""
    a = approve.create(sid, "mail_send", {"to": "a@x.com", "subject": "s"})
    d = approve.decide(a["id"], True)
    code = d["code"]
    # 攻击者换收件人执行
    r = approve.consume(sid, "mail_send", {"to": "b@evil.com", "subject": "s"}, code)
    assert not r["ok"]
    # 正确参数 + 正确码 → 过
    r2 = approve.consume(sid, "mail_send", {"to": "a@x.com", "subject": "s"}, code)
    assert r2["ok"]


# ---------------------------------------------------- 单元：码语义

def test_code_semantics(sid):
    a = approve.create(sid, "account_write", {"platform": "x", "fields": ["password"]})
    d = approve.decide(a["id"], True)
    assert d["ok"] and len(d["code"]) == 6
    # 码错拒
    assert not approve.consume(sid, "account_write",
                               {"platform": "x", "fields": ["password"]}, "000000")[ "ok"]
    # 码对过 → single-use（第二次拒）
    p = {"platform": "x", "fields": ["password"]}
    assert approve.consume(sid, "account_write", p, d["code"])["ok"]
    assert not approve.consume(sid, "account_write", p, d["code"])["ok"]


def test_denied_never_executable(sid):
    a = approve.create(sid, "mail_send", {"to": "x@y.z", "subject": "s"})
    approve.decide(a["id"], False)
    # 拒绝后无码可执行
    assert not approve.consume(sid, "mail_send", {"to": "x@y.z", "subject": "s"},
                               "123456")["ok"]


def test_ttl_expired_fail_closed(sid, monkeypatch):
    """A3 fail-closed：TTL 过期 → expired，批准了也不能执行。"""
    a = approve.create(sid, "mail_send", {"to": "x@y.z", "subject": "s"},
                       ttl_s=1)
    time_mod.sleep(1.2)
    d = approve.decide(a["id"], True)
    assert not d["ok"] and d["status"] == "expired"


# ---------------------------------------------------- API 集成

async def test_api_full_flow(client, sid):
    r = await client.post(f"/api/sessions/{sid}/approvals",
                          json={"action_type": "mail_send",
                                "params": {"to": "user@example.com", "subject": "hi"},
                                "note": "test"})
    assert r.status_code == 200
    aid = r.json()["id"]
    assert "user@example.com" in r.json()["summary"]
    r2 = await client.post(f"/api/approvals/{aid}/decide", json={"approve": True})
    assert r2.status_code == 200 and len(r2.json()["code"]) == 6
    r3 = await client.post("/api/approvals/consume",
                           json={"sid": sid, "action_type": "mail_send",
                                 "params": {"to": "user@example.com", "subject": "hi"},
                                 "confirm_code": r2.json()["code"]})
    assert r3.json()["ok"]


# ---------------------------------------------------- AC-4.2：管理面驳回

async def test_admin_deny_any_session(client, sid, monkeypatch):
    """>>> admin 驳回他人会话的待审：放行（管理面止损），decided_by=admin 入审计。"""
    from loadn_webui.security import userauth as ua
    r = await client.post(f"/api/sessions/{sid}/approvals",
                          json={"action_type": "mail_send",
                                "params": {"to": "x@y.z", "subject": "s"}})
    aid = r.json()["id"]
    monkeypatch.setattr(ua, "current_user",
                        lambda: {"id": 1, "role": "admin", "username": "root"})
    r2 = await client.post(f"/api/approvals/{aid}/decide", json={"approve": False})
    assert r2.status_code == 200 and r2.json()["ok"]
    assert approve.status(aid)["status"] == "denied"
    # decided_by='admin' 直查库断言（status() 面向终端不回该列）
    with approve._conn() as _c:
        _row = _c.execute("SELECT decided_by FROM approvals WHERE id=?",
                          (aid,)).fetchone()
    assert _row["decided_by"] == "admin"


async def test_plain_user_deny_others_404(client, sid, monkeypatch):
    """>>> 否定路径对赌：普通用户驳回他人会话待审 → 404（存在性不暴露）。"""
    from loadn_webui.security import userauth as ua
    r = await client.post(f"/api/sessions/{sid}/approvals",
                          json={"action_type": "mail_send",
                                "params": {"to": "x@y.z", "subject": "s"}})
    aid = r.json()["id"]
    # 会话属主设为别人（owner_id=99），当前用户 id=7
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET owner_id=99 WHERE id=?", (sid,))
    monkeypatch.setattr(ua, "current_user",
                        lambda: {"id": 7, "role": "user", "username": "u"})
    r2 = await client.post(f"/api/approvals/{aid}/decide", json={"approve": False})
    assert r2.status_code == 404
    assert approve.status(aid)["status"] == "pending"   # 未被误杀


# ---------------------------------------------------- CLI 门函数级（A3 场景）

async def test_a3_gate_enforce_blocks_without_code(client, sid, monkeypatch, capsys):
    """A3：enforce 模式下注入驱动 `r mail --to`（无审批参数）→ 门直接拒。

    子进程会重载 config（monkeypatch 不及），故直测门函数；CLI 子进程的
    warn 双轨路径由生产配置控制（approval_enforce=warn）。
    """
    from types import SimpleNamespace

    from loadn_webui import cli as cli_mod
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "enforce")
    args = SimpleNamespace(confirm="", request_approval="")
    gate = await cli_mod._approval_gate(
        args, "mail_send", {"to": "attacker@evil.com", "subject": "x"},
        "mail --to attacker@evil.com")
    assert gate == 2                      # 拒（未执行 mail_send）
    assert "--request-approval" in capsys.readouterr().err


async def test_a3_gate_warn_mode_audits_and_passes(client, sid, monkeypatch):
    """双轨期 warn：无门径放行 + 审计告警留痕（返回 None=放行）。"""
    from types import SimpleNamespace

    from loadn_webui import cli as cli_mod
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "warn")
    args = SimpleNamespace(confirm="", request_approval="")
    gate = await cli_mod._approval_gate(
        args, "mail_send", {"to": "x@y.z", "subject": "s"}, "mail --to x@y.z")
    assert gate is None                   # 放行（执行由调用方继续）


async def test_a3_cli_confirm_with_wrong_code_rejected(client, sid, server_url,
                                                       monkeypatch):
    """A3 CLI 码通道：--confirm 码错 → exit 2（真子进程，走 API）。"""
    from loadn_webui.security import approve as approve_mod
    _cli_env(server_url, sid, monkeypatch)
    a = approve_mod.create(sid, "mail_send", {"to": "x@y.z", "subject": "s"})
    approve_mod.decide(a["id"], True)
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui", "r", "mail",
         "--to", "x@y.z", "--subject", "s", "--confirm", "999999"],
        capture_output=True, text=True, timeout=60, env={**os.environ})
    assert p.returncode == 2
    assert "确认码门未过" in p.stderr


def _cli_env(server_url, sid, monkeypatch):
    monkeypatch.setenv("LOADN_API_BASE", server_url)
    monkeypatch.setenv("LOADN_SESSION_ID", sid)


async def test_a3_cli_request_approval_pends_then_expires(client, sid, server_url,
                                                          monkeypatch):
    """A3：--request-approval 挂起；无人裁决 → fail-closed 不执行。"""
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "approval_enforce", "warn")
    _cli_env(server_url, sid, monkeypatch)
    # TTL 压短：直接建一条 1s TTL 的（走 API），CLI 挂起轮询同一状态机
    a = approve.create(sid, "mail_send", {"to": "x@y.z", "subject": "s"}, ttl_s=1)
    time_mod.sleep(1.2)
    d = approve.decide(a["id"], True)
    assert d["status"] == "expired"           # 挂起中的请求超时即 expired
    # 而 CLI 的拒绝路径（enforce+无码）已在上一用例覆盖


def test_r3_consume_concurrent_single_execution(tmp_path):
    """三轮修对赌：两线程带同码并发 consume → 恰一个 ok（原 SELECT→UPDATE
    无写事务无守卫=双 ok，不可逆动作双执行：双付费/双发信）。BEGIN
    IMMEDIATE 串行化后第二方 SELECT 见 executed 走拒绝分支。"""
    import threading

    from loadn_webui.security import approve as ap
    out = ap.create("s-r3c", "mail_send", {"to": "x@y.z"})
    aid = out["id"]
    code = ap.decide(aid, True, by="t-setup")["code"]
    results = []

    def run():
        try:
            results.append(ap.consume("s-r3c", "mail_send",
                                      {"to": "x@y.z"}, code))
        except Exception as e:                     # noqa: BLE001
            results.append({"ok": False, "error": str(e)})
    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start(); t2.start(); t1.join(15); t2.join(15)
    assert sum(1 for r in results if r.get("ok")) == 1, results
    assert ap.status(aid)["status"] == "executed"


def test_r3_decide_concurrent_threads_serialized(tmp_path):
    """三轮修对赌（多线程面）：两线程同时 decide 同一审批（一批准一
    否决）——BEGIN IMMEDIATE 串行化：恰一个 ok 且终态与赢家一致。"""
    import threading

    from loadn_webui.security import approve as ap
    out = ap.create("s-r3t", "mail_send", {"to": "x@y.z"})
    aid = out["id"]
    results = {}

    def run(key, approve_):
        try:
            results[key] = ap.decide(aid, approve_, by=f"t-{key}")
        except Exception as e:                     # noqa: BLE001
            results[key] = {"ok": False, "error": str(e)}
    t1 = threading.Thread(target=run, args=("approve", True))
    t2 = threading.Thread(target=run, args=("deny", False))
    t1.start(); t2.start(); t1.join(15); t2.join(15)
    ok = [k for k, r in results.items() if r.get("ok")]
    assert len(ok) == 1, results
    real = ap.status(aid)["status"]
    assert real == ("approved" if ok[0] == "approve" else "denied")


def test_r3_sweep_expired_clears_zombies(tmp_path):
    """三轮修对赌：过期清扫——真超 TTL 的 pending 翻 expired 且补
    decided_at（生产 23 行僵尸实证）；未过期的原样保留（否定路径）。"""
    from loadn_webui.security import approve as ap
    fresh = ap.create("s-sw", "mail_send", {"to": "a@b.c"})   # TTL 600s 未到
    # 造一条已过期的：created_at 拨回 2 天前
    stale = ap.create("s-sw", "mail_send", {"to": "d@e.f"})
    with ap._conn() as c:
        from datetime import datetime, timedelta, timezone
        old_t = (datetime.now(timezone.utc)
                 - timedelta(days=2)).isoformat(timespec="seconds")
        c.execute("UPDATE approvals SET created_at=? WHERE id=?",
                  (old_t, stale["id"]))
    n = ap.sweep_expired()
    assert n >= 1
    assert ap.status(stale["id"])["status"] == "expired"
    with ap._conn() as c:
        decided = c.execute(
            "SELECT decided_at FROM approvals WHERE id=?",
            (stale["id"],)).fetchone()["decided_at"]
    assert decided, "清扫须补 decided_at（P8 时间轴残缺根因）"
    assert ap.status(fresh["id"])["status"] == "pending"   # 未过期不动
    assert ap.sweep_expired() == 0                          # 幂等
