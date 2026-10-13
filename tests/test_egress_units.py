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
        # 不 wait_closed：3.12+ 语义等全部 handler 退出，隧道故意保持到
        # 双向 EOF（见 _pipe），无界等价挂死（stop 同因已加 2s 上限）


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


# ------------------------------------------------- 隧道泄漏回归（2026-10-13
# 生产事故：job 断开后 _pipe 挂死在「双向 EOF 才收尾」，task 被 GC 销毁时
# upstream socket 无人 close——CLOSE_WAIT 堆积 ~1000 打满 fd；accept 重置
# + sqlite 打不开都是 fd 耗尽症状）。三个用例各自锁死一层收敛：
# EOF 半关 / 空闲看门狗 / 取消路径 finally close。

async def test_tunnel_client_gone_upstream_closed(proxy, monkeypatch):
    """job 断开 → 上游连接必须随之收敛（半关通知，不等上游 idle 断开）。

    对赌：旧「双向 EOF」语义下，上游（模拟 nginx keepalive 静默）不主动
    FIN，隧道永挂——上游侧 close 事件永不来，用例超时失败。
    """
    from loadn_webui.security import net_policy

    upstream_closed = asyncio.Event()

    async def silent_upstream(reader, writer):
        """模拟 nginx keepalive：收到数据不响应不主动断（挂在读上）。"""
        try:
            while True:
                ln = await reader.readline()
                if not ln:
                    break
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass
            upstream_closed.set()

    server = await asyncio.start_server(silent_upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(egress_proxy, "_allowed",
                        lambda h: (h or "").lower() == "allow.test")
    monkeypatch.setattr(net_policy, "rebind_check",
                        lambda host, **kw: (True, ""))

    async def _fake_open(host, port, **kw):
        return await asyncio.open_connection("127.0.0.1", up_port)

    class _AioShim:
        def __getattr__(self, k):
            return getattr(asyncio, k)

        @staticmethod
        async def open_connection(host, port, **kw):
            return await _fake_open(host, port, **kw)

    monkeypatch.setattr(egress_proxy, "asyncio", _AioShim())
    port = await proxy.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT allow.test:443 HTTP/1.1\r\nHost: "
                b"allow.test:443\r\n\r\n")
        await w.drain()
        assert (await r.readline()).startswith(b"HTTP/1.1 200")
        await r.readexactly(2)
        w.write(b"data\n")
        await w.drain()
        w.close()                      # job 侧直接断开
        # 半关 FIN → 上游读 EOF → 上游关连接 → 事件到。旧代码此处永挂。
        await asyncio.wait_for(upstream_closed.wait(), timeout=10)
    finally:
        await proxy.stop()
        server.close()


async def test_tunnel_idle_watchdog_reaps_dead_tunnel(proxy, monkeypatch):
    """烂对端（收到 FIN 也不关）→ 空闲看门狗收尾，egress 侧仍关 socket。

    断言点在 egress 侧的 upstream writer close 调用：对已 write_eof 的
    连接，close() 不再产生网络事件，烂对端侧无法感知——「fd 已释放」
    这一被测事实只能从持有一侧（egress）断言。
    """
    from loadn_webui.security import net_policy

    monkeypatch.setattr(egress_proxy, "_TUNNEL_IDLE_S", 0.5)
    upstream_closed = asyncio.Event()

    async def rotten_upstream(reader, writer):
        """模拟最烂对端：收到对端 FIN（read EOF）后仍挂着不关连接。"""
        while True:
            ln = await reader.readline()
            if not ln:
                break
        await asyncio.sleep(3600)      # 故意不退、不关

    server = await asyncio.start_server(rotten_upstream, "127.0.0.1", 0)
    up_port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(egress_proxy, "_allowed",
                        lambda h: (h or "").lower() == "allow.test")
    monkeypatch.setattr(net_policy, "rebind_check",
                        lambda host, **kw: (True, ""))

    async def _fake_open(host, port, **kw):
        r, w = await asyncio.open_connection("127.0.0.1", up_port)
        _orig_close = w.close

        def _record_close():
            _orig_close()
            upstream_closed.set()
        w.close = _record_close        # egress 释放 upstream socket 的时刻
        return r, w

    class _AioShim:
        def __getattr__(self, k):
            return getattr(asyncio, k)

        @staticmethod
        async def open_connection(host, port, **kw):
            return await _fake_open(host, port, **kw)

    monkeypatch.setattr(egress_proxy, "asyncio", _AioShim())
    port = await proxy.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT allow.test:443 HTTP/1.1\r\nHost: "
                b"allow.test:443\r\n\r\n")
        await w.drain()
        assert (await r.readline()).startswith(b"HTTP/1.1 200")
        await r.readexactly(2)
        w.write(b"data\n")
        await w.drain()
        w.close()
        # 半关对烂对端无效 → 看门狗 0.5s 判死 → egress finally close。
        # 旧代码挂死在 gather（上游永不 EOF）→ close 永不来 → 超时失败。
        await asyncio.wait_for(upstream_closed.wait(), timeout=10)
    finally:
        await proxy.stop()
        server.close()


async def test_pipe_cancel_still_closes_writers(proxy):
    """task 被取消（CancelledError 传播）→ 两侧 writer 仍必被 close。

    对赌：旧代码 close 循环在 gather 之后裸奔——取消异常直接炸出，
    upstream socket 泄漏（GC 销毁 task 后无人 close = CLOSE_WAIT）。
    """

    class _FakeReader:
        async def read(self, n):
            await asyncio.sleep(3600)   # 挂起等被取消
            return b""

    class _FakeWriter:
        def __init__(self):
            self.closed = False
            self.eof_written = False

        def write(self, d):
            pass

        async def drain(self):
            pass

        def can_write_eof(self):
            return True

        def write_eof(self):
            self.eof_written = True

        def close(self):
            self.closed = True

    fr1, fw1, fr2, fw2 = _FakeReader(), _FakeWriter(), _FakeReader(), \
        _FakeWriter()
    task = asyncio.create_task(proxy._pipe(fr1, fw1, fr2, fw2))
    await asyncio.sleep(0.05)           # 让 _p 两侧都挂到 read 上
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert fw1.closed and fw2.closed, "取消路径必须 close 两侧 writer"
    assert fw1.eof_written and fw2.eof_written, "取消路径应先半关再 close"
