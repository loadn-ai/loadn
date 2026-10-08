"""账号密码登录（八轮多用户批1）：login/logout/setup/status/me/改密。

免认证面（auth_middleware 白名单）：/api/auth/*——login/setup 自带强校验
（setup 仅在 users 空时开放，或持有效 token——存量部署首次绑账号）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...security import userauth as ua
from ...security.audit import audit
from ...util import iso

router = APIRouter(prefix="/api/auth")

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,31}$")


def _client_user(request: Request):
    """当前 cookie 用户（None=token/宽限通道）。"""
    sid = request.cookies.get(ua.COOKIE_NAME, "")
    return ua.session_user(sid)


@router.get("/status")
def auth_status(request: Request):
    """前端探测：需要安装向导 / 已登录谁 / cookie 通道是否可用。"""
    user = _client_user(request)
    return {
        "needs_setup": ua.user_count() == 0,
        # 账号体系已建立且访问者未登录——前端据此主动弹登录门（宽限期
        # 内 GET 全放行不会有 401 事件可等）
        "auth_required": ua.user_count() > 0 and user is None,
        "logged_in": user is not None,
        "user": ({"id": user["id"], "username": user["username"],
                  "role": user["role"],
                  "display_name": user["display_name"]} if user else None),
    }


@router.post("/setup")
def setup(body: dict, request: Request):
    """首账号安装向导：仅 users 空时开放（或持有效 token 的存量部署——
    token 持有者=旧单用户本人，建号即归并 legacy 行）。首号 role=admin。"""
    if ua.user_count() > 0:
        # 存量部署路径：token/宽限通道才可补建（cookie 未登录无权）
        user = _client_user(request)
        if user is None or user["role"] != "admin":
            raise HTTPException(403, "已有账号体系——请联系管理员或用 token 通道")
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not _USERNAME_RE.match(username):
        raise HTTPException(400, "用户名需匹配 ^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,31}$")
    if len(password) < 8:
        raise HTTPException(400, "密码至少 8 位")
    if ua.get_user_by_name(username) is not None:
        raise HTTPException(409, f"用户名已存在: {username}")
    first = ua.user_count() == 0
    uid = ua.create_user(username, password,
                         role="admin" if first else "user")
    if first:
        n = ua.claim_legacy_rows(uid)
        audit("auth", {"action": "setup_admin", "username": username,
                       "legacy_rows_claimed": n})
    sid = ua.create_session(uid, request.headers.get("user-agent", ""))
    audit("auth", {"action": "login", "username": username, "via": "setup"})
    resp = JSONResponse({"ok": True, "user": {"id": uid, "username": username,
                                              "role": "admin" if first else "user"}})
    resp.set_cookie(ua.COOKIE_NAME, sid, httponly=True, samesite="lax",
                    max_age=ua.SESSION_TTL_S, path="/")
    return resp


@router.post("/login")
def login(body: dict, request: Request):
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    user = ua.verify_login(username, password)
    if user is None:
        audit("auth", {"action": "login_failed", "username": username[:32]})
        raise HTTPException(401, "用户名或密码不正确")
    sid = ua.create_session(user["id"], request.headers.get("user-agent", ""))
    audit("auth", {"action": "login", "username": username})
    resp = JSONResponse({"ok": True, "user": {
        "id": user["id"], "username": user["username"], "role": user["role"]}})
    resp.set_cookie(ua.COOKIE_NAME, sid, httponly=True, samesite="lax",
                    max_age=ua.SESSION_TTL_S, path="/")
    return resp


@router.post("/logout")
def logout(request: Request):
    sid = request.cookies.get(ua.COOKIE_NAME, "")
    ua.drop_session(sid)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(ua.COOKIE_NAME, path="/")
    return resp


@router.get("/me")
def me(request: Request):
    user = _client_user(request)
    if user is None:
        raise HTTPException(401, "未登录（cookie 通道）")
    return {"id": user["id"], "username": user["username"],
            "role": user["role"], "display_name": user["display_name"],
            "last_login_at": user["last_login_at"]}


@router.post("/password")
def change_password(body: dict, request: Request):
    user = _client_user(request)
    if user is None:
        raise HTTPException(401, "未登录（cookie 通道）")
    old = str(body.get("old_password") or "")
    new = str(body.get("new_password") or "")
    if len(new) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    row = ua.get_user(user["id"])
    if not ua.verify_password(old, row["password_hash"]):
        raise HTTPException(401, "当前密码不正确")
    with ua._conn() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?",
                  (ua.hash_password(new), user["id"]))
    audit("auth", {"action": "password_changed", "username": user["username"]})
    return {"ok": True}


# ---------------------------------------------------------------- 用户管理（admin）
def _require_admin(request: Request):
    user = _client_user(request)
    if user is None or user["role"] != "admin":
        raise HTTPException(403, "admin required")
    return user


@router.get("/users")
def list_users(request: Request):
    """账号列表（admin）：不含哈希；带活跃会话数。"""
    _require_admin(request)
    ua.ensure_tables()
    with ua._conn() as c:
        rows = c.execute(
            "SELECT u.id, u.username, u.role, u.display_name, u.disabled, "
            "u.created_at, u.last_login_at, "
            "(SELECT COUNT(*) FROM auth_sessions s WHERE s.user_id=u.id "
            " AND s.expires_at > ?) AS active_sessions "
            "FROM users u ORDER BY u.id",
            (iso(),)).fetchall()
    return {"users": [dict(r) for r in rows]}


@router.post("/users")
def admin_create_user(body: dict, request: Request):
    """admin 建号（批2：替代 setup 存量分支的手工 API 调用）。"""
    _require_admin(request)
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    role = str(body.get("role") or "user")
    if role not in ("admin", "user"):
        raise HTTPException(400, "role 只能是 admin|user")
    if not _USERNAME_RE.match(username):
        raise HTTPException(400, "用户名需匹配 ^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,31}$")
    if len(password) < 8:
        raise HTTPException(400, "密码至少 8 位")
    if ua.get_user_by_name(username) is not None:
        raise HTTPException(409, f"用户名已存在: {username}")
    uid = ua.create_user(username, password, role=role)
    audit("auth", {"action": "user_created", "username": username,
                   "role": role, "by": _require_admin(request)["username"]})
    return {"ok": True, "id": uid}


@router.patch("/users/{uid}")
def patch_user(uid: int, body: dict, request: Request):
    """改角色/启停/显示名/重置密码（admin）。禁用即时生效：现存会话在
    下次请求即失效（session_user 查 disabled）。"""
    admin = _require_admin(request)
    row = ua.get_user(uid)
    if row is None:
        raise HTTPException(404, f"用户不存在: {uid}")
    updates: list = []
    with ua._conn() as c:
        if "role" in body:
            role = str(body["role"] or "")
            if role not in ("admin", "user"):
                raise HTTPException(400, "role 只能是 admin|user")
            if row["id"] == admin["id"] and role != "admin":
                raise HTTPException(400, "不能降级自己（避免锁死管理面）")
            c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
            updates.append(f"role={role}")
        if "disabled" in body:
            dis = 1 if body["disabled"] else 0
            if row["id"] == admin["id"] and dis:
                raise HTTPException(400, "不能禁用自己")
            c.execute("UPDATE users SET disabled=? WHERE id=?", (dis, uid))
            if dis:      # 禁用即踢下线全部会话
                c.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
            updates.append(f"disabled={dis}")
        if "display_name" in body:
            c.execute("UPDATE users SET display_name=? WHERE id=?",
                      (str(body["display_name"] or "")[:60], uid))
        if "password" in body:
            new = str(body.get("password") or "")
            if len(new) < 8:
                raise HTTPException(400, "密码至少 8 位")
            c.execute("UPDATE users SET password_hash=? WHERE id=?",
                      (ua.hash_password(new), uid))
            c.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
            updates.append("password_reset")
    audit("auth", {"action": "user_updated", "uid": uid,
                   "changes": ",".join(updates) or "display_name",
                   "by": admin["username"]})
    return {"ok": True}
