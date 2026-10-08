"""多用户账号密码认证（八轮）——pbkdf2（标准库，依赖红线内）+ 服务端会话。

通道语义（与 W0 token 并存）：
- **cookie 会话通道**：登录换 httpOnly cookie（auth_sessions 表）——
  request.state.user 有值，数据按属主隔离（owner_id）。
- **token/宽限通道**：原 W0 语义不变（CLI/API/SSE 与存量部署兼容）——
  request.state.user 为 None，属主检查放行（单用户时代语义）。

安全形态：
- 密码 pbkdf2_hmac-sha256（260k 轮，自带随机盐；格式 pbkdf2$iter$salt$hash）
- 登录失败恒定耗时（无论用户不存在/密码错都走一次完整 pbkdf2）
- 会话 sid 32B urlsafe；过期 30 天滑动（last_seen 刷新）
- 越权 fail-closed：owner 检查不过=404（不暴露存在性）
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import secrets
import sqlite3
import time

from ..config import PATHS
from ..util import get_logger, iso

log = get_logger(__name__)

# 当前请求的 cookie 用户（None=token/宽限通道）——中间件 set、属主
# 收口 helper get（sync 路由线程池的 context 复制由 anyio 保证）
_current_user: contextvars.ContextVar = contextvars.ContextVar(
    "loadn_user", default=None)


def set_current_user(user) -> None:
    _current_user.set(user)


def current_user():
    return _current_user.get()


def _iso_ts(ts: float) -> str:
    """epoch → iso() 同口径 UTC（util.iso() 无参版——这里要带时刻）。"""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(
        timespec="seconds")


PBKDF2_ITERS = 260_000
SESSION_TTL_S = 30 * 86400
COOKIE_NAME = "loadn_session"


# ---------------------------------------------------------------- 密码
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return f"pbkdf2${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        # 坏行/空密码：仍跑一次 pbkdf2 保持恒定耗时，恒 False
        hashlib.pbkdf2_hmac("sha256", password.encode(), b"x" * 16, PBKDF2_ITERS)
        return False


# ---------------------------------------------------------------- 用户
def _conn() -> sqlite3.Connection:
    PATHS["db"].parent.mkdir(parents=True, exist_ok=True)   # fresh home
    c = sqlite3.connect(PATHS["db"], timeout=10)
    c.row_factory = sqlite3.Row
    return c


def ensure_tables() -> None:
    """users/auth_sessions 建表（auth 路由可能先于 db.conn() 的迁移跑）。"""
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT CHECK(role IN ('admin','user')) DEFAULT 'user',
          display_name TEXT,
          disabled INTEGER DEFAULT 0,
          created_at TEXT, last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
          id TEXT PRIMARY KEY,
          user_id INTEGER REFERENCES users(id),
          created_at TEXT, expires_at TEXT, last_seen_at TEXT,
          agent TEXT
        );
        """)


def user_count() -> int:
    ensure_tables()
    with _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def get_user(uid: int):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def get_user_by_name(username: str):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE username=?",
                         (username,)).fetchone()


def create_user(username: str, password: str, role: str = "user") -> int:
    """建账号（唯一性由 UNIQUE 约束保证；调用方先做形态校验）。"""
    ensure_tables()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO users(username, password_hash, role, created_at) "
            "VALUES(?,?,?,?)",
            (username, hash_password(password), role, iso()))
        return cur.lastrowid


def claim_legacy_rows(uid: int) -> int:
    """存量归并：owner_id IS NULL 的行归属首个 admin（setup 时一次跑）。"""
    n = 0
    with _conn() as c:
        for tbl in ("sessions", "projects", "scheduled_jobs", "webhooks",
                    "channel_bindings"):
            cur = c.execute(
                f"UPDATE {tbl} SET owner_id=? WHERE owner_id IS NULL", (uid,))
            n += cur.rowcount or 0
    return n


def verify_login(username: str, password: str):
    """返回 user row 或 None（恒定耗时）。"""
    row = get_user_by_name(username)
    stored = row["password_hash"] if row is not None else ""
    ok = verify_password(password, stored or "pbkdf2$1$00$00")
    if not ok or row is None or row["disabled"]:
        return None
    with _conn() as c:
        c.execute("UPDATE users SET last_login_at=? WHERE id=?",
                  (iso(), row["id"]))
    return row


# ---------------------------------------------------------------- 会话
def create_session(uid: int, agent: str = "") -> str:
    ensure_tables()
    sid = secrets.token_urlsafe(32)
    now = time.time()
    with _conn() as c:
        c.execute(
            "INSERT INTO auth_sessions(id, user_id, created_at, expires_at, "
            "last_seen_at, agent) VALUES(?,?,?,?,?,?)",
            (sid, uid, _iso_ts(now), _iso_ts(now + SESSION_TTL_S), _iso_ts(now),
             agent[:120]))
    return sid


def session_user(sid: str):
    """cookie sid → user row（过期/未知/禁用 → None；滑动续期）。"""
    if not sid:
        return None
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT u.*, s.expires_at FROM auth_sessions s "
                "JOIN users u ON u.id=s.user_id WHERE s.id=?",
                (sid,)).fetchone()
            if row is None:
                return None
            exp = row["expires_at"] or ""
            # iso() 带时区可直接比较（字典序）
            if exp < iso():
                c.execute("DELETE FROM auth_sessions WHERE id=?", (sid,))
                return None
            if row["disabled"]:
                return None
            now = time.time()
            c.execute("UPDATE auth_sessions SET last_seen_at=?, expires_at=? "
                      "WHERE id=?", (_iso_ts(now), _iso_ts(now + SESSION_TTL_S), sid))
            return row
    except sqlite3.Error:
        return None


def drop_session(sid: str) -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM auth_sessions WHERE id=?", (sid,))
    except sqlite3.Error:
        pass


# ---------------------------------------------------------------- 属主判定
def owner_ok(row, user) -> bool:
    """属主检查（路由单点收口用）。

    user 为 None（token/宽限通道）→ 放行（单用户时代语义，CLI/存量兼容）；
    cookie 用户 → 行属主匹配或 admin 放行；row 无 owner_id 列（NULL）→
    仅 admin（legacy 归并前的窗口）。
    """
    if user is None:
        return True
    if user["role"] == "admin":
        return True
    owner = None
    try:
        owner = row["owner_id"]
    except (IndexError, KeyError):
        owner = None
    return owner is not None and owner == user["id"]
