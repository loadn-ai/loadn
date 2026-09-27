"""消息与轮次：发消息/插话/中止/编辑/提升快照/会话树。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import profile as profile_mod
from ... import workspace as ws_mod
from ...engine import ENGINE

router = APIRouter(prefix="/api")

from ._common import _get_session_or_404, _validate_attachments


@router.post("/sessions/{sid}/promote")
def promote_session(sid: str):
    """把现有任务升级为项目容器：原任务成为首个子任务，workspace 零迁移。

    projects.id 复用原 sid（目录名=pid=宪法 SESSION_ID 全一致）；settings env
    从 SESSION_ID 改写 PROJECT_ID（子任务的 per-session 值由 engine 恒注入）。
    之后项目行可「+ 子任务」（共享同一工作区）。
    """
    import json as _json
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        if sess is None:
            raise HTTPException(404, f"session not found: {sid}")
        if sess["project_id"]:
            raise HTTPException(400, "已是项目子任务，无需升级")
        db_mod.create_project(
            c, id=sid, title=sess["title"], workspace=sess["workspace"],
            profile=sess["profile"], skills_json=sess["skills_json"] or "[]",
            mcp_json=sess["mcp_json"] or "{}")
        db_mod.update_session(c, sid, project_id=sid)
    from ... import profile as _prof
    ws_mod.render_project_constitution(
        sid, sess["title"], _prof.get(sess["profile"]),
        _json.loads(sess["skills_json"] or "[]"))
    return {"ok": True, "project_id": sid}
@router.post("/sessions/{sid}/messages")
async def post_message(sid: str, body: dict):
    _get_session_or_404(sid)
    text = (body.get("text") or "").strip()
    atts = _validate_attachments(body.get("attachments"))
    if not text and not atts:
        raise HTTPException(400, "text 不能为空")
    mode = body.get("mode") if body.get("mode") in ("foreground", "background") else "foreground"
    # 全自动角色：首条真实消息重新匹配（零选择创建时探针只是占位标题），
    # 命中新角色则连同 skills 一起换成该角色默认（用户从未显式选过）
    with db_mod.conn() as c:
        if db_mod.kv_get(c, f"profile_auto:{sid}"):
            db_mod.kv_del(c, f"profile_auto:{sid}")
            prof = profile_mod.auto_match(text or "（见附件）")
            if prof and prof.name != db_mod.get_session(c, sid)["profile"]:
                db_mod.update_session(c, sid, profile=prof.name,
                                      skills_json=json.dumps(list(prof.skills),
                                                             ensure_ascii=False))
                ws_mod.rerender(sid, prof_name=prof.name, skills=list(prof.skills))
    try:
        tid = await ENGINE.submit(sid, text or "（见附件）", mode, attachments=atts)
    except PermissionError as e:      # W6.4 熔断（kill switch/canary 命中）
        raise HTTPException(403, str(e))
    with db_mod.conn() as c:
        turn = db_mod.to_dict(db_mod.get_turn(c, tid))
    return {"turn": turn}
@router.post("/turns/{tid}/stop")
async def stop_turn(tid: int):
    ok = await ENGINE.stop_turn(tid)
    if not ok:
        raise HTTPException(404, "turn 不存在或已结束")
    return {"ok": True}
@router.post("/sessions/{sid}/steer")
async def steer_session(sid: str, body: dict):
    """随时插话：运行中的 loadn turn 每轮 LLM 调用前轮询 .steer.jsonl，
    插话下一轮即注入主/子代理上下文（转向/中断实时生效，不等排队）。

    仅当该 session 有 running 的 loadn turn 时 steered=true；否则
    steered=false，前端回落正常发送（排队为新 turn）。插话落在最后一轮
    之后来不及注入的，engine._finish 按原话回队列（不丢话）。写文件/记账
    逻辑在 Engine.steer_if_running（scheduler.fire 复用同一通道）。
    """
    _get_session_or_404(sid)
    text = (body.get("text") or "").strip()[:4000]
    if not text:
        raise HTTPException(400, "text 不能为空")
    steered = ENGINE.steer_if_running(sid, text) is not None
    return {"ok": True, "steered": steered}
@router.delete("/messages/{mid}")
async def delete_message(mid: int):
    """删除单条气泡（只动展示层：messages 行 + 对应事件；transcript/turn
    记账不动——历史气泡删掉不等于 turn 没跑过）。"""
    with db_mod.conn() as c:
        row = c.execute("SELECT session_id FROM messages WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise HTTPException(404, "消息不存在")
        c.execute("DELETE FROM messages WHERE id=?", (mid,))
        sid = row["session_id"]
    ENGINE.publish(sid, "resync", {}, None)
    return {"ok": True}
@router.put("/messages/{mid}")
async def edit_message(mid: int, body: dict):
    """编辑单条气泡文本（展示层修正；不改 turn 历史——编辑后如需重跑
    请复制修改后的文本重新发送）。"""
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    with db_mod.conn() as c:
        row = c.execute("SELECT session_id FROM messages WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise HTTPException(404, "消息不存在")
        c.execute("UPDATE messages SET content=? WHERE id=?", (text, mid))
        sid = row["session_id"]
    ENGINE.publish(sid, "resync", {}, None)
    return {"ok": True}
@router.delete("/turns/{tid}")
async def retract_turn(tid: int):
    """撤回排队中的消息：turn + 消息 + SSE 事件一并删除，等同没发过。

    仅限 queued；运行中的先 stop。条件删除与引擎的 queued→running 原子占位
    互斥（engine._run_turn），队列里残留的 tid 不会被误执行也不会复活。
    """
    if tid in ENGINE.active:
        raise HTTPException(409, "turn 运行中，请先停止再撤回")
    with db_mod.conn() as c:
        turn = db_mod.get_turn(c, tid)
        if turn is None:
            raise HTTPException(404, "turn 不存在")
        cur = c.execute("DELETE FROM turns WHERE id=? AND status='queued'", (tid,))
        if cur.rowcount != 1:
            raise HTTPException(400, "仅排队中的消息可撤回（已开始/已结束的只能停止）")
        c.execute("DELETE FROM messages WHERE turn_id=?", (tid,))
        c.execute("DELETE FROM session_events WHERE turn_id=?", (tid,))
    ENGINE.publish(turn["session_id"], "turn_deleted", {"turn_id": tid}, tid)
    return {"ok": True}
