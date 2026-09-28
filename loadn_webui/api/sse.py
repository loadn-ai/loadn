"""SSE 端点：订阅 engine hub，Last-Event-ID 断线补发。

协议：`id: <session_events 自增id>` + `event: <type>` + `data: <json>`；
25s 无事件发 `: ping` 保活。超出保留窗口 → `resync` 整包（前端整体重置）。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse

from .. import db as db_mod
from .. import workspace as ws_mod
from ..config import CONFIG
from ..engine import ENGINE

PING_S = 25


def _fmt(eid: int, type_: str, data: dict) -> str:
    return f"id: {eid}\nevent: {type_}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _resync_snapshot(sid: str) -> dict:
    """整包快照：DB messages + turns + todos + 最近文件（transcript 兜底）。"""
    from .. import transcript as ts
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        msgs = [db_mod.to_dict(r) for r in db_mod.list_messages(c, sid)]
        turns = [db_mod.to_dict(r) for r in c.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY id", (sid,)).fetchall()]
    if sess is None:
        return {"session": None}
    todos = ts.latest_todos(sess["claude_session_id"]) if sess["session_fresh"] == 0 else None
    try:
        files = ts.recent_ws_files(ws_mod.ws_of(sid))
    except OSError:
        files = []
    return {"session": db_mod.to_dict(sess), "messages": msgs, "turns": turns,
            "todos": todos, "recent_files": files}


async def event_stream(sid: str, request: Request):
    last_id = 0
    lei = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
    if lei:
        try:
            last_id = int(lei)
        except ValueError:
            last_id = 0

    q = ENGINE.subscribe(sid)   # 先订阅（缓冲 live 事件），再回放 DB——无缝衔接

    def _replay_rows() -> list:
        """回放行集。断线补发（Last-Event-ID）照旧全量补；fresh connect 走
        精准回放——前端 live 只挂活跃 turn（旧 turn 的回放事件按 turnId 全被
        丢弃，是纯废流量），故只回放活跃 turn 尾部 replay_max_events 条，
        无活跃 turn 零回放（终态历史早已落在 messages/blocks_json）。"""
        with db_mod.conn() as c:
            if last_id > 0:
                return db_mod.events_after(c, sid, last_id)
            # 优先 running：串行队列里有排队的后续消息时（最新的是 queued），
            # 回放应给真正在跑的 turn，否则重进会话看不到运行中的流水
            row = c.execute(
                "SELECT id FROM turns WHERE session_id=? AND status='running' "
                "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if row is None:
                row = c.execute(
                    "SELECT id FROM turns WHERE session_id=? AND status IN ('running','queued') "
                    "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
            if not row:
                return []
            return db_mod.recent_events_for_turn(
                c, sid, row["id"], CONFIG.run.replay_max_events)

    async def gen():
        try:
            # 1) 回放（fresh connect 精准：仅活跃 turn 尾窗）
            replayed = 0
            rows = _replay_rows()
            for r in rows:
                eid = r["id"]
                if eid <= last_id:
                    continue
                try:
                    data = json.loads(r["data_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                yield _fmt(eid, r["type"], data)
                replayed = eid
            # 2) 断线补发不完整（该补的没补到）→ 前端整体重置
            if last_id > 0:
                with db_mod.conn() as c:
                    row = c.execute("SELECT MAX(id) m FROM session_events WHERE session_id=?",
                                    (sid,)).fetchone()
                max_id = row["m"] or 0
                if max_id > last_id and replayed < max_id:
                    yield _fmt(max_id + 1, "resync", _resync_snapshot(sid))
            # 3) live 流
            while True:
                if await request.is_disconnected():
                    return
                try:
                    eid, type_, data = await asyncio.wait_for(q.get(), timeout=PING_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if eid <= last_id:
                    continue
                yield _fmt(eid, type_, data)
        finally:
            ENGINE.unsubscribe(sid, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
