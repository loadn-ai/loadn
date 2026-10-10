"""W0 Web 面加固对抗用例（v1.1 方案 §6.1 验收 = T1a/T1b + CSP + docs 收护）。

对抗面（§8.0 D3 平台边界）：
- T1a  本地恶意页跨域写（无自定义头）→ 401/403；Host=evil.com → 403（DNS rebinding）
- T1b  SSE ticket 重放/复用 → single-use + 30s 过期拒绝
- 管理面（skills/tools/settings/schedules 非 GET）无 X-Workdaddy-Admin → 403（无宽限）
- /docs、/openapi.json 纳入认证
- 预览/下载响应带 CSP/nosniff/no-referrer
- token 比较常量时间（行为等价：正确通过/错误拒绝，无绕过）
"""
from __future__ import annotations

import time

import httpx
import pytest


@pytest.fixture()
def w0(server_url):
    """W0 专项上下文：裸 client（无默认头）+ 有限期 token 状态。"""
    from loadn_webui.config import CONFIG

    saved = (CONFIG.server.token, CONFIG.server.admin_token,
             CONFIG.server.token_grace_until, CONFIG.server.extra_hosts)
    # 强制立即 enforce（非宽限）做认证用例
    CONFIG.server.token = "w0test-token-123"
    CONFIG.server.token_grace_until = 0.0
    ctx = {
        "url": server_url,
        "token": "w0test-token-123",
        "headers": {"X-Workdaddy-Token": "w0test-token-123",
                    "X-Workdaddy-Admin": "w0test-token-123"},
    }
    yield ctx
    (CONFIG.server.token, CONFIG.server.admin_token,
     CONFIG.server.token_grace_until, CONFIG.server.extra_hosts) = saved


def _get(ctx, path, **kw):
    return httpx.get(ctx["url"] + path, timeout=10, **kw)


# ---------------------------------------------------------------- Host 白名单

def test_host_evil_rejected(w0):
    r = _get(w0, "/api/health", headers={"Host": "evil.com"})
    assert r.status_code == 403


def test_host_rebinding_subdomain_rejected(w0):
    r = _get(w0, "/api/health", headers={"Host": "127.0.0.1.evil.com"})
    assert r.status_code == 403


def test_host_loopback_allowed(w0):
    r = _get(w0, "/api/health", headers=w0["headers"])
    assert r.status_code == 200


def test_host_with_port_allowed(w0):
    r = _get(w0, "/api/health", headers={**w0["headers"], "Host": "127.0.0.1:8792"})
    assert r.status_code == 200


def test_host_ipv6_allowed(w0):
    r = _get(w0, "/api/health", headers={**w0["headers"], "Host": "[::1]:8792"})
    assert r.status_code == 200


def test_host_extra_hosts_config(w0):
    from loadn_webui.config import CONFIG
    CONFIG.server.extra_hosts = ["my.lan.box:9000", "nas.local"]
    try:
        r = _get(w0, "/api/health", headers={**w0["headers"], "Host": "my.lan.box:9000"})
        assert r.status_code == 200
        r = _get(w0, "/api/health", headers={**w0["headers"], "Host": "nas.local"})
        assert r.status_code == 200
    finally:
        CONFIG.server.extra_hosts = []


def test_host_share_domain_allowed(w0):
    """share 反代链路回归：share.base_url 的域名必须在白名单（v1.1 §14-4）。"""
    from loadn_webui.config import CONFIG
    saved = CONFIG.share.base_url
    CONFIG.share.base_url = "https://your-domain.com/share"
    try:
        r = _get(w0, "/share/whatever", headers={"Host": "your-domain.com"})
        assert r.status_code != 403          # 过 Host 闸（404 等业务码均可）
    finally:
        CONFIG.share.base_url = saved


def test_host_missing_rejected(w0):
    r = httpx.get(w0["url"] + "/api/health", timeout=10,
                  headers={**w0["headers"], "Host": ""})
    assert r.status_code == 403


