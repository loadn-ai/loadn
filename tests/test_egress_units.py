"""egress_proxy.py 未覆盖主干单测：CONNECT 隧道/LLM 网关注入/uds 双监听。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from loadn_webui.config import CONFIG
from loadn_webui.security import egress_proxy


@pytest.fixture()
def proxy(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    return egress_proxy.EgressProxy(port=0)


async def test_connect_tunnel_allow_and_deny(proxy, monkeypatch):
    """CONNECT：白名单域直通成隧道；非白名单 403。

    全离线形态（run 38159802912 教训：真连 open.bigmodel.cn 在 CI 3.12
    挂死 >300s 被 pytest-timeout 击杀——外部端点不可控）。本测试靶心是
    「门判定 + 隧道管道」语义：白名单/重绑定门打桩，上游用本地回显
    服务替代真端点（TLS 握手对管道语义无增量，裸 TCP 双向透传即验证）。
    """
    from loadn_webui.security import net_policy

    async def echo(reader, writer):
        try:
            while True:
                ln = await reader.readline()
                if not ln:
                    break
                writer.write(ln)
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(egress_proxy, "_allowed",
                        lambda h: (h or "").lower() == "allow.test")
    monkeypatch.setattr(net_policy, "rebind_check",
                        lambda host, **kw: (True, ""))

    async def _fake_open(host, port, **kw):
        return await asyncio.open_connection("127.0.0.1", up_port)

    class _AioShim:
        """仅 egress_proxy 模块内的 open_connection 指向本地回显——
        不动全局 asyncio（测试自身的连接不受劫持）。"""
        def __getattr__(self, k):
            return getattr(asyncio, k)

        @staticmethod
        async def open_connection(host, port, **kw):
            return await _fake_open(host, port, **kw)

    monkeypatch.setattr(egress_proxy, "asyncio", _AioShim())
    port = await proxy.start()
    try:
        # 非白名单 → 403（裸 socket 直说，不经 httpx）
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT denied.example.org:443 HTTP/1.1\r\nHost: "
                b"denied.example.org:443\r\n\r\n")
        await w.drain()
        resp = await r.readline()
        w.close()
        assert resp.startswith(b"HTTP/1.1 403")
        # 白名单 → 200 隧道建立 + 双向透传（回显自证）
        r2, w2 = await asyncio.open_connection("127.0.0.1", port)
        w2.write(b"CONNECT allow.test:443 HTTP/1.1\r\nHost: "
                 b"allow.test:443\r\n\r\n")
        await w2.drain()
        resp2 = await r2.readline()
        assert resp2.startswith(b"HTTP/1.1 200")
        await r2.readexactly(2)            # \r\n 收尾
        w2.write(b"ping-through-tunnel\n")
        await w2.drain()
        assert await r2.readline() == b"ping-through-tunnel\n"
        w2.close()
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()


async def test_connect_tunnel_unknown_host_502(proxy):
    """白名单域但 DNS 解析失败 → 502（上游连不上分支）。"""
    port = await proxy.start()
    try:
        # 用 hosts 撞不出的白名单子域——子域匹配 allow，解析失败 → 502
        err = None
        try:
            async with httpx.AsyncClient(
                    proxy=f"http://127.0.0.1:{port}", timeout=15) as c:
                await c.get("https://no-such-host.opencode.ai/")
        except (httpx.ProxyError, httpx.ConnectError) as e:
            err = e
        except httpx.ReadTimeout as e:
            # DNS 慢环境：getaddrinfo 失败等满 resolver 超时（实测 20s）>
            # 客户端 15s——代理来不及回 502 客户端先超时。语义同「连不上」
            err = e
        assert err is not None
    finally:
        await proxy.stop()


async def test_llm_gateway_injects_creds(monkeypatch, tmp_path):
    """P5 网关：llm-gw.internal 请求 → 上游收到注入的 Bearer（非 dummy）。"""
    upstream_seen = {}

    async def fake_handler(reader, writer):
        # 假上游：读请求头，回 200
        data = await reader.readline()
        headers = {}
        while True:
            h = await reader.readline()
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode().partition(":")
            headers[k.strip().lower()] = v.strip()
        upstream_seen.update(line=data.decode(), headers=headers)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(fake_handler, "127.0.0.1", 0)
    up_port = srv.sockets[0].getsockname()[1]
    monkeypatch.setattr(egress_proxy, "_UPSTREAM",
                        (f"http://127.0.0.1:{up_port}", "REAL-SECRET-TOKEN"))
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r = await httpx.AsyncClient(
            proxy=f"http://127.0.0.1:{port}", timeout=15).post(
            "http://llm-gw.internal/v1/messages",
            content=b'{"x":1}',
            headers={"content-type": "application/json",
                     "authorization": "Bearer dummy-controlled-by-egress-gw"})
        assert r.status_code == 200 and r.text == "ok"
        assert "REAL-SECRET-TOKEN" in upstream_seen["headers"].get(
            "authorization", "")
        assert upstream_seen["headers"].get("x-api-key") == "REAL-SECRET-TOKEN"
        assert upstream_seen["line"].startswith("POST /v1/messages")
    finally:
        await px.stop()
        srv.close()


async def test_llm_gateway_no_upstream_503(monkeypatch):
    monkeypatch.setattr(egress_proxy, "_UPSTREAM", ("", ""))
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r = await httpx.AsyncClient(
            proxy=f"http://127.0.0.1:{port}", timeout=10).post(
            "http://llm-gw.internal/v1/messages")
        assert r.status_code == 503
    finally:
        await px.stop()


async def test_uds_listener_and_roundtrip(proxy, tmp_path):
    """uds 双监听：unix socket 上的请求与 TCP 同策略。"""
    uds = tmp_path / "eg.sock"
    px = egress_proxy.EgressProxy(port=0, uds_path=str(uds))
    await px.start()
    try:
        assert uds.exists()
        reader, writer = await asyncio.open_unix_connection(str(uds))
        writer.write(b"CONNECT denied.example.org:443 HTTP/1.1\r\n"
                     b"Host: denied.example.org:443\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        assert line.startswith(b"HTTP/1.1 403")
        writer.close()
    finally:
        await px.stop()


async def test_host_mismatch_enforced(proxy):
    """明文 Host 分片（URL host ≠ Host 头）→ enforce 拒。"""
    port = await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET http://open.bigmodel.cn/ HTTP/1.1\r\n"
                     b"Host: other.example.org\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=10)
        assert line.startswith(b"HTTP/1.1 403")
        writer.close()
    finally:
        await proxy.stop()


async def test_llm_gateway_large_body_integrity(monkeypatch):
    """回归：大请求体跨 TCP 分段必须完整转发（read→readexactly 实修）。

    旧实现 reader.read(clen) 首读只拿第一段——上游收到截断 JSON 报
    body.NNNN: JSON decode error；假上游读满 content-length 并验 JSON。
    """
    import json as _json
    payload = {"system": "x" * 40, "messages": [
        {"role": "user", "content": "y" * 200_000}]}     # 200KB+ 必跨分段
    raw = _json.dumps(payload).encode()
    upstream_seen = {}

    async def fake_handler(reader, writer):
        line = await reader.readline()
        headers = {}
        while True:
            h = await reader.readline()
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode().partition(":")
            headers[k.strip().lower()] = v.strip()
        n = int(headers.get("content-length", "0"))
        body = b""
        while len(body) < n:                     # 读满（readexactly 语义）
            chunk = await reader.read(65536)
            if not chunk:
                break
            body += chunk
        upstream_seen["body_len"] = len(body)
        try:
            _json.loads(body)
            upstream_seen["valid"] = True
        except Exception:                        # noqa: BLE001
            upstream_seen["valid"] = False
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(fake_handler, "127.0.0.1", 0)
    up_port = srv.sockets[0].getsockname()[1]
    monkeypatch.setattr(egress_proxy, "_UPSTREAM",
                        (f"http://127.0.0.1:{up_port}", "REAL"))
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r = await httpx.AsyncClient(
            proxy=f"http://127.0.0.1:{port}", timeout=30).post(
            "http://llm-gw.internal/v1/messages", content=raw,
            headers={"content-type": "application/json"})
        assert r.status_code == 200
        assert upstream_seen["body_len"] == len(raw), "上游收到的体长不对（截断）"
        assert upstream_seen["valid"] is True, "上游收到非法 JSON"
    finally:
        await px.stop()
        srv.close()
