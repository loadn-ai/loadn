"""P10 按目标权限三档：决策序/匹配/fail-closed/窄化/egress 挂点/API。

验收五件：①never 拒绝（approval 建+自动否决，链路留痕）②always 放行
（码随响应、consume 可用）③ask 落原审批门（pending）④三档窄化各生成
正确记录 ⑤策略表损坏回落 ask。
"""
from __future__ import annotations

import json
import sqlite3

from loadn_webui.config import PATHS
from loadn_webui.security import target_policy as tp


def _policy_audit() -> list[dict]:
    p = PATHS["var"] / "audit.db"
    if not p.exists():
        return []
    out = []
    with sqlite3.connect(p) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'audit_events%'")]
        for t in sorted(tables):
            for r in c.execute(f"SELECT type, detail_json FROM {t} "
                               "WHERE type IN ('policy_change','policy_decision',"
                               "'approval_decision') ORDER BY rowid"):
                out.append({"type": r[0], **json.loads(r[1])})
    return out


async def _mk_session(client) -> str:
    r = await client.post("/api/sessions", json={"title": "策略测试"})
    return r.json()["session"]["id"]


# ---------------------------------------------------------------- 单元
def test_match_and_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_WEBUI_HOME", str(tmp_path))
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "eng"))
    assert tp.match_hit("a.com", "a.com") and not tp.match_hit("a.com", "b.com")
    assert tp.match_hit("*.evil.com", "x.evil.com")
    assert tp.match_hit("*.evil.com", "deep.x.evil.com")
    assert not tp.match_hit("*.evil.com", "evil.com")     # 裸域不吃通配
    # 最严优先：同键 never+always 并存 → never
    tp.add("dual.com", "host", "never")
    tp.add("dual.com", "host", "always")
    assert tp.decide("host", "dual.com") == "never"
    # ⑤ 表损坏 → ask（fail-closed）
    with sqlite3.connect(PATHS["db"]) as c:
        c.execute("DROP TABLE target_policies")
    assert tp.decide("host", "dual.com") == "ask"


# ---------------------------------------------------------------- ①②③
async def test_never_always_ask_gates(client):
    sid = await _mk_session(client)
    # ask（无记录）→ pending 原门
    r = await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "mail_send", "params": {"to": "a@b.c"}, "note": "n"})
    assert r.status_code == 200
    out = r.json()
    assert "policy" not in out and out.get("ttl_s")     # 原审批门形状
    # ② always → 自动批准 + 码可 consume
    tp.add("mail_send", "action", "always")
    r = await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "mail_send", "params": {"to": "x@y.z"}})
    out = r.json()
    assert out["policy"] == "always" and out["status"] == "approved"
    assert out["code"] and len(out["code"]) == 6
    r = await client.post("/api/approvals/consume", json={
        "sid": sid, "action_type": "mail_send",
        "params": {"to": "x@y.z"}, "confirm_code": out["code"]})
    assert r.status_code == 200 and r.json().get("ok"), r.text
    # ① never（动作类）→ 建+自动否决
    tp.add("sms_send", "action", "never")
    r = await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "sms_send", "params": {"to": "13900000000"}})
    out = r.json()
    assert out["policy"] == "never" and out["status"] == "denied"
    r = await client.get(f"/api/approvals/{out['id']}")
    assert r.json()["status"] == "denied"
    audits = _policy_audit()
    assert any(a.get("policy") == "target_never" or
               a.get("decision") == "denied" for a in audits)
    # host 维度叠加：never 的 host 盖过 always 的动作类
    tp.add("pay", "action", "always")
    tp.add("evil-pay.com", "host", "never")
    r = await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "pay", "params": {"host": "evil-pay.com", "amt": 1}})
    assert r.json()["policy"] == "never"
    # bash_allow（本地动作）不进此表：加了 never 也不影响
    tp.add("bash_allow", "action", "never")
    r = await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "bash_allow", "params": {"prefix": ["git", "push"]}})
    assert "policy" not in r.json()                      # 走原审批门


# ---------------------------------------------------------------- ④ 窄化
async def test_always_allow_three_scopes(client):
    from loadn_webui.security import approve as approve_mod
    sid = await _mk_session(client)
    out = approve_mod.create(sid, "mail_send",
                             {"host": "api.mail.com", "to": "a@b.c"})
    aid = out["id"]
    # exact：match=动作类+参数指纹
    r = await client.post(f"/api/admin/target-policy/from-approval/{aid}",
                          json={"scope": "exact"})
    m1 = r.json()["match"]
    assert m1.startswith("mail_send:") and len(m1.split(":")[1]) == 12
    assert r.json()["kind"] == "action"
    # domain-action：动作类+域名
    r = await client.post(f"/api/admin/target-policy/from-approval/{aid}",
                          json={"scope": "domain-action"})
    assert r.json()["match"] == "mail_send:api.mail.com"
    # target：host 全放行
    r = await client.post(f"/api/admin/target-policy/from-approval/{aid}",
                          json={"scope": "target"})
    assert r.json() == {"ok": True, "id": r.json()["id"],
                        "match": "api.mail.com", "kind": "host"} or \
        r.json()["match"] == "api.mail.com"
    rows = tp.list_all()
    assert any(x["created_from"] == f"approval:{aid}" for x in rows)
    assert {x["scope_note"] for x in rows} >= {"仅此确切参数",
                                               "此域名+此动作类（api.mail.com）"}
    # 无域名动作：domain-action 拒 400，target=动作类
    out2 = approve_mod.create(sid, "sms_send", {"to": "139"})
    r = await client.post(
        f"/api/admin/target-policy/from-approval/{out2['id']}",
        json={"scope": "domain-action"})
    assert r.status_code == 400
    r = await client.post(
        f"/api/admin/target-policy/from-approval/{out2['id']}",
        json={"scope": "target"})
    assert r.json()["match"] == "sms_send"


# ---------------------------------------------------------------- 管理 API
async def test_admin_crud_and_validation(client):
    r = await client.get("/api/admin/target-policy")
    assert r.status_code == 200 and "policies" in r.json()
    assert (await client.post("/api/admin/target-policy", json={
        "match": "*.x.com", "kind": "host", "mode": "never"})).status_code == 200
    assert (await client.post("/api/admin/target-policy", json={
        "match": "", "kind": "host", "mode": "never"})).status_code == 400
    assert (await client.post("/api/admin/target-policy", json={
        "match": "a.com", "kind": "bogus", "mode": "never"})).status_code == 400
    assert (await client.post("/api/admin/target-policy", json={
        "match": "a.com", "kind": "host", "mode": "yolo"})).status_code == 400
    pid = tp.list_all()[-1]["id"]
    assert (await client.patch(f"/api/admin/target-policy/{pid}",
                               json={"mode": "ask"})).status_code == 200
    assert tp.decide("host", "a.com") == "ask"
    assert (await client.delete(
        f"/api/admin/target-policy/{pid}")).status_code == 200
    assert (await client.delete(
        f"/api/admin/target-policy/{pid}")).status_code == 404
    assert any(a.get("action") == "target_mode" for a in _policy_audit())
