"""workspace 占用落库与后台重算（BD-3 / AC-4.3c backlog 收尾）。

对赌焦点：
① _workspace_size 算完落 sessions.size_bytes/size_checked_at（重启不蒸发）；
② POST /admin/tasks/size-rescan 触发即返回 + running 中重复 409 + 线程
   收敛后全会话 size_bytes 落库；
③ sort=size 下推 SQL——分页模式下全局序正确（跨页排序不再只是页内重排）。
"""
from __future__ import annotations

import asyncio

from loadn_webui import db as db_mod
from loadn_webui.api.routes.admin import _SIZE_CACHE


async def _mk_session(client, title: str) -> str:
    resp = await client.post("/api/sessions", json={"title": title,
                                                    "first_message": "你好"})
    return resp.json()["session"]["id"]


async def _wait_rescan_idle(timeout_s: float = 10) -> None:
    from loadn_webui.api.routes.admin import _SIZE_RESCAN
    for _ in range(int(timeout_s / 0.05)):
        if not _SIZE_RESCAN["running"]:
            return
        await asyncio.sleep(0.05)


async def test_size_cached_in_db(client, ws_root):
    """>>> 惰性算落库：tasks_overview 摸过的会话 size_bytes/size_checked_at 入库。"""
    sid = await _mk_session(client, "占用落库")
    _SIZE_CACHE.pop(sid, None)          # 清内存缓存强制重算
    d = (await client.get("/api/admin/tasks")).json()
    row = next(t for t in d["tasks"] if t["id"] == sid)
    assert row["size_bytes"] is not None and row["size_bytes"] >= 0
    with db_mod.conn() as c:
        r = c.execute("SELECT size_bytes, size_checked_at FROM sessions"
                      " WHERE id=?", (sid,)).fetchone()
    assert r is not None and r["size_bytes"] is not None, "必须落库（重启不蒸发）"
    assert r["size_checked_at"], "统计时刻必须落库"


async def test_rescan_background_and_409(client, ws_root):
    """>>> 触发即返回 + running 中 409 + 线程收敛后全会话落库。"""
    sids = [await _mk_session(client, f"重算-{i}") for i in range(3)]
    r = await client.post("/api/admin/tasks/size-rescan")
    assert r.status_code == 200 and r.json()["started"] is True
    r2 = await client.post("/api/admin/tasks/size-rescan")
    assert r2.status_code == 409, "running 中重复触发必须 409"
    await _wait_rescan_idle()
    with db_mod.conn() as c:
        rows = c.execute("SELECT id, size_bytes FROM sessions"
                         " WHERE id IN (?,?,?)", sids).fetchall()
    assert all(x["size_bytes"] is not None for x in rows), "全会话落库（含未翻页的）"


async def test_sort_size_global_order(client, ws_root):
    """>>> sort=size 走 SQL：分页模式下跨页全局序（两会话不同占用，
    limit=1 首页必须是较大者——旧实现取 updated 首页再页内重排，跨页即错）。"""
    a, b = await _mk_session(client, "小占用"), await _mk_session(client, "大占用")
    # 直接落库两个悬殊缓存值（排序下推用列值，无需真实目录大小）
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET size_bytes=100 WHERE id=?", (a,))
        c.execute("UPDATE sessions SET size_bytes=9000000 WHERE id=?", (b,))
    d = (await client.get("/api/admin/tasks?sort=size&dir=desc&limit=1")).json()
    assert d["tasks"][0]["id"] == b, "sort=size 分页必须全局序（SQL 下推）"
    d2 = (await client.get("/api/admin/tasks?sort=size&dir=asc&limit=1")).json()
    assert d2["tasks"][0]["id"] == a
