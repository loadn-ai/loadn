"""M3：egress_proxy/egress_grants 存活变异对赌（串行复核后的真盲区）。

首轮 egress_proxy 扫描三段并发互相 checkout 冲掉变异（且 run_tests 丢了
-B/pyc 修复半截）——报告不可信，本文件收串行复核后仍存活的盲区：
- 热重载新两键 on_deny/wait_s 范围门（非法值保旧）+ yaml1.1 裸 off 归一
- 明文 HTTP 正路径：URL host=Host 头一致放行转发（分片检测不过触发/
  host 回退链/非默认端口/缺省路径/头重组去 proxy-connection）
- CONNECT 缺 :port 默认 443（int("") 炸连接 vs 可读 403）
- ensure_session_tcp 幂等（In 反转=首调 KeyError）
- grants TTL 钳制域常量（300/86400/7200 三界）+ revoke 未命中优雅 False
"""
from __future__ import annotations

import asyncio

import yaml

from loadn_webui import egress_grants, egress_proxy
from loadn_webui.config import CONFIG, ROOT


# ---------------------------------------------------------------- 热重载门
def test_hot_reload_gates_range(monkeypatch):
    """on_deny/wait_s/mode 热更范围门：合法生效（含 15 下界），非法保旧。"""
    p = ROOT / "config.yaml"
    old_mode = CONFIG.security.egress_mode
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "deny")
    monkeypatch.setattr(CONFIG.security, "egress_ask_wait_s", 60)
    try:
        def _reload(doc):
            p.write_text(yaml.safe_dump(doc))
            monkeypatch.setattr(egress_proxy, "_POLICY_MTIME", None)
            egress_proxy._maybe_reload_policy()

        # 合法值生效（wait_s 下界 15 恰好过门）
        _reload({"security": {"egress_on_deny": "ask",
                              "egress_ask_wait_s": 15}})
        assert CONFIG.security.egress_on_deny == "ask"
        assert CONFIG.security.egress_ask_wait_s == 15
        # 非法 on_deny / 越界 wait_s → 保旧值
        _reload({"security": {"egress_on_deny": "bogus",
                              "egress_ask_wait_s": 700}})
        assert CONFIG.security.egress_on_deny == "ask"
        assert CONFIG.security.egress_ask_wait_s == 15
        _reload({"security": {"egress_ask_wait_s": 14}})    # 下界外
        assert CONFIG.security.egress_ask_wait_s == 15
        _reload({"security": {"egress_ask_wait_s": 600}})   # 上界内生效
        assert CONFIG.security.egress_ask_wait_s == 600
        _reload({"security": {"egress_ask_wait_s": 601}})   # 上界外保旧
        assert CONFIG.security.egress_ask_wait_s == 600
        # yaml 1.1 裸 off → False → 归一 "off"；非法 mode 串保旧
        _reload({"security": {"egress_mode": False}})
        assert CONFIG.security.egress_mode == "off"
        _reload({"security": {"egress_mode": "bogus"}})
        assert CONFIG.security.egress_mode == "off"
    finally:
        p.unlink(missing_ok=True)
        CONFIG.security.egress_mode = old_mode
        egress_proxy._POLICY_MTIME = None


