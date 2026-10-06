"""P8 动作台账：工具动作 / 审批 / 拦截 三源聚合（只读，token 面）。

「它做了什么、做成了什么」的一屏视图：assistant 消息 blocks（bash/文件/
网络分类 + is_error）、approvals 全态（pending=被拦截待审批，带「去审批」
aid）、审计 tool_call_blocked（拦截面）。性能纪律：工具源只取最近 400 行
消息窗口（有界——禁 >10k 全表扫），审批/审计各 ≤200/500 行；过滤与分页
在聚合后内存完成。
"""
from __future__ import annotations

import json

from fastapi import APIRouter

from ... import db as db_mod

router = APIRouter(prefix="/api")

# 工具名 → 台账类型（卡片分类）
_KIND_FILE = {"Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Task"}
_KIND_NET = {"WebSearch", "WebFetch"}
_TOOL_WINDOW = 400          # 工具源消息行窗口（有界）


def _classify(name: str) -> str:
    n = (name or "").split("__")[-1]
    if n == "Bash" or n.startswith("bash"):
        return "bash"
    if n in _KIND_FILE:
        return "file"
    if n in _KIND_NET or n.startswith(("browser", "screenshot", "click",
                                       "navigate", "fetch", "http", "curl")):
        return "net"
    return "tool"


@router.get("/activity")
def activity(sid: str = "", kind: str = "", status: str = "",
             limit: int = 50, offset: int = 0):
    """聚合动作台账。过滤：sid/kind(bash|file|net|tool|approval|blocked)/
    status(done|error|pending|denied|blocked)；分页 limit≤200。"""
    items: list[dict] = []
    # ① 工具动作（messages blocks——最近 N 行有界窗口）
    q = ("SELECT session_id, created_at, blocks_json FROM messages "
         "WHERE role='assistant' AND blocks_json IS NOT NULL "
         "AND blocks_json != '[]'")
    args: list = []
    if sid:
        q += " AND session_id=?"
        args.append(sid)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(_TOOL_WINDOW)
    with db_mod.conn() as c:
        rows = c.execute(q, args).fetchall()
    for r in rows:
        try:
            blocks = json.loads(r["blocks_json"] or "[]")
        except ValueError:
            continue
        for b in blocks:
            if not isinstance(b, dict) or not b.get("name"):
                continue
            items.append({
                "ts": r["created_at"], "sid": r["session_id"],
                "kind": _classify(str(b["name"])), "source": "tool",
                "title": f"{b['name']}：{(b.get('brief') or '')[:80]}",
                "status": "error" if b.get("is_error") else "done",
                "duration_s": None, "ref": None})
    # ② 审批（全态；pending=被拦截待审批）
    from ...security import approve as approve_mod
    for a in approve_mod.list_items(sid=sid or None, limit=500):
        st = {"pending": "pending", "approved": "done",
              "denied": "denied"}.get(a["status"], "done")
        items.append({
            "ts": a["created_at"], "sid": a["sid"], "kind": "approval",
            "source": "approval", "title": f"审批：{a['summary'][:80]}",
            "status": st,
            "duration_s": (a["decided_at"] and a["created_at"] and None),
            "ref": {"approval_id": a["id"]}})
    # ③ 拦截（审计 tool_call_blocked）
    from ...security import audit as audit_mod
    for row in audit_mod.tail(200, "tool_call_blocked"):
        try:
            d = json.loads(row["detail_json"] or "{}")
        except ValueError:
            d = {}
        tool = str(d.get("tool") or d.get("name") or "?")
        items.append({
            "ts": row["ts"], "sid": row["sid"] or "",
            "kind": _classify(tool) if tool != "?" else "tool",
            "source": "blocked", "title": f"拦截：{tool}（"
                      f"{str(d.get('reason') or '')[:60]}）",
            "status": "blocked", "duration_s": None, "ref": None})
    # 过滤 + 排序 + 分页
    if kind:
        items = [x for x in items if x["kind"] == kind]
    if status:
        items = [x for x in items if x["status"] == status]
    items.sort(key=lambda x: str(x["ts"] or ""), reverse=True)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    page = items[offset:offset + limit]
    return {"items": page, "offset": offset, "limit": limit,
            "has_more": len(items) > offset + limit}
