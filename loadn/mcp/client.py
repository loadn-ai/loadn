"""MCPLayer（工程详设 §4.10）：stdio JSON-RPC + streamable-http 备用。

发现：cwd/.mcp.json（业界工作区标准格式 {"mcpServers": {name:
{command, args, env}}}）。工具注册名 mcp__<server>__<tool>（与 claude CLI
同形）。初始化失败降级：警告不阻断会话。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from loadn.tools.base import Tool, ToolContext, ToolError
from loadn.util import get_logger

log = get_logger(__name__)

_MCP_PROTOCOL = "2025-06-18"
_RPC_ID = [0]


def load_mcp_config(cwd: Path) -> dict:
    """读 cwd/.mcp.json 的 mcpServers（缺文件 = 空）。"""
    p = Path(cwd) / ".mcp.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        servers = (data or {}).get("mcpServers") or {}
        return servers if isinstance(servers, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


class StdioMCPConnection:
    """单 server 的 stdio JSON-RPC 连接（request/response 按 id 配对）。"""

    def __init__(self, name: str, command: str, args: list[str],
                 env: dict | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            self.command, *self.args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, **self.env}, start_new_session=True)
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._rpc("initialize", {
            "protocolVersion": _MCP_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "loadn", "version": "0.1.0"}})
        await self._rpc("notifications/initialized", None)   # 通知无响应

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                raw = await self.proc.stdout.readline()
            except Exception:  # noqa: BLE001
                break
            if not raw:
                break
            line = raw.decode(errors="replace").strip()
            if not line.startswith("{"):
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = msg.get("id")
            fut = self._pending.pop(rid, None) if rid is not None else None
            if fut is not None and not fut.done():
                fut.set_result(msg)

    async def _rpc(self, method: str, params: dict | None) -> dict:
        if self.proc is None or self.proc.returncode is not None:
            raise ConnectionError(f"MCP server {self.name} 未运行")
        _RPC_ID[0] += 1
        rid = _RPC_ID[0]
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        assert self.proc.stdin
        self.proc.stdin.write((json.dumps(req) + "\n").encode())
        await self.proc.stdin.drain()
        try:
            resp = await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise TimeoutError(f"MCP {self.name}.{method} 30s 无响应") from None
        if "error" in resp:
            raise RuntimeError(f"MCP {self.name}.{method}: {resp['error']}")
        return resp.get("result") or {}

    async def list_tools(self) -> list[dict]:
        res = await self._rpc("tools/list", {})
        return res.get("tools") or []

    async def call_tool(self, tool: str, args: dict) -> dict:
        return await self._rpc("tools/call", {"name": tool, "arguments": args})

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), 3)
            except Exception:  # noqa: BLE001
                self.proc.kill()


class HTTPMCPConnection:
    """streamable-HTTP MCP server 连接（P1-1a，codex rmcp 同构）。

    协议：POST JSON-RPC 到单端点，Accept: application/json, text/event-stream；
    响应两种形态——纯 JSON，或 SSE 流（`data:` 行携带 JSON-RPC 消息，取
    id 匹配的一条）。initialize 响应带 `Mcp-Session-Id` 头则后续请求回带
    （2025-06-18 语义）。httpx.AsyncClient（单依赖红线内）。

    初始化重试：250ms/1000ms 两档退避（codex STREAMABLE_HTTP_RETRY_DELAYS_MS
    同构）——HTTP 通道抖动常见，重试两档仍失败才降级跳过。
    """

    RETRY_DELAYS_S = (0.25, 1.0)
    RPC_TIMEOUT_S = 60.0

    def __init__(self, name: str, url: str, headers: dict | None = None,
                 client: httpx.AsyncClient | None = None,
                 sleep=asyncio.sleep, oauth_conf: dict | None = None) -> None:
        self.name = name
        self.url = url
        self.headers = dict(headers or {})
        self.oauth_conf = dict(oauth_conf or {})   # P1-1b：preapproved 等
        self._client = client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = client is None
        self._session_id: str | None = None
        self._sleep = sleep          # 测试注入（退避计时断言）

    async def start(self) -> None:
        """initialize（带两档重试）+ initialized 通知。"""
        payload = {
            "protocolVersion": _MCP_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "loadn", "version": "0.1.0"}}
        await self._ensure_token()                    # P1-1b：缓存 token 预挂
        last: Exception | None = None
        for attempt in range(len(self.RETRY_DELAYS_S) + 1):
            if attempt:
                await self._sleep(self.RETRY_DELAYS_S[attempt - 1])
            try:
                resp = await self._post("initialize", payload, raw=True)
                self._decode(resp, _RPC_ID[0])       # 校验非 error（丢结果体）
                self._session_id = resp.headers.get("mcp-session-id") \
                    or self._session_id
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401 and attempt == 0:
                    # 401 → OAuth 流（一次性；成功则带 Bearer 重试本轮）
                    await self._oauth_and_retry(e.response)
                    continue
                last = e
            except Exception as e:                    # noqa: BLE001
                last = e
        else:
            raise ConnectionError(
                f"MCP http {self.name} initialize 失败（重试 "
                f"{len(self.RETRY_DELAYS_S)} 档后放弃）：{last}")
        await self._post("notifications/initialized", None,
                         expect_response=False)

    async def _ensure_token(self) -> None:
        """store 里的缓存 token 预挂 Authorization（有则免一次 401 往返）。"""
        from loadn.mcp.oauth import FileTokenStore
        tok = FileTokenStore().get(self.url)
        if tok and "Authorization" not in self.headers:
            self.headers["Authorization"] = f"Bearer {tok}"

    async def _oauth_and_retry(self, response: httpx.Response) -> None:
        """401 → WWW-Authenticate → OAuth 流 → 挂 Bearer（失败抛=降级跳过）。"""
        from loadn.mcp.oauth import OAuthFlow
        challenge = response.headers.get("www-authenticate", "")
        if "resource_metadata" not in challenge:
            return                      # 非 OAuth 形态 401：留给普通重试/放弃
        preapproved = bool(self.oauth_conf.get("preapproved"))
        flow = OAuthFlow(
            self._client,
            approve_fn=(lambda url: True) if preapproved else None)
        token = await flow.obtain(self.url, challenge)
        self.headers["Authorization"] = f"Bearer {token}"

    async def _post(self, method: str, params: dict | None,
                    *, expect_response: bool = True,
                    raw: bool = False) -> httpx.Response | dict:
        _RPC_ID[0] += 1
        req: dict = {"jsonrpc": "2.0", "id": _RPC_ID[0], "method": method}
        if params is not None:
            req["params"] = params
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", **self.headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        r = await self._client.post(self.url, json=req, headers=headers)
        r.raise_for_status()
        if raw or not expect_response:
            return r
        return self._decode(r, req["id"])

    @staticmethod
    def _decode(r: httpx.Response, want_id: int) -> dict:
        """JSON 或 SSE 形态解码；SSE 取 id 匹配的首条消息。"""
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            for line in r.text.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == want_id:
                    if "error" in msg:
                        raise RuntimeError(f"MCP http error: {msg['error']}")
                    return msg.get("result") or {}
            raise TimeoutError(f"SSE 流中无 id={want_id} 的响应")
        msg = r.json()
        if msg.get("id") != want_id and "error" in msg:
            raise RuntimeError(f"MCP http error: {msg['error']}")
        if "error" in msg:
            raise RuntimeError(f"MCP http error: {msg['error']}")
        return msg.get("result") or {}

    async def _rpc(self, method: str, params: dict | None) -> dict:
        return await self._post(method, params)       # 与 stdio 同名同语义

    async def list_tools(self) -> list[dict]:
        res = await self._rpc("tools/list", {})
        return res.get("tools") or []

    async def call_tool(self, tool: str, args: dict) -> dict:
        return await self._rpc("tools/call", {"name": tool, "arguments": args})

    async def stop(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class MCPTool(Tool):
    """远端 MCP 工具的本地包装（mcp__<server>__<tool>）。"""

    def __init__(self, conn, spec: dict) -> None:
        self.conn = conn
        self.remote_name = spec.get("name") or ""
        self.name = f"mcp__{conn.name}__{self.remote_name}"
        self.description = (spec.get("description") or "")[:2000]
        self.input_schema = spec.get("inputSchema") or {"type": "object"}
        self.timeout_s = 120

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        res = await self.conn.call_tool(self.remote_name, args or {})
        if res.get("isError"):
            raise ToolError(_text_of(res)[:4000] or "MCP 工具报错")
        return _text_of(res) or "(ok)"


def _text_of(res: dict) -> str:
    parts = [c.get("text", "") for c in res.get("content") or []
             if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(p for p in parts if p)


async def discover(cwd: Path) -> tuple[dict[str, Tool], list]:
    """启动 .mcp.json 里全部 server 并列工具。单个失败降级警告，不炸会话。

    transport（P1-1a 起）：stdio（command/args/env）与 http
    （{"type":"http","url":…,"headers":…}，oauth 段会话 2 接入）。
    """
    tools: dict[str, Tool] = {}
    conns: list = []
    for name, conf in load_mcp_config(cwd).items():
        if not isinstance(conf, dict):
            continue
        stype = str(conf.get("type", "stdio" if conf.get("command") else ""))
        if stype == "http" or (stype == "streamable-http" and conf.get("url")):
            if not conf.get("url"):
                log.warning("MCP %s type=http 缺 url，跳过", name)
                continue
            conn: object = HTTPMCPConnection(
                name, str(conf["url"]),
                headers=dict(conf.get("headers") or {}),
                oauth_conf=dict(conf.get("oauth") or {}))
        elif stype == "stdio" and conf.get("command"):
            conn = StdioMCPConnection(name, conf["command"],
                                      list(conf.get("args") or []),
                                      conf.get("env") or {})
        else:
            log.warning("MCP %s 未知类型 %r（或缺 command/url），跳过",
                        name, stype or None)
            continue
        try:
            await conn.start()
            for spec in await conn.list_tools():
                t = MCPTool(conn, spec)
                tools[t.name] = t
            conns.append(conn)
            log.info("MCP %s 就绪（%d 工具）", name, len(tools))
        except Exception as e:  # noqa: BLE001 — 初始化失败降级
            log.warning("MCP %s 初始化失败（跳过）：%s", name, e)
            await conn.stop()
    return tools, conns