# ---------------------------------------------------------------- 明文正路径
async def test_plain_http_matching_host_forwarded(monkeypatch):
    """URL host=Host 头 → 转发：缺省路径补 /、头重组、端口取 URL、无分片误报。

    一次请求对赌五个存活位：分片判定 and→or 过触发（一致请求不得记
    deny-mismatch）/ 缺省路径 or→and / 头重组 not in 反转 / Host 键改写 /
    u.port or 80 反转（非默认端口连不上=502）。文件内须先于 mismatch 用例
    （那例会真记 deny-mismatch 行，本例审计断言只认干净窗口）。
    """
    seen: dict = {}

    async def upstream(reader, writer):
        line = await reader.readline()
        headers = {}
        while True:
            h = await reader.readline()
            if h in (b"\r\n", b"\n", b""):
                break
            k, _, v = h.decode("latin1").partition(":")
            headers[k.strip().lower()] = v.strip()
        seen["line"] = line.decode().strip()
        seen["headers"] = headers
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nok-up")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = srv.sockets[0].getsockname()[1]
    monkeypatch.setattr(CONFIG.security, "egress_mode", "off")
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET http://127.0.0.1:{up_port} HTTP/1.1\r\n"
                f"Host: 127.0.0.1\r\nUser-Agent: gate-probe\r\n"
                f"Proxy-Connection: keep-alive\r\n\r\n".encode("latin1"))
        await w.drain()
        status = await asyncio.wait_for(r.readline(), timeout=10)
        assert status.startswith(b"HTTP/1.1 200"), status
        while (await asyncio.wait_for(r.readline(), timeout=10)) \
                not in (b"\r\n", b"\n", b""):
            pass                                       # 吃掉响应头
        assert await asyncio.wait_for(r.readexactly(5), timeout=10) \
            == b"ok-up"                                # 响应体透传
        w.close()
        assert seen["line"] == "GET / HTTP/1.1"        # 无路径 URL 缺省 /
        assert seen["headers"].get("host") == "127.0.0.1"
        assert seen["headers"].get("user-agent") == "gate-probe"
        assert "proxy-connection" not in seen["headers"]  # 直通头必须剥
        # 一致请求不得记 deny-mismatch（分片检测过触发=杀手）
        import json as _json

        from loadn_webui import audit as audit_mod
        bad = [row for row in audit_mod.tail(100, "egress_request")
               if (d := _json.loads(row["detail_json"])).get("host")
               == "127.0.0.1" and d.get("decision") == "deny-mismatch"]
        assert not bad
    finally:
        await px.stop()
        srv.close()


async def test_plain_http_host_fallback_from_url(monkeypatch):
    """off 档 Host 头指向别处：转发仍按 URL host（host_hdr 回退链不得反客为主）。"""
    seen: dict = {}

    async def upstream(reader, writer):
        await reader.readline()
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        seen["ok"] = True
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = srv.sockets[0].getsockname()[1]
    monkeypatch.setattr(CONFIG.security, "egress_mode", "off")
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(f"GET http://127.0.0.1:{up_port}/ok HTTP/1.1\r\n"
                f"Host: unreachable.invalid\r\n\r\n".encode("latin1"))
        await w.drain()
        status = await asyncio.wait_for(r.readline(), timeout=10)
        assert status.startswith(b"HTTP/1.1 200"), status
        w.close()
        assert seen.get("ok") is True
    finally:
        await px.stop()
        srv.close()


# ---------------------------------------------------------------- CONNECT
async def test_connect_without_port_still_gates(monkeypatch):
    """CONNECT 缺 :port → 默认 443，判定先行——非白名单给可读 403 而非炸连接。"""
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT denied.example.org HTTP/1.1\r\n\r\n")
        await w.drain()
        line = await asyncio.wait_for(r.readline(), timeout=10)
        assert line.startswith(b"HTTP/1.1 403"), line
        w.close()
    finally:
        await px.stop()


# ---------------------------------------------------------------- 会话 TCP
async def test_ensure_session_tcp_idempotent():
    """首调建/次调复用同端口，注册表单条（In 反转=首调 KeyError）。"""
    px = egress_proxy.EgressProxy(port=0)
    try:
        p1 = await px.ensure_session_tcp("s-tcp")
        p2 = await px.ensure_session_tcp("s-tcp")
        assert isinstance(p1, int) and p1 == p2
        assert [k for k in px._session_servers if k.startswith("tcp:")] \
            == ["tcp:s-tcp"]
    finally:
        await px.stop()


