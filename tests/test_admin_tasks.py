"""任务管理页数据面 GET /api/admin/tasks：聚合指标（轮数/消息/产物/token/
费用/体积）+ 筛选（状态/项目/分类/引擎/搜索）+ 排序 + 属主过滤。"""
from __future__ import annotations

import json

from loadn_webui import db as db_mod


async def _mk(client, title: str, **body) -> str:
    r = await client.post("/api/sessions", json={"title": title, **body})
    assert r.status_code == 200, r.text
    return r.json()["session"]["id"]


async def test_tasks_aggregates_and_filters(client, ws_root):
    s1 = await _mk(client, "调研任务甲")
    s2 = await _mk(client, "闲聊任务乙")
    # s1 造数据：2 turns / 3 messages / 1 artifact + usage
    with db_mod.conn() as c:
        t1 = db_mod.create_turn(c, session_id=s1, status="done")
        db_mod.create_turn(c, session_id=s1, status="done")
        c.execute("INSERT INTO messages(session_id,turn_id,role,content,created_at)"
                  " VALUES(?,'user','u','x',datetime('now'))", (s1,))
        c.execute("INSERT INTO messages(session_id,turn_id,role,content,created_at)"
                  " VALUES(?,'assistant','a','y',datetime('now'))", (s1,))
        (ws_root / s1 / "artifacts").mkdir(parents=True, exist_ok=True)
        (ws_root / s1 / "artifacts" / "r.md").write_text("x" * 1000)
        db_mod.upsert_artifact(c, session_id=s1, path="artifacts/r.md",
                               kind="md", title="r", size=1000, mtime=1.0)
        db_mod.update_session(c, s1,
                              usage_json=json.dumps({"total": 123, "total_all": 456}),
                              cost_usd=0.5, engine="loadn")
        t_running = db_mod.create_turn(c, session_id=s2, status="running")
        c.execute("INSERT INTO messages(session_id,turn_id,role,content,created_at)"
                  " VALUES(?,'user','u','z',datetime('now'))", (s2,))

    d = (await client.get("/api/admin/tasks")).json()
    by_id = {t["id"]: t for t in d["tasks"]}
    a = by_id[s1]
    assert a["n_turns"] == 2 and a["n_messages"] == 2 and a["n_artifacts"] == 1
    assert a["tokens"] == 456                     # total_all 优先于 total
    assert a["cost_usd"] == 0.5
    assert a["size_bytes"] >= 1000 and a["engine"] == "loadn"
    assert not a["running"]
    assert by_id[s2]["running"] and by_id[s2]["n_turns"] == 1

    # 筛选：engine / 搜索 / 状态（全量套件共享库——断言按本用例 id 收敛）
    d = (await client.get("/api/admin/tasks?engine=loadn")).json()
    ids = {t["id"] for t in d["tasks"]}
    assert s1 in ids and s2 not in ids
    d = (await client.get("/api/admin/tasks?q=闲聊")).json()
    assert {t["id"] for t in d["tasks"]} == {s2}
    await client.delete(f"/api/sessions/{s1}")
    d = (await client.get("/api/admin/tasks?status=archived&q=甲")).json()
    assert s1 in {t["id"] for t in d["tasks"]}
    d = (await client.get("/api/admin/tasks?status=active")).json()
    assert s1 not in [t["id"] for t in d["tasks"]]

    # 排序：tokens asc → s2（0 token）在前
    d = (await client.get("/api/admin/tasks?sort=tokens&dir=asc")).json()
    ids = [t["id"] for t in d["tasks"]]
    assert ids.index(s2) < ids.index(s1)   # noqa: SIM300

    # 分类筛选 + 移动标记位
    cid = (await client.post("/api/categories",
                             json={"name": "批管理"})).json()["category"]["id"]
    await client.patch(f"/api/sessions/{s2}", json={"category_id": cid})
    d = (await client.get(f"/api/admin/tasks?category_id={cid}")).json()
    got = {t["id"] for t in d["tasks"]}
    assert s2 in got
    assert next(t for t in d["tasks"] if t["id"] == s2)["category_id"] == cid

    # running 行在归档前也可见；收尾清理运行行（防串扰其他用例）
    with db_mod.conn() as c:
        db_mod.update_turn(c, t_running, status="done")


async def test_owner_scope_for_non_admin(client, monkeypatch):
    """>>> 属主对赌：非 admin 只看自己的 + legacy 无主行。"""
    s_mine = await _mk(client, "我的")
    s_other = await _mk(client, "别人的")

    class _FakeUser(dict):
        pass

    from loadn_webui.security import userauth as ua
    monkeypatch.setattr(ua, "current_user",
                        lambda: {"id": 7, "role": "user", "username": "u"})
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET owner_id=7 WHERE id=?", (s_mine,))
        c.execute("UPDATE sessions SET owner_id=99 WHERE id=?", (s_other,))
    d = (await client.get("/api/admin/tasks")).json()
    ids = {t["id"] for t in d["tasks"]}
    assert s_mine in ids and s_other not in ids
