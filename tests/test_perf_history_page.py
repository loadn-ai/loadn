"""PERF-4：历史消息分页拉取——详情尾窗 / messages_earlier 计数 / 翻页
衔接（顺序正确、无重复无遗漏）/ 到底判定 / limit 钳制 / 属主门 /
sse resync 快照同口径窗口。

首屏只拉最近 100 条是本卡的验收锚点：对赌「窗口大小恰 100（不是 99/
101）」与「翻页与尾窗严格衔接（无重复/无跳号）」两条不变量——任何
off-by-one / 排序反转都会被杀死。零 token。
"""
from __future__ import annotations

import json

import httpx
import pytest

from loadn_webui import db as db_mod

from .test_perf_sessions import _mk_auth_sess, _mk_session, _mk_user

pytestmark = [pytest.mark.coverage("perf.history_page")]

PAGE = 100   # 写死页大小对赌（不引用 MSG_PAGE——引用被测常量=变异同步漂移）


def _mk_messages(sid: str, n: int) -> list[int]:
    """插 n 条消息（user/assistant 交替），返回实际自增 id 列表（升序）。"""
    ids: list[int] = []
    with db_mod.conn() as c:
        for i in range(n):
            cur = c.execute(
                "INSERT INTO messages(session_id, role, content,"
                " blocks_json, created_at) VALUES(?,?,?,?,?)",
                (sid, "user" if i % 2 == 0 else "assistant", f"msg-{i}",
                 None if i % 2 == 0 else json.dumps(
                     [{"name": "Bash", "brief": f"#{i}"}]),
                 f"2026-09-{(i % 28) + 1:02d}T00:00:00+00:00"))
            ids.append(cur.lastrowid)
    return ids


async def _drain(sid: str, first_page: list[dict], first_earlier: int,
                 client) -> tuple[list[dict], int]:
    """从首页尾窗出发沿「加载更多」翻到底，聚合全部消息（模拟前端行为）。"""
    loaded = list(first_page)
    earlier = first_earlier
    for _ in range(50):                       # 防御性上限（漏了 earlier 会死循环）
        if earlier <= 0:
            break
        cursor = min(m["id"] for m in loaded)
        d = (await client.get(
            f"/api/sessions/{sid}/messages/page?before_id={cursor}")).json()
        loaded = d["messages"] + loaded
        earlier = d["earlier"]
    return loaded, earlier


# ---------------------------------------------------------------- ① 尾窗+计数
async def test_detail_tail_window_and_earlier_count(client):
    """250 条会话：详情只回最新 100 条（升序、恰为尾段）+ earlier=150。"""
    sid = "20261020_hp-tail"
    _mk_session(sid, "2026-10-01T00:00:00+00:00")
    ids = _mk_messages(sid, 250)
    d = (await client.get(f"/api/sessions/{sid}")).json()
    got = [m["id"] for m in d["messages"]]
    assert len(got) == PAGE, f"首屏必须只铺 {PAGE} 条（实际 {len(got)}）"
    assert got == ids[-PAGE:], "尾窗必须是最新 100 条且 id 升序"
    assert [m["content"] for m in d["messages"]] == [
        f"msg-{i}" for i in range(150, 250)], "内容与位置对应（无错位）"
    assert d["messages_earlier"] == 150


async def test_detail_small_session_no_earlier(client):
    """不足一页的会话：全量返回、earlier=0（小会话零行为变化）。"""
    sid = "20261020_hp-small"
    _mk_session(sid, "2026-10-01T00:00:00+00:00")
    ids = _mk_messages(sid, 7)
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert [m["id"] for m in d["messages"]] == ids
    assert d["messages_earlier"] == 0
    # 空会话：messages=[] 且 earlier=0（不是缺字段）
    sid2 = "20261020_hp-empty"
    _mk_session(sid2, "2026-10-01T00:00:00+00:00")
    d2 = (await client.get(f"/api/sessions/{sid2}")).json()
    assert d2["messages"] == [] and d2["messages_earlier"] == 0


# ---------------------------------------------------------------- ② 翻页衔接对赌
async def test_page_walk_no_dup_no_gap(client):
    """翻页到底聚合：全量 250 条与库内逐条一致——顺序正确、无重复、无遗漏。"""
    sid = "20261020_hp-walk"
    _mk_session(sid, "2026-10-01T00:00:00+00:00")
    ids = _mk_messages(sid, 250)
    d = (await client.get(f"/api/sessions/{sid}")).json()
    loaded, earlier = await _drain(sid, d["messages"], d["messages_earlier"], client)
    assert earlier == 0, "翻到底 earlier 必须归零"
    assert [m["id"] for m in loaded] == ids, "聚合=库内全量（无重复/无遗漏/升序）"
    assert len({m["id"] for m in loaded}) == 250, "不得出现重复 id"