# ---------------------------------------------------------------- grants 域
def test_grants_clamp_bounds_and_revoke_miss():
    """TTL 三界常量（下钳 300/上钳 86400/默认 7200）+ revoke 未命中优雅 False。"""
    try:
        assert egress_grants.grant(
            "s-cl", "a.example.com", 1)["ttl_s"] == 300
        assert egress_grants.grant(
            "s-cl", "b.example.com", 999_999)["ttl_s"] == 86400
        assert egress_grants.grant(
            "s-cl2", "c.example.com")["ttl_s"] == 7200
        assert egress_grants.revoke("s-cl", "missing.example.com") is False
        assert egress_grants.revoke("no-such-sid", "a.example.com") is False
        assert egress_grants.revoke("s-cl", "a.example.com") is True
        assert egress_grants.revoke("s-cl", "a.example.com") is False
    finally:
        egress_grants._GRANTS.pop("s-cl", None)
        egress_grants._GRANTS.pop("s-cl2", None)


# ---------------------------------------------------------------- fail-closed 面
async def test_gate_rebind_denied_both_paths(monkeypatch):
    """DNS 重绑定双路 fail-closed：白名单域解析出私网拒；授权域同理。

    白名单只管「去哪」，重绑定管「实际到了哪」——两路 return False
    反转成 True 即放行私网回流（安全关键，此前从未对赌）。
    """
    from loadn_webui import net_policy
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow", ["rebind.example.com"])
    monkeypatch.setattr(net_policy, "rebind_check",
                        lambda host: (False, "private-ip"))
    px = egress_proxy.EgressProxy(port=0)
    ok, why = await px._gate("rebind.example.com", 443)
    assert not ok and why == "dns-rebind"          # 白名单路径
    egress_grants.grant("s-reb", "granted.example.com", 7200)
    try:
        ok2, why2 = await px._gate("granted.example.com", 443, sid="s-reb")
        assert not ok2 and why2 == "dns-rebind"    # 临时授权路径
    finally:
        egress_grants._GRANTS.pop("s-reb", None)


async def test_ask_create_failure_fail_closed(monkeypatch):
    """弹卡创建失败 → 回退直接拒（fail-closed），绝不放行。"""
    from loadn_webui import approve as approve_mod
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "ask")

    def boom(*a, **k):
        raise ValueError("approve store down")

    monkeypatch.setattr(approve_mod, "create", boom)
    px = egress_proxy.EgressProxy(port=0)
    ok, why = await px._gate("askfail.example.com", 443, sid="s-askfail")
    assert not ok and why == "not-in-allowlist"


def test_deny_body_chinese_readable():
    """403 体原生 UTF-8（ensure_ascii=False）——agent 可读设计点。"""
    body = egress_proxy._deny_body("x.example.com", "not-in-allowlist")
    assert "不在出口白名单".encode() in body


async def test_socket_mode_0660(tmp_path):
    """共享/会话级 socket 权限 0o660（组内 rw、无 world 位）。"""
    import os as _os
    px = egress_proxy.EgressProxy(port=0, uds_path=str(tmp_path / "e.sock"))
    await px.start()
    per = px._session_path("s-mode")
    try:
        assert _os.stat(tmp_path / "e.sock").st_mode & 0o777 == 0o660
        await px.ensure_session_uds("s-mode")
        assert _os.stat(per).st_mode & 0o777 == 0o660
    finally:
        await px.stop()
        per.unlink(missing_ok=True)


async def test_llm_gateway_default_path(monkeypatch):
    """网关缺省路由契约：裸域名请求（无路径）→ 真上游收到 /v1/messages。"""
    upstream_seen: dict = {}

    async def upstream(reader, writer):
        upstream_seen["line"] = (await reader.readline()).decode().strip()
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(upstream, "127.0.0.1", 0)
    up_port = srv.sockets[0].getsockname()[1]
    monkeypatch.setattr(egress_proxy, "_UPSTREAM",
                        (f"http://127.0.0.1:{up_port}", "T"))
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    px = egress_proxy.EgressProxy(port=0)
    port = await px.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"POST http://llm-gw.internal HTTP/1.1\r\n"
                b"Content-Length: 0\r\n\r\n")
        await w.drain()
        status = await asyncio.wait_for(r.readline(), timeout=10)
        assert status.startswith(b"HTTP/1.1 200"), status
        w.close()
        assert upstream_seen["line"].startswith("POST /v1/messages")
    finally:
        await px.stop()
        srv.close()

