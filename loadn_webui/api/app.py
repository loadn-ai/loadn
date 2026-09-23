"""FastAPI 装配：lifespan（单实例闸/孤儿回收/重启恢复）、W0 Web 面加固、SSE、静态前端。

W0（2026-09-22，T1 零点击 RCE 链修复）：
- Host 白名单（回环 ∪ server.extra_hosts ∪ share 域名）——防 DNS rebinding；
- token 认证强制（Bearer / X-Workdaddy-Token / ?token= 兼容 / SSE 一次性 ticket），
  机生 token 14 天宽限 warn→enforce，比较常量时间；
- 管理面（skills/skillhub/tools/settings/schedules 非 GET）独立 X-Workdaddy-Admin
  头，无宽限立即 enforce——跨域简单请求带不了自定义头，天然免疫 CSRF；
- /docs、/openapi.json、/redoc 纳入认证；
- frontend/dist 存在则挂 SPA（非 /api 404 → index.html fallback）。
"""
from __future__ import annotations

import fcntl
import gzip
import hmac
import os
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import db as db_mod
from ..claude_runner import reap_orphans
from ..config import CONFIG, PATHS, admin_token_value, resolve_runtime_token
from ..engine import ENGINE
from ..share import share_router
from ..util import get_logger
from . import routes
from .sse import event_stream

log = get_logger(__name__)

# 单实例锁持有句柄（进程生命周期内不能被 GC 关闭释放）
_SERVE_LOCK_FH = None
_EGRESS: list = [None]             # W5.1 代理实例（lifespan 起/停）

# SSE 一次性 ticket（W0.3）：ticket -> 过期 epoch。single-use（验证即摘除）+30s。
_TICKET_TTL_S = 30
_TICKETS: dict[str, float] = {}

# 管理面前缀（W0.4）：非 GET/HEAD/OPTIONS 一律要求 X-Workdaddy-Admin。
_ADMIN_PREFIXES = ("/api/skills", "/api/skillhub", "/api/tools",
                   "/api/settings", "/api/schedules", "/api/admin")

# 会话级破坏性端点（W6.4）：路径形如 /api/sessions/{sid}/kill，前缀表
# 表达不了通配，按末段判定（kill/rollback/unlock 非法 GET 一律双头）
_SESSION_ADMIN_SUFFIXES = ("kill", "rollback", "unlock")

# 宽限期告警限流（5 分钟一条，防日志刷屏）
_GRACE_WARN_EVERY_S = 300
_last_grace_warn = 0.0


def _issue_ticket() -> str:
    now = time.time()
    for t in [t for t, exp in _TICKETS.items() if exp < now]:
        _TICKETS.pop(t, None)
    ticket = secrets.token_urlsafe(24)
    _TICKETS[ticket] = now + _TICKET_TTL_S
    return ticket


def _norm_host(host: str) -> str:
    """Host 头归一（去端口；IPv6 字面量去方括号）。"""
    h = (host or "").strip().lower()
    if h.startswith("["):                     # [::1]:8792 / [::1]
        return h.split("]")[0].lstrip("[")
    return h.rsplit(":", 1)[0] if ":" in h else h


