"""P10 目标策略管理面（admin）：列表/新建/改档/删除 + 审批窄化生成 +
SKILL targets 建议（仅展示）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ...security import target_policy as tp

router = APIRouter(prefix="/api/admin")


@router.get("/target-policy")
def list_policies():
    return {"policies": tp.list_all(),
            "suggestions": tp.skill_suggestions()}


@router.post("/target-policy")
def add_policy(body: dict):
    try:
        pid = tp.add(str(body.get("match") or ""), str(body.get("kind") or ""),
                     str(body.get("mode") or ""),
                     scope_note=str(body.get("scope_note") or ""),
                     created_from=str(body.get("created_from") or "manual"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "id": pid}


@router.patch("/target-policy/{pid}")
def patch_policy(pid: int, body: dict):
    try:
        tp.set_mode(pid, str(body.get("mode") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    return {"ok": True}


@router.delete("/target-policy/{pid}")
def delete_policy(pid: int):
    try:
        tp.delete(pid)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    return {"ok": True}


@router.post("/target-policy/from-approval/{aid}")
def from_approval(aid: int, body: dict):
    """审批卡「Always allow」三档窄化：exact（仅此确切参数——action+参数
    指纹）/ domain-action（此域名+此动作类）/ target（此目标全放行）。
    created_from=approval:<id>。"""
    from ...security import approve as approve_mod
    try:
        st = approve_mod.status(aid)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    # status() 不带 params——直查行取 params_json（窄化要参数指纹/域名）
    import json as _json2
    with approve_mod._conn() as _c:
        _row = _c.execute("SELECT params_json FROM approvals WHERE id=?",
                          (aid,)).fetchone()
    params = dict(_json2.loads(_row["params_json"] or "{}")) if _row else {}
    scope = str(body.get("scope") or "exact")
    action_type = str(st.get("action_type") or "")
    # 域名维度只认 host 类参数（to=收件人，不是目标系统）
    host = str(params.get("host") or params.get("domain") or "").strip()
    import hashlib
    if scope == "exact":
        fp = hashlib.sha1(json.dumps(params, sort_keys=True,
                                     ensure_ascii=False).encode()
                           ).hexdigest()[:12]
        match, kind = f"{action_type}:{fp}", "action"
        note = "仅此确切参数"
    elif scope == "domain-action" and host:
        match, kind = f"{action_type}:{host}", "action"
        note = f"此域名+此动作类（{host}）"
    elif scope == "target" and host:
        match, kind = host, "host"
        note = f"此目标全放行（{host}）"
    elif scope == "target":
        match, kind = action_type, "action"
        note = "此动作类全放行"
    else:
        raise HTTPException(400, f"scope={scope} 不适用（无域名参数的动作只"
                                 "支持 exact/target）")
    pid = tp.add(match, kind, "always", scope_note=note,
                 created_from=f"approval:{aid}")
    return {"ok": True, "id": pid, "match": match, "kind": kind}
