"""P8 动作台账 API：三源聚合（工具/审批/拦截）+ 过滤/分页/三态。

工具源经 messages.blocks_json（有界 400 行窗口）；审批全态（pending=
被拦截待审批，带 approval_id 供「去审批」）；拦截经审计 tool_call_blocked。
"""
from __future__ import annotations

import json


async def _seed(client):
    """铺三源数据：会话+带 blocks 的 assistant 消息 + 审批 + 拦截审计。"""
    from loadn_webui import db as db_mod
    from loadn_webui.security import approve as approve_mod
    from loadn_webui.security.audit import audit
    r = await client.post("/api/sessions", json={"title": "台账测试"})
    sid = r.json()["session"]["id"]
    blocks = json.dumps([
        {"name": "Bash", "brief": "pytest -q", "is_error": False},
        {"name": "Write", "brief": "docs/x.md", "is_error": True},
        {"name": "WebFetch", "brief": "https://a.b", "is_error": False},
    ], ensure_ascii=False)
    with db_mod.conn() as c:
        db_mod.add_message(c, session_id=sid, turn_id=None,
                           role="assistant", content="ok", blocks_json=blocks)
    approve_mod.create(sid, "mail_send", {"to": "a@b.c"}, note="清理")
    audit("tool_call_blocked", {"tool": "WebFetch", "reason": "egress 拒绝",
                                "target": "https://evil.x"}, sid=sid)
    return sid


async def test_activity_three_sources_and_filters(client):
    sid = await _seed(client)
    r = await client.get("/api/activity")
    assert r.status_code == 200
    items = r.json()["items"]
    kinds = {x["kind"] for x in items}
    assert {"bash", "file", "net", "approval"} <= kinds   # 拦截按工具归类
    st = {x["status"] for x in items}
    assert {"done", "error", "pending", "blocked"} <= st       # 三态+
    # 类型过滤
    r = await client.get("/api/activity", params={"kind": "approval"})
    assert all(x["kind"] == "approval" for x in r.json()["items"])
    ap = r.json()["items"][0]
    assert ap["status"] == "pending" and ap["ref"]["approval_id"] > 0
    # 状态过滤（被拦截待审批）
    r = await client.get("/api/activity", params={"status": "pending"})
    assert all(x["status"] == "pending" for x in r.json()["items"])
    # 会话过滤
    r = await client.get("/api/activity", params={"sid": sid})
    assert r.json()["items"] and all(x["sid"] == sid for x in r.json()["items"])
    # 工具动作的 error 态（is_error → error）
    r = await client.get("/api/activity", params={"kind": "file"})
    assert r.json()["items"][0]["status"] == "error"
    # 拦截项（status=blocked）标题带原因，类型按工具归 net
    r = await client.get("/api/activity", params={"status": "blocked"})
    blk = r.json()["items"]
    assert blk and "egress 拒绝" in blk[0]["title"] and blk[0]["kind"] == "net"


async def test_activity_pagination_and_bounds(client):
    await _seed(client)
    r = await client.get("/api/activity", params={"limit": 2, "offset": 0})
    d = r.json()
    assert len(d["items"]) == 2 and d["has_more"] is True
    r2 = await client.get("/api/activity", params={"limit": 2, "offset": 2})
    assert not (set(x["title"] for x in r2.json()["items"])
                & set(x["title"] for x in d["items"]))          # 不重叠
    # 上限收口（limit>200 → 200；不炸）
    r = await client.get("/api/activity", params={"limit": 99999})
    assert r.status_code == 200 and r.json()["limit"] == 200


async def test_activity_denied_approval_state(client):
    """审批裁决后台账状态迁移：pending → denied（denied 也是三态之一）。"""
    from loadn_webui.security import approve as approve_mod
    sid = await _seed(client)
    with approve_mod._conn() as _c:   # 直接造一条已裁决记录（decide 需确认码链路）
        _c.execute(
            "INSERT INTO approvals(sid, action_type, summary, params_json,"
            " params_hash, status, created_at, decided_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (sid, "bash", "已否决动作", "{}", "deadbeef", "denied",
             "2026-10-06T10:00:00", "2026-10-06T10:01:00"))
        _c.commit()
    r = await client.get("/api/activity", params={"kind": "approval",
                                                  "status": "denied"})
    items = r.json()["items"]
    assert items and items[0]["status"] == "denied"
