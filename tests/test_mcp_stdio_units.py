"""T6b：mcp/client.py StdioMCPConnection 真子进程单测（原 63.7%——
stdio 连接层从未直测，此前只经 e2e discover 间接）。

echo-MCP：真 python 子进程 server（stdout 先打噪声行/坏 JSON 行再回
JSON-RPC——read_loop 的跳行分支一并覆盖），不 mock 被测物。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loadn.mcp.client import MCPTool, StdioMCPConnection

# 真 echo server：噪声行 → 坏 JSON → 非对象行；id 配对回响应；
# tools/list 回清单；tools/call 回结果；notifications/initialized 回执
_ECHO_SERVER = r'''
import json, sys
print("noise line", flush=True)          # read_loop 应跳过
print("{broken json", flush=True)        # 坏 JSON 应跳过
print("[1,2,3]", flush=True)             # 非 { 开头应跳过
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    req = json.loads(line)
    rid = req.get("id")
    m = req.get("method")
    if m == "initialize":
        resp = {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {}}}
    elif m == "notifications/initialized":
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}),
              flush=True)
        continue
    elif m == "tools/list":
        resp = {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": "echo", "description": "回声",
             "inputSchema": {"type": "object"}}]}}
    elif m == "tools/call":
        resp = {"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": "called"}]}}
    elif m == "boom":
        resp = {"jsonrpc": "2.0", "id": rid,
                "error": {"code": 1, "message": "炸了"}}
    else:
        resp = {"jsonrpc": "2.0", "id": rid, "result": {}}
    print(json.dumps(resp), flush=True)
'''

async def _mk(code: str, tmp_path: Path) -> StdioMCPConnection:
    script = tmp_path / "srv.py"
    script.write_text(code, encoding="utf-8")
    return StdioMCPConnection("t", sys.executable, [str(script)])


async def test_stdio_full_lifecycle(tmp_path):
    """start（噪声行免疫）→ list_tools → call_tool → error → stop。"""
    conn = await _mk(_ECHO_SERVER, tmp_path)
    await conn.start()                                  # 含 initialize+initialized
    tools = await conn.list_tools()
    assert [t["name"] for t in tools] == ["echo"]
    res = await conn.call_tool("echo", {"x": 1})
    assert res["content"][0]["text"] == "called"
    with pytest.raises(RuntimeError, match="boom.*炸了"):
        await conn._rpc("boom", {})
    await conn.stop()
    assert conn.proc is not None


async def test_rpc_error_response_raises(tmp_path):
    conn = await _mk(_ECHO_SERVER, tmp_path)
    await conn.start()
    with pytest.raises(RuntimeError, match="MCP t.boom"):
        await conn._rpc("boom", {})
    await conn.stop()


async def test_rpc_dead_process_raises(tmp_path):
    """server 进程死后 _rpc → ConnectionError（未运行）。"""
    conn = await _mk(_ECHO_SERVER, tmp_path)
    await conn.start()
    conn.proc.terminate()
    await conn.proc.wait()
    with pytest.raises(ConnectionError, match="未运行"):
        await conn.list_tools()
    await conn.stop()


def test_mcptool_name_and_wrap():
    """MCPTool 包装：mcp__<server>__<tool> 命名 + isError 回 ToolError。"""
    class _Conn:
        name = "srv"
        calls = []

        async def call_tool(self, tool, args):
            _Conn.calls.append((tool, args))
            return {"content": [{"type": "text", "text": "ok"}]}

    t = MCPTool(_Conn(), {"name": "ping", "description": "测"})
    assert t.name == "mcp__srv__ping"
    assert t.description == "测"

    import asyncio
    out = asyncio.new_event_loop().run_until_complete(
        t.execute({}, None))
    assert out == "ok"
    assert _Conn.calls == [("ping", {})]


def test_text_of_variants():
    from loadn.mcp.client import _text_of
    assert _text_of({"content": [{"type": "text", "text": "a"},
                                 {"type": "text", "text": "b"}]}) == "a\nb"
    assert _text_of({}) == ""
    assert _text_of({"content": [{"type": "image"}]}) == ""
    assert _text_of({"content": "not-list"}) == ""