def _allowed_hosts() -> set[str]:
    hosts = {"127.0.0.1", "localhost", "::1"}
    hosts |= {_norm_host(h) for h in (CONFIG.server.extra_hosts or [])}
    base = (getattr(CONFIG.share, "base_url", "") or "").strip()
    if base:
        hosts.add((urlsplit(base).hostname or "").lower())
    return hosts


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _SERVE_LOCK_FH
    from ..config import ensure_dirs, migrate_legacy_db
    from ..scheduler import get_scheduler
    ensure_dirs()
    resolve_runtime_token()   # W0.1：token 空 → 生成 var/server_token + 14 天宽限
    _mig = migrate_legacy_db()   # R2.5：旧 var/workdaddy.db → var/loadn.db（copy）
    if _mig is not None:
        log.info("[R2.5] 旧库迁移完成 → %s", _mig)
    # W5.1：出口白名单代理（引擎 env 通道接管；enforce 由 security.egress_mode）
    from ..egress_proxy import EgressProxy
    try:
        from ..config import PATHS as _P
        _EGRESS[0] = EgressProxy(
            port=CONFIG.security.egress_proxy_port,
            uds_path=str(_P["run"] / "egress.sock"))
        CONFIG.security.egress_proxy_port = await _EGRESS[0].start()
    except OSError:
        log.exception("egress 代理绑定失败（多实例/端口占用）——本实例无代理")
    # 单实例闸（2026-09-17 事故）：双 serve 抢端口时，败者在 bind 失败前也会先跑
    # lifespan——孤儿清理/中断恢复会直接误伤胜者正在跑的 turn（systemd
    # Restart=always 每 5s 拉起僵尸实例，几小时内三批把活跃 turn 标成
    # interrupted，用户新消息 6 秒内被秒杀）。flock 排他：拿不到锁 = 已有实例
    # 在管平台，干净退出、不碰任何状态（配 unit Restart=on-failure 即不再重试）。
    _SERVE_LOCK_FH = open(PATHS["run"] / "serve.lock", "w")
    try:
        fcntl.flock(_SERVE_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning("serve.lock 已被持有（已有实例在跑），本实例直接退出")
        os._exit(0)
    # 顺序关键：先恢复收养（claimed pids）再清孤儿——否则幸存 worker 被误杀
    recovered = ENGINE.recover_after_restart()
    reaped = reap_orphans(skip=recovered["claimed_pids"])
    sched = get_scheduler(ENGINE)
    sched.start()          # durable：停机期间到期的 job 重启后由首轮扫描补投
    log.info("loadn webui 启动：孤儿清理 %d，收养 %s 补记账 %s interrupted %d requeue %s",
             reaped, recovered["adopted"], recovered["finished"],
             recovered["interrupted"], recovered["requeued"])
    yield
    await sched.stop()
    if _EGRESS[0] is not None:
        await _EGRESS[0].stop()
    # 优雅停：turn 子进程独立 session 存活（配 systemd KillMode=process），
    # CancelledError 路径不杀——下次启动 recover_after_restart 收养续跑


def check_auth(request: Request) -> bool:
    """token 校验（常量时间）。通道：SSE ticket（single-use）> 头/Bearer/?token=。

    给了 ticket 参数即只认 ticket（无效直接拒，不回退 token——防混绕）。
    ?token= 为兼容通道（脚本/SSE 旧用法），宽限期结束次版本移除。
    """
    token = CONFIG.server.token
    if not token:
        return True
    tk = request.query_params.get("ticket")
    if tk is not None:
        exp = _TICKETS.pop(tk, None)          # single-use：取出即消费
        return exp is not None and exp >= time.time()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token):
        return True
    # 双名：X-Loadn-*（R9 品牌名）+ X-Workdaddy-*（存量 CLI/脚本兼容层）
    supplied = (request.headers.get("X-Loadn-Token", "")
                or request.headers.get("X-Workdaddy-Token", ""))
    if hmac.compare_digest(supplied, token):
        return True
    if hmac.compare_digest(request.query_params.get("token", ""), token):
        return True
    return False


def _in_grace() -> bool:
    g = CONFIG.server.token_grace_until
    return g > 0 and time.time() < g


def _grace_warn(request: Request) -> None:
    global _last_grace_warn
    now = time.time()
    if now - _last_grace_warn >= _GRACE_WARN_EVERY_S:
        _last_grace_warn = now
        days = max(0.0, (CONFIG.server.token_grace_until - now) / 86400)
        log.warning("[W0] token 宽限期内放行未认证请求 %s %s（剩 %.1f 天后强制认证；"
                    "前端刷新一次按提示输入 token 即可）",
                    request.method, request.url.path, days)


def _is_admin_plane(path: str, method: str) -> bool:
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    if path.startswith(_ADMIN_PREFIXES):
        return True
    return (path.startswith("/api/sessions/")
            and path.rsplit("/", 1)[-1] in _SESSION_ADMIN_SUFFIXES)