# ---------------------------------------------------------------- token 认证

def test_no_credentials_401(w0):
    r = _get(w0, "/api/health")
    assert r.status_code == 401


def test_wrong_token_401(w0):
    r = _get(w0, "/api/health", headers={"X-Workdaddy-Token": "wrong"})
    assert r.status_code == 401


def test_bearer_ok(w0):
    r = _get(w0, "/api/health", headers={"Authorization": f"Bearer {w0['token']}"})
    assert r.status_code == 200


def test_query_token_compat(w0):
    """?token= 兼容通道（宽限期后次版本移除）。"""
    r = _get(w0, "/api/health?token=" + w0["token"])
    assert r.status_code == 200


def test_docs_protected(w0):
    assert _get(w0, "/docs").status_code == 401
    assert _get(w0, "/openapi.json").status_code == 401
    assert _get(w0, "/docs", headers=w0["headers"]).status_code == 200


def test_spa_index_still_public(w0):
    """SPA 静态不设防（token 输入页要先能加载）。"""
    r = _get(w0, "/")
    assert r.status_code in (200, 404)       # dist 在=200；api-only 模式 404/200 均可
    assert r.status_code != 401


# ---------------------------------------------------------------- 管理面（无宽限）

def test_admin_plane_no_header_403(w0):
    """T1a 核心：恶意页跨域简单请求 POST 装 skill——无自定义头即拒。"""
    r = httpx.post(w0["url"] + "/api/skills/install", timeout=10,
                   headers={"X-Workdaddy-Token": w0["token"]}, json={"ref": "x"})
    assert r.status_code == 403


def test_admin_plane_no_auth_at_all_403(w0):
    r = httpx.post(w0["url"] + "/api/skills/install", timeout=10, json={"ref": "x"})
    assert r.status_code in (401, 403)


def test_admin_plane_with_header_passes_auth(w0):
    r = httpx.post(w0["url"] + "/api/skills/install", timeout=10,
                   headers=w0["headers"], json={"ref": "x"})
    assert r.status_code != 403 and r.status_code != 401   # 过闸（业务码任意）


def test_admin_plane_get_reads_allowed(w0):
    """管理面 GET（列表/搜索）不要求 admin 头——只有写操作收紧。"""
    r = _get(w0, "/api/skills", headers={"X-Workdaddy-Token": w0["token"]})
    assert r.status_code == 200


def test_admin_plane_separate_admin_token(w0):
    from loadn_webui.config import CONFIG
    CONFIG.server.admin_token = "admin-only-456"
    try:
        # 只带 token（=用户消息 token）访问管理面 → 403
        r = httpx.post(w0["url"] + "/api/skills/install", timeout=10,
                       headers={"X-Workdaddy-Token": w0["token"]}, json={"ref": "x"})
        assert r.status_code == 403
        # 带 admin 头 → 过闸
        r = httpx.post(w0["url"] + "/api/skills/install", timeout=10,
                       headers={"X-Workdaddy-Admin": "admin-only-456"},
                       json={"ref": "x"})
        assert r.status_code != 403 and r.status_code != 401
    finally:
        CONFIG.server.admin_token = ""


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/skills"),
    ("PUT", "/api/skills/x/file"),
    ("POST", "/api/skills/upload"),
    ("PUT", "/api/tools/mcp/foo"),
    ("DELETE", "/api/tools/mcp/foo"),
    ("PUT", "/api/settings/run"),
    # /api/schedules 与 /api/hooks 的写面已降普通面（多用户批2——owner
    # 复核在路由内）；钉版改判「不再 403」（防无意升回管理面/或再降级
    # 更多敏感面时显形）
    ("PUT", "/api/memory/config"),
])
def test_admin_plane_prefix_matrix(w0, method, path):
    """全部管理面前缀 × 写方法：token 但无 admin 头 → 403。"""
    r = httpx.request(method, w0["url"] + path, timeout=10,
                      headers={"X-Workdaddy-Token": w0["token"]}, json={})
    assert r.status_code == 403, f"{method} {path}"


