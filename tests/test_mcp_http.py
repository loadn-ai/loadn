"""P1-1a MCP streamable-HTTP transport 验收（httpx.MockTransport，零真请求）。

- initialize 握手 + Mcp-Session-Id 回带 + initialized 通知
- JSON 与 SSE 两种响应形态的 list/call
- initialize 250ms/1000ms 两档重试（第三次成功 / 三连失败降级）
- discover()：type=http 配置接线成 mcp__<server>__<tool>；坏 server 跳过
"""
from __future__ import annotations

import json

import httpx
import pytest

from loadn.mcp.client import HTTPMCPConnection, discover


def _mock(handler, session_id="sess-1"):
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(wrapped))
    return client, calls


def _handler_json(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content)
    method = body.get("method")
    if method == "initialize":
        return httpx.Response(200, headers={"Mcp-Session-Id": "sess-1"},
                              json={"jsonrpc": "2.0", "id": body["id"],
                                    "result": {"protocolVersion": "2025-06-18",
                                               "serverInfo": {"name": "fake"}}})
    if method == "tools/list":
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"tools": [{"name": "echo",
                                  "description": "回声",
                                  "inputSchema": {"type": "object"}}]}})
    if method == "tools/call":
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"content": [{"type": "text",
                                    "text": "hi " + json.dumps(
                                        body["params"]["arguments"])}]}})
    return httpx.Response(202)          # notifications → 202


# ---------------------------------------------------------------- 握手与调用
async def test_initialize_session_and_json_roundtrip():
    client, calls = _mock(_handler_json)
    conn = HTTPMCPConnection("fake", "https://mcp.example/mcp", client=client)
    await conn.start()
    assert conn._session_id == "sess-1"
    tools = await conn.list_tools()
    assert tools[0]["name"] == "echo"
    out = await conn.call_tool("echo", {"x": 1})
    assert "hi" in out["content"][0]["text"]
    # 后续请求回带会话头
    later = [c for c in calls if c.headers.get("Mcp-Session-Id") == "sess-1"]
    assert len(later) >= 3               # list + call + initialized 通知
    await conn.stop()


async def test_sse_response_shape():
    """SSE 形态：data: 行携带 JSON-RPC，取 id 匹配消息。"""
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {}})
        if body.get("method") == "tools/list":
            noise = json.dumps({"jsonrpc": "2.0", "id": "other"})
            match = json.dumps({"jsonrpc": "2.0", "id": body["id"],
                                "result": {"tools": [{"name": "s"}]}})
            events = (": ping\n\nevent: message\n"
                      f"data: {noise}\n\ndata: {match}\n\n")
            return httpx.Response(200,
                                  headers={"Content-Type": "text/event-stream"},
                                  text=events)
        return httpx.Response(202)

    client, _ = _mock(handler)
    conn = HTTPMCPConnection("fake", "https://mcp.example/mcp", client=client)
    await conn.start()
    assert (await conn.list_tools())[0]["name"] == "s"
    await conn.stop()


# ---------------------------------------------------------------- 重试
async def test_initialize_retry_backoff_then_success():
    """两档退避（250/1000ms）后第三次成功——codex 两档同构。"""
    state = {"n": 0}
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if body.get("method") == "initialize":
            state["n"] += 1
            if state["n"] < 3:
                return httpx.Response(500)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {}})
        return httpx.Response(202)

    client, _ = _mock(handler)
    conn = HTTPMCPConnection("fake", "https://mcp.example/mcp",
                             client=client, sleep=fake_sleep)
    await conn.start()
    assert state["n"] == 3
    assert slept == [0.25, 1.0]          # 两档退避精确值
    await conn.stop()


async def test_initialize_gives_up_after_two_retries():
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    client, _ = _mock(lambda r: httpx.Response(503))
    conn = HTTPMCPConnection("fake", "https://mcp.example/mcp",
                             client=client, sleep=fake_sleep)
    with pytest.raises(ConnectionError, match="initialize 失败"):
        await conn.start()
    assert slept == [0.25, 1.0]
    await conn.stop()


# ---------------------------------------------------------------- discover
async def test_discover_http_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {
            "remote": {"type": "http", "url": "https://mcp.example/mcp"},
            "broken": {"type": "http", "url": "https://down.example/mcp"}}}),
        encoding="utf-8")
    real_init = HTTPMCPConnection.start

    async def patched_start(self):
        if "down" in self.url:
            raise ConnectionError("down")
        return await real_init(self)

    monkeypatch.setattr(HTTPMCPConnection, "start", patched_start)
    # broken 走 MockTransport 拦不住（URL 不同）——改为直接注 fake client
    import loadn.mcp.client as mc

    def factory(name, url, headers=None, client=None, sleep=None,
                oauth_conf=None):
        c, _ = _mock(_handler_json)
        return HTTPMCPConnection(name, url, headers=headers, client=c)

    monkeypatch.setattr(mc, "HTTPMCPConnection", factory)
    # broken 仍需走失败路径：恢复真 start 的 patched 版对 factory 产物生效
    tools, conns = await discover(tmp_path)
    assert "mcp__remote__echo" in tools
    assert len(conns) == 1
    for c in conns:
        await c.stop()


async def test_discover_unknown_type_skipped(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps(
        {"mcpServers": {"weird": {"type": "grpc"}}}), encoding="utf-8")
    tools, conns = await discover(tmp_path)
    assert tools == {} and conns == []
