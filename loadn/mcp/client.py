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


class MCPTool(Tool):
    """远端 MCP 工具的本地包装（mcp__<server>__<tool>）。"""

    def __init__(self, conn: StdioMCPConnection, spec: dict) -> None:
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


async def discover(cwd: Path) -> tuple[dict[str, Tool], list[StdioMCPConnection]]:
    """启动 .mcp.json 里全部 server 并列工具。单个失败降级警告，不炸会话。"""
    tools: dict[str, Tool] = {}
    conns: list[StdioMCPConnection] = []
    for name, conf in load_mcp_config(cwd).items():
        if not isinstance(conf, dict) or not conf.get("command"):
            continue
        if str(conf.get("type", "stdio")) != "stdio":
            log.warning("MCP %s 非 stdio 类型（%s），暂不支持，跳过", name, conf.get("type"))
            continue
        conn = StdioMCPConnection(name, conf["command"],
                                  list(conf.get("args") or []),
                                  conf.get("env") or {})
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
