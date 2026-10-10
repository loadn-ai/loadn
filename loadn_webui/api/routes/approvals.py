"""审批面：决策/消费确认码/详情。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ...engine import ENGINE

router = APIRouter(prefix="/api")

@router.post("/approvals/{aid}/decide")
def decide_approval(aid: int, body: dict):
    """用户裁决（token 面）：{approve: bool}。批准响应携带一次性明文码。

    AC-4.2：admin 角色可裁决全平台待审（管理面「驳回」止损入口——批准
    语义不变仍走会话内确认码）；decided_by 记 'admin' 区分审计。"""
    from ...security import approve as approve_mod
    from ...security import userauth as _ua
    # 多用户批2：裁决权=会话属主（越权=404 不暴露存在性；token/宽限通道
    # user=None 放行——CLI 与渠道线程兼容）
    _u = _ua.current_user()
    _is_admin = _u is not None and _u.get("role") == "admin"
    if _u is not None and not _is_admin:
        try:
            _sid = approve_mod.status(aid)["sid"]
        except LookupError as e:
            raise HTTPException(404, str(e))
        with db_mod.conn() as _c:
            _sess = db_mod.get_session(_c, _sid)
        if _sess is None or not _ua.owner_ok(_sess, _u):
            raise HTTPException(404, f"approval not found: {aid}")
    try:
        out = approve_mod.decide(aid, bool(body.get("approve")),
                                 by="admin" if _is_admin else "user")
    except LookupError as e:
        raise HTTPException(404, str(e))
    if out.get("ok"):
        st = approve_mod.status(aid)
        ENGINE.publish(st["sid"], "approval",
                       {"kind": "decided", "id": aid, "status": out["status"]})
    return out
@router.get("/approvals/{aid}")
def approval_status(aid: int):
    from ...security import approve as approve_mod
    try:
        return approve_mod.status(aid)
    except LookupError as e:
        raise HTTPException(404, str(e))
@router.post("/approvals/consume")
def consume_approval(body: dict):
    """CLI 执行前验证（token 面）：{sid, action_type, params, confirm_code}。"""
    from ...security import approve as approve_mod
    return approve_mod.consume(
        str(body.get("sid") or ""), str(body.get("action_type") or ""),
        dict(body.get("params") or {}), str(body.get("confirm_code") or ""))