# ---------------------------------------------------------------- SSE ticket（T1b）

def test_ticket_flow_ok(w0):
    r = _get(w0, "/api/sse-ticket", headers=w0["headers"])
    assert r.status_code == 200
    ticket = r.json()["ticket"]
    assert r.json()["expires_in"] == 30
    # 用票连 SSE：sid 不存在 → 404（说明过了 auth；401 才是 auth 失败）
    r = _get(w0, f"/api/sessions/00000000-0000-0000-0000-000000000000/events?ticket={ticket}")
    assert r.status_code == 404


def test_ticket_replay_rejected(w0):
    """T1b：single-use——同票第二次必拒。"""
    ticket = _get(w0, "/api/sse-ticket", headers=w0["headers"]).json()["ticket"]
    _get(w0, f"/api/sessions/00000000-0000-0000-0000-000000000000/events?ticket={ticket}")
    r = _get(w0, f"/api/sessions/00000000-0000-0000-0000-000000000000/events?ticket={ticket}")
    assert r.status_code == 401


def test_ticket_forged_rejected(w0):
    r = _get(w0, "/api/sessions/00000000-0000-0000-0000-000000000000/events?ticket=forged")
    assert r.status_code == 401


def test_ticket_no_fallback_to_token(w0):
    """给了 ticket 参数即只认 ticket（防 ticket→token 混绕）。"""
    r = _get(w0, "/api/sessions/00000000-0000-0000-0000-000000000000/events"
                 f"?ticket=forged&token={w0['token']}")
    assert r.status_code == 401


def test_ticket_requires_auth_to_issue(w0):
    assert _get(w0, "/api/sse-ticket").status_code == 401


def test_ticket_expiry(w0):
    """过期票拒（不真等 30s：直接塞一张已过期票进内存表）。"""
    from loadn_webui.api.app import _TICKETS
    _TICKETS["expired-test"] = time.time() - 1
    r = _get(w0, "/api/sessions/00000000-0000-0000-0000-000000000000/events?ticket=expired-test")
    assert r.status_code == 401


# ---------------------------------------------------------------- 宽限期语义

def test_grace_period_allows_but_admin_still_enforced(w0):
    """宽限期内：普通面无凭证放行；管理面无宽限仍 403。"""
    from loadn_webui.config import CONFIG
    CONFIG.server.token_grace_until = time.time() + 86400
    try:
        assert _get(w0, "/api/health").status_code == 200        # 宽限放行
        r = httpx.post(w0["url"] + "/api/skills/install", timeout=10, json={"ref": "x"})
        assert r.status_code in (401, 403)                       # 管理面照拦
    finally:
        CONFIG.server.token_grace_until = 0.0


# ---------------------------------------------------------------- CSP / 预览头

async def test_preview_csp_headers(w0, client):
    """预览/下载响应带 CSP+nosniff+no-referrer（W0.6）。

    走真数据流：建会话 → ingest md 进 artifacts/ → 扫描 → preview。
    """
    sid = (await client.post("/api/sessions", json={"title": "w0-csp"})
           ).json()["session"]["id"]
    r = await client.post(f"/api/sessions/{sid}/ingest?to=artifacts/",
                          files={"file": ("w0.md", b"# hi\n\nworld", "text/markdown")})
    assert r.status_code == 200
    arts = (await client.get(f"/api/sessions/{sid}/artifacts")).json()["artifacts"]
    aid = next(a["id"] for a in arts if a["path"].endswith("w0.md"))
    r = await client.get(f"/api/artifacts/{aid}/preview")
    assert r.status_code == 200
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    r = await client.get(f"/api/artifacts/{aid}/download")
    assert "content-security-policy" in r.headers
    r = await client.get(f"/api/sessions/{sid}/archive?path=artifacts/")
    assert r.status_code == 200
    assert "content-security-policy" in r.headers


