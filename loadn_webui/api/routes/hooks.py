"""P3 webhook 触发入口：/api/hooks 管理面（CRUD）+ /hooks/{token} 公开触发。

公开触发挂 /api 之外（share.py 同款——auth 中间件只护 /api 前缀，
token 即凭证）；管理面在 /api/hooks 下吃双头认证（_ADMIN_PREFIXES 已含）。
host_guard 对全部路径生效（外部源 Host 须在白名单）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ... import db as db_mod
from ... import hooks as hooks_mod
from ... import profile as profile_mod

router = APIRouter(prefix="/api")          # 管理面（W0 token + admin 双头）
pub = APIRouter()                          # 公开触发（token 即凭证）

from ._common import _http_err


def _norm_ips(val) -> str | None:
    """allowed_ips：list[str] 或逗号分隔串 → JSON 文本；空 = 不限。"""
    if val is None:
        return None
    items = ([str(x).strip() for x in val]
             if isinstance(val, list) else
             [x.strip() for x in str(val).split(",")])
    items = [x for x in items if x]
    if not items:
        return None
    for x in items:
        if "/" in x or ":" in x:
            raise HTTPException(400, f"allowed_ips 暂只支持单 IP（不支持网段/IPv6 段）：{x}")
    return json.dumps(items)


@router.get("/hooks")
def list_hooks():
    from ...security import userauth as _ua
    _u = _ua.current_user()
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_hooks(c)]
    if _u is not None and _u["role"] != "admin":
        rows = [r for r in rows if r.get("owner_id") in (None, _u["id"])]
    return {"hooks": rows}


@router.post("/hooks")
def create_hook(body: dict):
    name = str(body.get("name") or "").strip()[:80]
    if not name:
        raise HTTPException(400, "name 不能为空")
    tpl = str(body.get("prompt_template") or "").strip()
    if not tpl:
        raise HTTPException(400, "prompt_template 不能为空")
    if "{{payload}}" not in tpl:
        raise HTTPException(400, "prompt_template 须包含 {{payload}} 占位符")
    prof = str(body.get("profile") or "").strip()
    if prof and prof != "auto" and prof not in profile_mod.load_registry():
        raise HTTPException(400, f"未知 profile：{prof}")
    raw_rate = body.get("rate_limit_per_min")
    try:
        rate = (hooks_mod.RATE_LIMIT_DEFAULT
                if raw_rate is None or raw_rate == "" else int(raw_rate))
    except (TypeError, ValueError):
        raise HTTPException(400, "rate_limit_per_min 须为整数") from None
    if not 1 <= rate <= 600:
        raise HTTPException(400, "rate_limit_per_min 需在 1-600")
    from ...security import userauth as _ua
    _u = _ua.current_user()
    token = hooks_mod.mint_token()
    with db_mod.conn() as c:
        hid = db_mod.create_hook(
            c, token=token, name=name, profile=prof or None,
            prompt_template=tpl, enabled=1 if body.get("enabled", True) else 0,
            allowed_ips_json=_norm_ips(body.get("allowed_ips")),
            rate_limit_per_min=rate,
            owner_id=(_u["id"] if _u is not None else None))
        row = db_mod.to_dict(db_mod.get_hook(c, hid))
    return {"ok": True, "hook": row}


@router.patch("/hooks/{hid}")
def patch_hook(hid: int, body: dict):
    """改名/模板/启停/限流/白名单。token 不改（要换就删了重建）。"""
    updates: dict = {}
    if "name" in body:
        name = str(body["name"] or "").strip()[:80]
        if not name:
            raise HTTPException(400, "name 不能为空")
        updates["name"] = name
    if "prompt_template" in body:
        tpl = str(body["prompt_template"] or "").strip()
        if not tpl:
            raise HTTPException(400, "prompt_template 不能为空")
        if "{{payload}}" not in tpl:
            raise HTTPException(400, "prompt_template 须包含 {{payload}} 占位符")
        updates["prompt_template"] = tpl
    if "profile" in body:
        prof = str(body["profile"] or "").strip()
        if prof and prof != "auto" and prof not in profile_mod.load_registry():
            raise HTTPException(400, f"未知 profile：{prof}")
        updates["profile"] = prof or None
    if "enabled" in body:
        updates["enabled"] = 1 if body["enabled"] else 0
    if "allowed_ips" in body:
        updates["allowed_ips_json"] = _norm_ips(body["allowed_ips"])
    if "rate_limit_per_min" in body:
        try:
            rate = int(body["rate_limit_per_min"])
        except (TypeError, ValueError):
            raise HTTPException(400, "rate_limit_per_min 须为整数") from None
        if not 1 <= rate <= 600:
            raise HTTPException(400, "rate_limit_per_min 需在 1-600")
        updates["rate_limit_per_min"] = rate
    if not updates:
        raise HTTPException(400, "无可更新字段")
    from ...security import userauth as _ua
    with db_mod.conn() as c:
        h = db_mod.get_hook(c, hid)
        if h is None or not _ua.owner_ok(h, _ua.current_user()):
            raise HTTPException(404, f"webhook 不存在: {hid}")
        db_mod.update_hook(c, hid, **updates)
        row = db_mod.to_dict(db_mod.get_hook(c, hid))
    return {"ok": True, "hook": row}


@router.delete("/hooks/{hid}")
def delete_hook(hid: int):
    from ...security import userauth as _ua
    with db_mod.conn() as c:
        h = db_mod.get_hook(c, hid)
        if h is None or not _ua.owner_ok(h, _ua.current_user()):
            raise HTTPException(404, f"webhook 不存在: {hid}")
        db_mod.delete_hook(c, hid)
    return {"ok": True}


# ---------------------------------------------------------------- 公开触发（token 即凭证）
def _hook_by_token_or_401(token: str, *, ip: str = ""):
    # 二轮修#24：常量时间比对（未知 token 侧也做一次同量级 sha256——
    # 计时侧信道不泄露 token 存在性）；ip 留痕（原空串）
    import hashlib
    import hmac as _hm
    with db_mod.conn() as c:
        hook = db_mod.get_hook_by_token(c, token)
    if hook is None:
        _hm.compare_digest(
            hashlib.sha256(token.encode()).hexdigest(),
            hashlib.sha256(b"").hexdigest())       # 恒定耗时形态
        hooks_mod._audit_reject(None, ip, "reject_unknown_token", "未知 token")
        raise HTTPException(401, "未知 webhook token")
    return hook


@pub.post("/hooks/{token}")
async def trigger_hook(token: str, request: Request):
    """事件触发：校验 → 限流 → 模板渲染 → 建 session 后台执行（202 异步）。"""
    ip = (request.client.host if request.client else "") or ""
    hook = _hook_by_token_or_401(token, ip=ip)
    try:
        out = await hooks_mod.fire(hook, await request.body(), ip)
    except hooks_mod.HookRejected as e:
        raise HTTPException(e.status, e.reason) from None
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
    return JSONResponse(status_code=202, content={
        "status": "accepted", "session_id": out["session_id"],
        "run_id": out["run_id"]})


@pub.get("/hooks/{token}/runs/{run_id}")
def hook_run_status(token: str, run_id: int):
    """触发结果轮询（异步不阻塞）：run → session/turn 状态 + 最后回复。"""
    hook = _hook_by_token_or_401(token)          # 轮询无 request.ip 面（GET）
    with db_mod.conn() as c:
        run = db_mod.get_hook_run(c, hook["id"], run_id)
        if run is None:
            raise HTTPException(404, f"run 不存在: {run_id}")
        sess = db_mod.get_session(c, run["session_id"])
        turn = c.execute(
            "SELECT id, status, num_turns, duration_s, cost_usd FROM turns "
            "WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (run["session_id"],)).fetchone()
        last = c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='assistant' "
            "ORDER BY id DESC LIMIT 1", (run["session_id"],)).fetchone()
    return {
        "id": run["id"], "session_id": run["session_id"],
        "created_at": run["created_at"],
        "status": (sess["status"] if sess else "gone"),
        "turn": (dict(zip(("id", "status", "num_turns", "duration_s", "cost_usd"),
                          tuple(turn))) if turn else None),
        "result": (last["content"] if last else None),
    }
