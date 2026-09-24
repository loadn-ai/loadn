"""MCP HTTP OAuth（P1-1b，codex oauth 三件同构）。

401 → 解析 WWW-Authenticate（Bearer resource_metadata，RFC 9728）→ AS
元数据（RFC 8414）→ 动态客户端注册（RFC 7591，server 支持时）→ PKCE
S256 授权码流（本机回环 redirect 捕获 code）→ token 交换 → 存储 →
Bearer 重试。

- 审批门（卡面④，headless 无弹窗=fail-closed）：默认不放行——
  `.mcp.json` 的 `oauth.preapproved=true`（用户显式预授权=同意工件）或
  注入 approve_fn（平台审批卡的桥接点，webui 集成随后续卡接）才走流。
  打开的授权 URL 会经 open_url 抛出，绝不静默发起。
- token 存储：引擎侧 `$LOADN_HOME/mcp_tokens.json`（0600 原子写）。webui
  vault（W3）在另一进程不可达（进程边界铁律）——平台形态的 vault 适配
  随 webui 集成卡补，接口（token_store 注入）已留。
- 状态校验：state 随机 + 回调比对（CSRF）；verifier 仅存内存。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path

import httpx

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)

CALLBACK_TIMEOUT_S = 300


def parse_www_authenticate(header: str) -> dict:
    """Bearer challenge 参数（k="v", k2="v2"）→ dict（codex 同构简化）。

    首段的 scheme 前缀（`Bearer `）剥掉再取参。
    """
    out: dict = {}
    parts = (header or "").split(",")
    if parts and parts[0].strip().lower().startswith("bearer "):
        parts[0] = parts[0].strip()[len("bearer "):]
    for part in parts:
        k, sep, v = part.partition("=")
        if sep:
            out[k.strip().lower()] = v.strip().strip('"')
    return out


class FileTokenStore:
    """0600 原子 JSON（$LOADN_HOME/mcp_tokens.json）——server url → token。"""

    def __init__(self, path: Path | None = None):
        self.path = path or (loadn_home() / "mcp_tokens.json")

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, url: str) -> str | None:
        return (self.load().get(url) or {}).get("access_token") \
            if isinstance(self.load().get(url), dict) else None

    def put(self, url: str, token: str, refresh: str | None = None) -> None:
        data = self.load()
        data[url] = {"access_token": token, **({"refresh_token": refresh}
                                               if refresh else {})}
        self.save(data)


def _pkce() -> tuple[str, str]:
    """(verifier, challenge)——S256。"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)) \
        .decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


class OAuthFlow:
    """单次授权码流。client 注入（MockTransport 可测）；回调走真回环 socket。"""

    def __init__(self, client: httpx.AsyncClient, *,
                 approve_fn=None, open_url=None,
                 token_store: FileTokenStore | None = None,
                 sleep=asyncio.sleep):
        self._client = client
        self._approve_fn = approve_fn      # (authorize_url) -> bool；None=看 preapproved
        self._open_url = open_url          # (url) -> None；默认 log
        self.store = token_store or FileTokenStore()
        self._sleep = sleep

    async def obtain(self, resource_url: str,
                     challenge_header: str) -> str:
        """完整流；返回 access_token（并已入 store）。"""
        params = parse_www_authenticate(challenge_header)
        meta_url = params.get("resource_metadata")
        if not meta_url:
            raise ConnectionError("401 挑战缺 resource_metadata（非 MCP OAuth 形态）")
        # ① 资源元数据 → authorization_servers
        res_meta = (await self._client.get(meta_url)).raise_for_status().json()
        auth_server = (res_meta.get("authorization_servers") or [""])[0]
        if not auth_server:
            raise ConnectionError("资源元数据缺 authorization_servers")
        # ② AS 元数据
        as_meta = (await self._client.get(
            auth_server.rstrip("/") + "/.well-known/oauth-authorization-server"
        )).raise_for_status().json()
        auth_ep = as_meta.get("authorization_endpoint")
        token_ep = as_meta.get("token_endpoint")
        if not auth_ep or not token_ep:
            raise ConnectionError("AS 元数据缺 authorization/token endpoint")
        # ③ 动态客户端注册（server 支持时；否则匿名公共客户端）
        client_id, client_secret = "loadn-mcp", None
        reg_ep = as_meta.get("registration_endpoint")
        if reg_ep:
            reg = (await self._client.post(reg_ep, json={
                "client_name": "loadn",
                "redirect_uris": [],        # 占位——真实 redirect_uris 回环端口
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            })).raise_for_status().json()
            client_id = reg.get("client_id") or client_id
            client_secret = reg.get("client_secret")
        # ④ 回环回调监听 + PKCE + state
        got = asyncio.get_running_loop().create_future()

        async def _handle(reader, writer):
            try:
                line = (await reader.readline()).decode(errors="replace")
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                from urllib.parse import parse_qs, urlsplit
                q = parse_qs(urlsplit(line.split(" ")[1] if " " in line
                                       else "/").query)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n"
                             b"Connection: close\r\n\r\nloadn!")
                await writer.drain()
                if q.get("state", [""])[0] == state and not got.done():
                    got.set_result(q.get("code", [None])[0])
                elif not got.done():
                    got.set_result(None)
            except Exception:                              # noqa: BLE001
                pass
            finally:
                try:
                    writer.close()
                except OSError:
                    pass

        server = await asyncio.start_server(_handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        redirect_uri = f"http://127.0.0.1:{port}/cb"
        verifier, challenge = _pkce()
        state = secrets.token_urlsafe(16)
        try:
            from urllib.parse import urlencode
            authorize_url = auth_ep + "?" + urlencode({
                "response_type": "code", "client_id": client_id,
                "redirect_uri": redirect_uri, "state": state,
                "code_challenge": challenge, "code_challenge_method": "S256"})
            # ⑤ 审批门（fail-closed）：不静默发起
            allowed = self._approve_fn(authorize_url) \
                if self._approve_fn is not None else False
            if not allowed:
                raise ConnectionError(
                    "MCP OAuth 需用户批准（headless fail-closed）：在 .mcp.json "
                    "该 server 的 oauth.preapproved=true，或经平台审批后重试；"
                    f"授权地址：{authorize_url}")
            (self._open_url or (lambda u: log.info("MCP OAuth 授权地址：%s", u)))(
                authorize_url)
            code = await asyncio.wait_for(got, CALLBACK_TIMEOUT_S)
            if not code:
                raise ConnectionError("回调缺 code/state 不符（CSRF 防护拒收）")
            # ⑥ token 交换
            form = {"grant_type": "authorization_code", "code": code,
                    "redirect_uri": redirect_uri, "client_id": client_id,
                    "code_verifier": verifier}
            if client_secret:
                form["client_secret"] = client_secret
            tok = (await self._client.post(token_ep, data=form)) \
                .raise_for_status().json()
            access = tok.get("access_token")
            if not access:
                raise ConnectionError("token 端点未返回 access_token")
            self.store.put(resource_url, access, tok.get("refresh_token"))
            return access
        finally:
            server.close()
            await server.wait_closed()