# ---------------------------------------------------------------- token 生成纪律

def test_generated_token_file_0600(server_url):
    """lifespan 生成的 var/server_token 权限 0600。"""
    import stat

    from loadn_webui.config import PATHS
    p = PATHS["var"] / "server_token"
    assert p.exists(), "lifespan 应已生成 token 文件"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


# ---------------------------------------------------------------- R9.1 安全中心+头双名

def test_header_dual_name_loadn_primary(w0):
    """前端 R9 改发 X-Loadn-*——主名必须可用（宽限期掩盖过失配）。"""
    r = _get(w0, "/api/health", headers={"X-Loadn-Token": w0["token"]})
    assert r.status_code == 200


def test_header_dual_name_admin(w0):
    """X-Loadn-Admin 也要过管理面（kill-all POST）。"""
    r = httpx.post(w0["url"] + "/api/admin/audit/verify", timeout=10,
                   headers={"X-Loadn-Token": w0["token"],
                            "X-Loadn-Admin": w0["token"]})
    assert r.status_code == 200


def test_admin_prefix_now_gated(w0):
    """回归：/api/admin/* 非 GET 必须吃 admin 双头（曾漏在前缀表外）。"""
    r = httpx.post(w0["url"] + "/api/admin/audit/verify", timeout=10,
                   headers={"X-Loadn-Token": w0["token"]})
    assert r.status_code == 403


def test_session_kill_gated(w0):
    """会话级 kill（后缀判定）也要 admin 双头。"""
    r = httpx.post(w0["url"] + "/api/sessions/nonexistent/kill", timeout=10,
                   headers={"X-Loadn-Token": w0["token"]})
    assert r.status_code == 403          # admin 门先于 404


def test_security_posture_shape(w0):
    """姿态端点：六段齐+不泄密（无 vault 值/canary token 字样）。"""
    r = _get(w0, "/api/admin/security", headers=w0["headers"])
    assert r.status_code == 200
    d = r.json()
    for k in ("sandbox", "policy", "egress", "canary", "vault", "audit"):
        assert k in d, f"姿态缺 {k}"
    assert "platforms" in d["vault"] and "secrets" not in str(d).lower()


def test_audit_feed_and_verify(w0):
    """审计流端点（GET，token 面）+ verify（POST，admin 面）。"""
    r = _get(w0, "/api/admin/audit?n=5", headers=w0["headers"])
    assert r.status_code == 200
    assert "events" in r.json()
    r = httpx.post(w0["url"] + "/api/admin/audit/verify", timeout=30,
                   headers=w0["headers"])
    assert r.status_code == 200
    assert "problems" in r.json()


# ---------------------------------------------------------------- 熔断解除（产品化补）

def test_kill_all_clear_only_unlocks_kill_locks():
    """解除熔断：kill 锁解开、蜜罐锁保留、KILL_ALL 标记删除。"""
    from loadn_webui import db as db_mod
    from loadn_webui.api import routes as rt
    from loadn_webui.config import PATHS
    from loadn_webui.security import canary as canary_mod
    with db_mod.conn() as c:
        for sid in ("t-killx", "t-canaryx"):
            c.execute("INSERT OR REPLACE INTO sessions(id) VALUES(?)", (sid,))
    canary_mod.lock_session("t-killx", "kill-all（全局熔断）")
    canary_mod.lock_session("t-canaryx", "canary 命中（诱饵凭证被使用）")
    PATHS["run"].mkdir(parents=True, exist_ok=True)
    (PATHS["run"] / "KILL_ALL").write_text("x")
    out = rt.kill_all_clear()
    assert out["cleared"] is True
    assert "t-killx" in out["unlocked"]
    assert any(k["sid"] == "t-canaryx" for k in out["kept_locked"])
    assert canary_mod.is_locked("t-canaryx")      # 真警报不解
    assert not canary_mod.is_locked("t-killx")
    assert not (PATHS["run"] / "KILL_ALL").exists()