app = FastAPI(title="loadn webui", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """W0：token 认证（/api + /docs + /openapi.json + /redoc）+ 管理面 admin 头。

    /share（公开只读设计）与 SPA 静态资源不设防——SPA 加载本身无敏感数据，
    且 token 输入页需要先能加载。管理面无宽限：token 生效即要求 admin 头
    （跨域简单请求带不了自定义头 → CSRF 免疫，T1a）。
    """
    path = request.url.path
    token = CONFIG.server.token
    protected = (path.startswith("/api") or path in ("/docs", "/openapi.json")
                 or path.startswith(("/docs", "/redoc")))
    if token and protected:
        # 1) 管理面：独立 admin 头（先查，无宽限）
        if _is_admin_plane(path, request.method):
            admin = admin_token_value()
            supplied = (request.headers.get("X-Loadn-Admin", "")
                        or request.headers.get("X-Workdaddy-Admin", ""))
            if not supplied or not hmac.compare_digest(supplied, admin):
                return JSONResponse({"error": "admin required"}, status_code=403)
            return await call_next(request)
        # 2) 普通面：token（宽限期内无凭证放行+限流告警）
        if not check_auth(request):
            if _in_grace():
                _grace_warn(request)
            else:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.get("/api/sse-ticket")
async def sse_ticket(request: Request):
    """W0.3 SSE 一次性票：EventSource 带不了自定义头，先认证取票再连流。"""
    return {"ticket": _issue_ticket(), "expires_in": _TICKET_TTL_S}


@app.middleware("http")
async def host_guard_middleware(request: Request, call_next):
    """W0.2 Host 白名单（防 DNS rebinding）。

    Starlette 后注册 = 外层：须在 auth 之前执行（定义在本文件 auth 之后注册）。
    """
    host = _norm_host(request.headers.get("host", ""))
    if not host or host not in _allowed_hosts():
        log.warning("[W0] Host 拒绝：%r 不在白名单", request.headers.get("host"))
        return JSONResponse({"error": "forbidden host"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def gzip_json_middleware(request: Request, call_next):
    """只压 JSON 响应（SSE/文件下载不动——gzip 缓冲会拖垮流式输出）。

    远程打开大会话：messages blocks_json 明细动辄数 MB 裸传，gzip 后 ~1/8。
    客户端 httpx/浏览器对 Content-Encoding 全部透明解压。"""
    resp = await call_next(request)
    if (resp.status_code != 200
            or "gzip" not in request.headers.get("accept-encoding", "")
            or not resp.headers.get("content-type", "").startswith("application/json")):
        return resp
    body = b"".join([chunk async for chunk in resp.body_iterator])
    gz = gzip.compress(body, 6) if len(body) >= 1024 else body
    # 重建 Response 必须承袭原响应自定义头（如 ingest 直通道的 CORS 头），
    # 只排除由本中间件/传输层重新生成的表示头
    headers = {k: v for k, v in resp.headers.items()
               if k.lower() not in ("content-length", "content-encoding", "vary",
                                    "content-type", "server", "date")}
    headers["Vary"] = "Accept-Encoding"
    if len(gz) < len(body):
        headers["Content-Encoding"] = "gzip"
        return Response(content=gz, status_code=200,
                        media_type="application/json", headers=headers)
    return Response(content=body, status_code=200,
                    media_type="application/json", headers=headers)


app.include_router(routes.router)
# /share 公开只读路由（auth 中间件只护 /api）：注册须早于下方 spa_fallback
app.include_router(share_router)


@app.get("/api/sessions/{sid}/events")
async def sse_events(sid: str, request: Request):
    with db_mod.conn() as c:
        if db_mod.get_session(c, sid) is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
    return await event_stream(sid, request)


# ---------------------------------------------------------------- 静态前端（SPA）
_DIST = PATHS["frontend_dist"]


@app.get("/")
async def index():
    p = _DIST / "index.html"
    if p.exists():
        return FileResponse(p, headers={"Cache-Control": "no-store"})
    return JSONResponse({"name": "loadn webui", "status": "api-only",
                         "hint": "frontend/dist 不存在；API 文档见 /docs"})


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    if full_path.startswith(("api", "docs", "openapi.json", "redoc")):
        return JSONResponse({"error": "not found"}, status_code=404)
    if full_path:
        p = (_DIST / full_path).resolve()
        try:
            p.relative_to(_DIST.resolve())
        except ValueError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if p.is_file():
            return FileResponse(p, headers={"Cache-Control": "no-store"})
    idx = _DIST / "index.html"
    if idx.exists():
        return FileResponse(idx, headers={"Cache-Control": "no-store"})
    return JSONResponse({"error": "not found"}, status_code=404)
