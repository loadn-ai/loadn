"""资源探测历史入库（AC-5.10f / 产品方案 P2-6）。

对赌焦点：
① POST /admin/resources/test 结果落 ping_history（未知目标不走网络即返回
   ok=False——用它做无网对赌）；
② GET /admin/resources/history 聚合正确：last 取每目标最新一条、
   fails_24h 只计 24h 窗口内失败；
③ 敏感读分级：普通 cookie 用户读 history 403（前缀 /api/admin/resources
   既有覆盖，防新端点破口）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loadn_webui import db as db_mod
from loadn_webui.util import iso


async def test_ping_test_writes_history(client):
    """>>> 探测即落库：未知目标（无网络请求路径）ok=0 也入历史。"""
    r = await client.post("/api/admin/resources/test",
                          json={"only": ["__no_such_target__"]})
    assert r.status_code == 200
    body = r.json()
    assert body["results"]["__no_such_target__"]["ok"] is False
    with db_mod.conn() as c:
        rows = c.execute(
            "SELECT target, ok, msg FROM ping_history "
            "WHERE target = '__no_such_target__'").fetchall()
    assert len(rows) == 1, "探测结果必须落 ping_history"
    assert rows[0]["ok"] == 0
    assert "未知资源" in rows[0]["msg"]


def _seed(rows: list[tuple[str, int, int | None, str]]) -> None:
    with db_mod.conn() as c:
        c.execute("DELETE FROM ping_history")
        c.executemany(
            "INSERT INTO ping_history(target, ok, ms, msg, ts) VALUES (?,?,?,?,?)",
            rows)


async def test_history_aggregation_last_and_fails(client):
    """>>> last=每目标最新一条；fails_24h 只计 24h 窗口内失败。"""
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=30)).isoformat()
    _seed([
        ("sms", 0, None, "timeout", old),          # 窗口外失败：不计 fails_24h
        ("sms", 1, 120, "ok", iso()),              # 最新一条 ok
        ("mail", 1, 80, "ok", old),                # 旧成功
        ("mail", 0, None, "conn refused", iso()),  # 最新一条失败且在窗口内
    ])
    d = (await client.get("/api/admin/resources/history")).json()
    items = {it["target"]: it for it in d["items"]}
    assert items["sms"]["last"]["ok"] is True, "last 必须取 id 最大（最新）一条"
    assert items["sms"]["last"]["ms"] == 120
    assert items["sms"]["fails_24h"] == 0, "30h 前的失败不计入 24h 窗口"
    assert items["mail"]["last"]["ok"] is False
    assert items["mail"]["fails_24h"] == 1
    assert "ts" in items["sms"]["last"], "卡上显示最近时间需要 ts"


async def test_history_plain_user_403(client, monkeypatch):
    """>>> 敏感读分级：普通 cookie 用户读 history 403（防新端点破口）。"""
    from loadn_webui.security import userauth as ua
    monkeypatch.setattr(ua, "session_user",
                        lambda _c: {"id": 7, "role": "user", "username": "u"})
    r = await client.get("/api/admin/resources/history")
    assert r.status_code == 403, "history 在 /api/admin/resources 前缀覆盖内"
