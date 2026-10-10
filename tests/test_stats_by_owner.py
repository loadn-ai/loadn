"""成本按属主聚合（BC-2 / AC-4.3c backlog）：by_owner 维度对赌。

焦点：①turns 按 sessions.owner_id→users.username 归桶；②无主会话
（token 通道/legacy，owner_id NULL）归「(无主)」不丢数；③totals.turns
== by_owner 各桶 turns 之和（全覆盖对赌）。
"""
from __future__ import annotations

from tests.conftest import wait_turn


async def _mk_turn(client, ws_root, title: str) -> None:
    resp = await client.post("/api/sessions", json={"title": title,
                                                    "first_message": "你好"})
    sid = resp.json()["session"]["id"]
    tid = (await client.get(f"/api/sessions/{sid}")).json()["turns"][0]["id"]
    t = await wait_turn(client, sid, tid)
    assert t["status"] == "done", t


async def test_by_owner_buckets_and_full_cover(client, ws_root):
    """>>> token 通道建会话（无主）→ by_owner 必有「(无主)」桶且全覆盖。"""
    await _mk_turn(client, ws_root, "by_owner-无主")
    d = (await client.get("/api/stats/cost")).json()
    assert "by_owner" in d, "响应必须带 by_owner（AC-4.3c）"
    owners = {o["owner"]: o for o in d["by_owner"]}
    assert "(无主)" in owners, "token/legacy 会话须归「(无主)」桶"
    # 全覆盖对赌：各桶 turns 之和 == 总 turns
    assert sum(o["turns"] for o in d["by_owner"]) == d["totals"]["turns"], \
        "by_owner 必须覆盖全部 turns（含无主）"
    no_owner = owners["(无主)"]
    assert no_owner["turns"] >= 1
    for k in ("tokens", "cost_cli_usd", "cost_api_usd"):
        assert k in no_owner


async def test_by_owner_named_user_bucket(client, ws_root, monkeypatch):
    """>>> cookie 用户会话归 username 桶（owner_id→users.username join 正确）。"""
    from loadn_webui import db as db_mod

    # 直接在库内造一个有主会话 + turn（绕开完整登录流——owner 归属是本测焦点）
    with db_mod.conn() as c:
        c.execute("INSERT OR IGNORE INTO users(username, password_hash, role, created_at)"
                  " VALUES('byowner-alice', 'x', 'user', '2026-01-01T00:00:00')")
        uid = c.execute("SELECT id FROM users WHERE username='byowner-alice'"
                        ).fetchone()["id"]
        sid = "sess-byowner-test-1"
        c.execute(
            "INSERT INTO sessions(id, title, profile, created_at, updated_at, owner_id)"
            " VALUES(?, 'by_owner 归属测', 'default', '2026-01-01T00:00:00',"
            " '2026-01-01T00:00:00', ?)", (sid, uid))
        c.execute(
            "INSERT INTO turns(session_id, status, started_at, finished_at, cost_usd)"
            " VALUES(?, 'done', '2026-01-01T00:00:00', '2026-01-01T00:00:01', 0.01)",
            (sid,))

    d = (await client.get("/api/stats/cost")).json()
    owners = {o["owner"]: o for o in d["by_owner"]}
    assert "byowner-alice" in owners, "有主会话须归 username 桶"
    assert owners["byowner-alice"]["turns"] >= 1
    # 覆盖对赌仍成立（无主桶 + 具名桶 == 总量）
    assert sum(o["turns"] for o in d["by_owner"]) == d["totals"]["turns"]
