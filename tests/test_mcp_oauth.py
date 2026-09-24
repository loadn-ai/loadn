"""P1-1b MCP OAuth 验收（mock 授权端点 + 真回环回调 socket，零外网）。

- 全流：401 挑战 → RFC9728 资源元数据 → AS 元数据 → 动态注册 →
  PKCE S256 授权 URL →（测试以真实 HTTP GET 打回环回调）→ token 交换 →
  Bearer 重试 initialize 成功
- token 只在 store（0600）；二次连接命中缓存不再走流
- headless 审批门 fail-closed：未 preapproved → 拒，绝不发起授权
- WWW-Authenticate 解析
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from loadn.mcp.client import HTTPMCPConnection
from loadn.mcp.oauth import FileTokenStore, parse_www_authenticate

MCP_URL = "https://mcp.example/mcp"
CHALLENGE = ('Bearer realm="mcp", resource_metadata='
             '"https://mcp.example/.well-known/oauth-protected-resource"')


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> FileTokenStore:
    st = FileTokenStore(tmp_path / "mcp_tokens.json")
    return st


# ---------------------------------------------------------------- 解析
def test_parse_www_authenticate():
    p = parse_www_authenticate(CHALLENGE)
    assert p["realm"] == "mcp"
    assert p["resource_metadata"].endswith("oauth-protected-resource")
    assert parse_www_authenticate("") == {}


# ---------------------------------------------------------------- 全流
async def test_full_oauth_flow_and_bearer_retry(tmp_path, monkeypatch):
    import loadn.mcp.oauth as oauth_mod
    store = FileTokenStore(tmp_path / "tokens.json")
    monkeypatch.setattr(oauth_mod, "FileTokenStore",
                        lambda path=None: store)
    auth_seen: list[str] = []

    def open_url(url):             # 「打开浏览器」= 记录 + 线程内模拟用户到达
        import threading
        auth_seen.append(url)
        q = parse_qs(urlsplit(url).query)
        redirect = q["redirect_uri"][0]
        sep = "&" if "?" in redirect else "?"

        def hit():
            try:
                httpx.get(f"{redirect}{sep}code=CODE1&state={q['state'][0]}",
                          timeout=5)
            except Exception:                              # noqa: BLE001
                pass
        threading.Thread(target=hit, daemon=True).start()

    def make_client():
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "oauth-protected-resource" in path:
                return httpx.Response(200, json={
                    "authorization_servers": ["https://as.example"]})
            if path == "/.well-known/oauth-authorization-server":
                return httpx.Response(200, json={
                    "authorization_endpoint": "https://as.example/authorize",
                    "token_endpoint": "https://as.example/token",
                    "registration_endpoint": "https://as.example/register"})
            if path == "/register":
                return httpx.Response(200, json={"client_id": "cid-1"})
            if path == "/token":
                form = parse_qs(request.content.decode())
                assert form["code"] == ["CODE1"]
                assert "code_challenge_method" not in form  # challenge 只进 authorize URL
                assert form["code_verifier"]
                return httpx.Response(200, json={"access_token": "tok-XYZ"})
            body = json.loads(request.content)
            if request.headers.get("authorization") == "Bearer tok-XYZ":
                return httpx.Response(200, json={
                    "jsonrpc": "2.0", "id": body.get("id"), "result": {}})
            return httpx.Response(401,
                                  headers={"WWW-Authenticate": CHALLENGE})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    client = make_client()
    conn = HTTPMCPConnection("remote", MCP_URL, client=client,
                             oauth_conf={"preapproved": True})
    # open_url/approve 注入：把 OAuthFlow 的 open_url 换成上面的模拟器
    real_flow = oauth_mod.OAuthFlow

    class PatchedFlow(real_flow):
        def __init__(self, c, **kw):
            kw.setdefault("open_url", open_url)
            super().__init__(c, **kw)

    monkeypatch.setattr(oauth_mod, "OAuthFlow", PatchedFlow)   # _oauth_and_retry 的取用 seam
    monkeypatch.setattr(oauth_mod, "CALLBACK_TIMEOUT_S", 15)
    await conn.start()
    # 授权 URL 携带 PKCE S256 与 state
    q = parse_qs(urlsplit(auth_seen[0]).query)
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["state"]
    # token 只在 store（0600）
    data = json.loads((tmp_path / "tokens.json").read_text())
    assert data[MCP_URL]["access_token"] == "tok-XYZ"
    assert (tmp_path / "tokens.json").stat().st_mode & 0o777 == 0o600
    await conn.stop()


async def test_cached_token_skips_flow(tmp_path, monkeypatch):
    import loadn.mcp.oauth as oauth_mod
    store = FileTokenStore(tmp_path / "tokens.json")
    store.put(MCP_URL, "tok-CACHED")
    monkeypatch.setattr(oauth_mod, "FileTokenStore", lambda path=None: store)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer tok-CACHED"
        body = json.loads(request.content)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body.get("id"), "result": {}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    conn = HTTPMCPConnection("remote", MCP_URL, client=client)
    await conn.start()                     # 无 401：缓存 token 直接过
    await conn.stop()


async def test_not_preapproved_fail_closed(tmp_path, monkeypatch):
    import loadn.mcp.oauth as oauth_mod
    store = FileTokenStore(tmp_path / "tokens.json")
    monkeypatch.setattr(oauth_mod, "FileTokenStore", lambda path=None: store)
    flow_attempts = {"n": 0}

    class Guard(oauth_mod.OAuthFlow):
        async def obtain(self, *a, **k):
            flow_attempts["n"] += 1
            return await super().obtain(*a, **k)

    monkeypatch.setattr(oauth_mod, "OAuthFlow", Guard)   # 取用 seam

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "oauth-protected-resource" in path:
            return httpx.Response(200, json={
                "authorization_servers": ["https://as.example"]})
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "authorization_endpoint": "https://as.example/authorize",
                "token_endpoint": "https://as.example/token",
                "registration_endpoint": "https://as.example/register"})
        if path == "/register":
            return httpx.Response(200, json={"client_id": "cid-1"})
        return httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    conn = HTTPMCPConnection("remote", MCP_URL, client=client,
                             oauth_conf={})            # 未预授权
    with pytest.raises(ConnectionError, match="需用户批准"):
        await conn.start()
    # OAuth 流被走到审批门即拒（fail-closed），store 无 token
    assert flow_attempts["n"] == 1
    assert store.load() == {}
    await conn.stop()
