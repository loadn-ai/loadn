"""审批面：决策/消费确认码/详情。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ...engine import ENGINE

router = APIRouter(prefix="/api")

@router.post("/approvals/{aid}/decide")
def decide_approval(aid: int, body: dict):
    """用户裁决（token 面）：{approve: bool}。批准响应携带一次性明文码。"""
    from ... import approve as approve_mod
    try:
        out = approve_mod.decide(aid, bool(body.get("approve")))
    except LookupError as e:
        raise HTTPException(404, str(e))
    if out.get("ok"):
        st = approve_mod.status(aid)
        ENGINE.publish(st["sid"], "approval",
                       {"kind": "decided", "id": aid, "status": out["status"]})
    return out
@router.get("/approvals/{aid}")
def approval_status(aid: int):
    from ... import approve as approve_mod
    try:
        return approve_mod.status(aid)
    except LookupError as e:
        raise HTTPException(404, str(e))
@router.post("/approvals/consume")
def consume_approval(body: dict):
    """CLI 执行前验证（token 面）：{sid, action_type, params, confirm_code}。"""
    from ... import approve as approve_mod
    return approve_mod.consume(
        str(body.get("sid") or ""), str(body.get("action_type") or ""),
        dict(body.get("params") or {}), str(body.get("confirm_code") or ""))