async def test_page_walk_exact_boundary(client):
    """页边界恰好整除（200=100+100）与多一条（201）：都无重叠无缝隙。"""
    for n, sid in ((2 * PAGE, "20261020_hp-b200"), (2 * PAGE + 1, "20261020_hp-b201")):
        _mk_session(sid, "2026-10-01T00:00:00+00:00")
        ids = _mk_messages(sid, n)
        d = (await client.get(f"/api/sessions/{sid}")).json()
        loaded, _ = await _drain(sid, d["messages"], d["messages_earlier"], client)
        assert [m["id"] for m in loaded] == ids, f"n={n} 翻页聚合必须无缺失"


# ---------------------------------------------------------------- ③ 翻页端点语义
async def test_page_endpoint_semantics(client):
    """before_id 游标语义：页内全部 id<before_id、升序、earlier 递减到 0；
    beyond 头部（before_id 比最老消息还小）→ 空页 earlier=0。"""
    sid = "20261020_hp-sem"
    _mk_session(sid, "2026-10-01T00:00:00+00:00")
    ids = _mk_messages(sid, 130)
    d = (await client.get(f"/api/sessions/{sid}")).json()
    oldest = d["messages"][0]["id"]
    p1 = (await client.get(
        f"/api/sessions/{sid}/messages/page?before_id={oldest}")).json()
    assert len(p1["messages"]) == 30
    assert [m["id"] for m in p1["messages"]] == ids[:30]
    assert p1["earlier"] == 0
    # 越过头部的游标：空页不炸
    p0 = (await client.get(
        f"/api/sessions/{sid}/messages/page?before_id={ids[0] - 1}")).json()
    assert p0["messages"] == [] and p0["earlier"] == 0
    # limit 钳制：负数/0 → 1 条；>500 → 500（不信任客户端入参）
    p_lim = (await client.get(
        f"/api/sessions/{sid}/messages/page?before_id={oldest}&limit=0")).json()
    assert len(p_lim["messages"]) == 1
    r_bad = await client.get(f"/api/sessions/{sid}/messages/page?before_id=abc")
    assert r_bad.status_code == 422, "before_id 非整数必须 422 拒绝"
    # assistant 块折叠口径与详情一致（blocks_json 可解析且工具块在）
    am = next(m for m in p1["messages"] if m["role"] == "assistant")
    assert json.loads(am["blocks_json"])[0]["name"] == "Bash"


async def test_page_owner_gate(server_url):
    """否定路径：他人会话翻页=404（不暴露存在性，与详情/归档回源同门）。"""
    sid = "20261020_hp-own"
    _mk_session(sid, "2026-10-01T00:00:00+00:00", owner_id=None)
    _mk_messages(sid, 120)
    with db_mod.conn() as c:
        uid = _mk_user(c, "hp-userb")
        asid = _mk_auth_sess(c, uid, 25 * 86400)
    async with httpx.AsyncClient(
            base_url=server_url, timeout=10,
            cookies={"loadn_session": asid}) as ub:
        r = await ub.get(f"/api/sessions/{sid}/messages/page?before_id=1")
        assert r.status_code == 404, "他人会话分页回源必须 404"


# ---------------------------------------------------------------- ④ sse 快照同口径
async def test_resync_snapshot_windowed(client):
    """删/改消息触发的 resync 快照：messages 同为尾窗 + messages_earlier。"""
    from loadn_webui.api.sse import _resync_snapshot
    sid = "20261020_hp-sse"
    _mk_session(sid, "2026-10-01T00:00:00+00:00")
    ids = _mk_messages(sid, 130)
    snap = _resync_snapshot(sid)
    assert [m["id"] for m in snap["messages"]] == ids[-PAGE:]
    assert snap["messages_earlier"] == 30
    # 小会话快照行为不变（messages_earlier=0）
    sid2 = "20261020_hp-sse2"
    _mk_session(sid2, "2026-10-01T00:00:00+00:00")
    ids2 = _mk_messages(sid2, 5)
    snap2 = _resync_snapshot(sid2)
    assert [m["id"] for m in snap2["messages"]] == ids2
    assert snap2["messages_earlier"] == 0


# ---------------------------------------------------------------- ⑤ db 层单元
def test_db_page_queries():
    """db 层三函数：尾窗/翻页/计数的边界（0 条、超量 limit、跨会话隔离）。"""
    sid = "20261020_hp-db"
    _mk_session(sid, "2026-10-01T00:00:00+00:00")
    sid_other = "20261020_hp-db2"
    _mk_session(sid_other, "2026-10-01T00:00:00+00:00")
    ids = _mk_messages(sid, 10)
    _mk_messages(sid_other, 3)            # 邻居会话不得混入
    with db_mod.conn() as c:
        assert [r["id"] for r in db_mod.list_messages_tail(c, sid, 4)] == ids[-4:]
        assert [r["id"] for r in db_mod.list_messages_tail(c, sid, 99)] == ids
        assert [r["id"] for r in db_mod.list_messages_tail(c, sid, 0)] == []
        assert [r["id"] for r in db_mod.list_messages_before(
            c, sid, ids[3], 2)] == ids[1:3]
        assert db_mod.list_messages_before(c, sid, ids[0], 5) == []
        assert db_mod.count_messages_before(c, sid, ids[3]) == 3
        assert db_mod.count_messages_before(c, sid, ids[0]) == 0