def test_admin_approvals_and_vault_readonly(w0):
    """审批清单/vault 概览端点：GET 可读（token 面），vault 响应不含明文字段。"""
    r = _get(w0, "/api/admin/approvals", headers=w0["headers"])
    assert r.status_code == 200 and "pending" in r.json()
    r = _get(w0, "/api/admin/vault", headers=w0["headers"])
    assert r.status_code == 200
    d = r.json()
    assert "platforms" in d and "verify" in d
    assert "password" not in str(d.get("platforms"))


def test_noauth_mode_admin_plane_open(w0):
    """>>> 无认证模式语义钉死：server.token 置空 = 整站免认证（含管理面写）——
    外层守卫 if token and protected 直接跳过。部署者自负外层防护；本测试
    防的是未来有人把管理面单独 fail-closed 导致空 token 模式自锁。"""
    from loadn_webui.config import CONFIG
    CONFIG.server.token = ""
    CONFIG.server.admin_token = ""
    # 管理面写操作（settings PUT）：无任何凭证头 → 不再 403/401
    r = httpx.put(w0["url"] + "/api/settings/claude",
                  json={"effort": "high"}, timeout=10)
    assert r.status_code not in (401, 403), r.text
    # 普通面读同样放行
    r2 = httpx.get(w0["url"] + "/api/sessions", timeout=10)
    assert r2.status_code == 200


# ---------------------------------------------------------------- 读面分级（AC-1.1）
# cookie 登录用户对敏感 GET（凭证/审计/姿态/外联/绑定/全量设置）要求 admin
# 角色；token 双头通道 GET 语义不变（test_admin_plane_get_reads_allowed 锁定）。

async def test_admin_read_plane_user_403(client, monkeypatch):
    """>>> 否定路径对赌：普通 cookie 用户读敏感面 → 403（GET 不再全放行）。"""
    from loadn_webui.security import userauth as ua
    monkeypatch.setattr(ua, "session_user",
                        lambda _c: {"id": 7, "role": "user", "username": "u"})
    for path in ("/api/admin/vault", "/api/admin/security", "/api/admin/audit",
                 "/api/admin/egress", "/api/admin/channels",
                 "/api/admin/approvals", "/api/admin/resources",
                 "/api/admin/target-policy", "/api/admin/system",
                 "/api/settings"):
        r = await client.get(path)
        assert r.status_code == 403, f"{path} 应要求 admin（读面分级）"


async def test_admin_read_plane_admin_ok(client, monkeypatch):
    """admin cookie 用户读敏感面放行（回归：分级只拦普通用户）。"""
    from loadn_webui.security import userauth as ua
    monkeypatch.setattr(ua, "session_user",
                        lambda _c: {"id": 1, "role": "admin", "username": "root"})
    for path in ("/api/admin/vault", "/api/settings"):
        r = await client.get(path)
        assert r.status_code == 200, path


async def test_admin_read_plane_tasks_still_scoped(client, monkeypatch):
    """>>> 防误伤对赌：/api/admin/tasks 不在敏感读清单——普通用户可读
    （属主过滤在路由内，见 test_admin_tasks.py test_owner_scope_for_non_admin）。"""
    from loadn_webui.security import userauth as ua
    monkeypatch.setattr(ua, "session_user",
                        lambda _c: {"id": 7, "role": "user", "username": "u"})
    r = await client.get("/api/admin/tasks")
    assert r.status_code == 200


async def test_admin_read_plane_token_channel_unchanged(client, monkeypatch):
    """>>> 通道语义对赌：无 cookie（token 双头）GET 敏感面仍放行——
    CLI/存量部署读路径不受读面分级影响。"""
    from loadn_webui.security import userauth as ua
    monkeypatch.setattr(ua, "session_user", lambda _c: None)
    r = await client.get("/api/admin/vault")   # client 默认带 token+admin 头
    assert r.status_code == 200
