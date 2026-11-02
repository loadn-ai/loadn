"""EG-R1：egress 会话通道回收（端口只增不减的泄漏修复）。

生产实证（2026-11-02）：进程存活半天积累 5-7 个会话级回环端口，监控
面板逐个 HTTP 探测飘 502 红。修复三路：归档/删除即关（sessions 路由
钩子）+ 空闲 2h 惰性回收（ensure 时顺带扫）+ close_session 幂等可重开。
"""
from __future__ import annotations

import time

from loadn_webui.security import egress_proxy
from loadn_webui.security.egress_proxy import EgressProxy


async def test_close_session_releases_and_reopens():
    """close： dicts 清空+监听关闭；再 ensure 幂等重开（新端口）。"""
    p = EgressProxy(port=0)
    port1 = await p.ensure_session_tcp("s-reap-1")
    assert port1 and "s-reap-1" in p._session_ports
    p.close_session("s-reap-1")
    assert "s-reap-1" not in p._session_ports
    assert "tcp:s-reap-1" not in p._session_servers
    p.close_session("s-reap-1")                                    # 幂等不炸
    port2 = await p.ensure_session_tcp("s-reap-1")
    assert isinstance(port2, int) and port2 > 0                     # 重开成功
    assert "s-reap-1" in p._session_ports


async def test_reap_idle_on_ensure():
    """空闲 2h 的会话通道在下次 ensure 时被回收；未过期与 keep 保护的不动。"""
    p = EgressProxy(port=0)
    await p.ensure_session_tcp("s-old")
    await p.ensure_session_tcp("s-fresh")
    # old 伪造 3h 前活动；fresh 保持刚刚
    p._session_last["s-old"] = time.monotonic() - 3 * 3600
    await p.ensure_session_tcp("s-new")          # 触发惰性回收
    assert "s-old" not in p._session_ports       # 过期回收
    assert "s-fresh" in p._session_ports         # 未过期保留
    assert "s-new" in p._session_ports           # keep=本次 ensure 的sid
    # ensure 命中已有通道（幂等短路）不触发回收——无回归即可


async def test_handle_touch_refreshes_activity():
    """连接到达刷新活动时间（回收依据是「最后连接」而非创建时间）。"""
    p = EgressProxy(port=0)
    await p.ensure_session_tcp("s-touch")
    p._session_last["s-touch"] = time.monotonic() - 3 * 3600   # 伪造过期
    # 直接调 _handle 会真的读 socket——用极短 reader 触发超时路径前 touch
    import asyncio
    r, w = await asyncio.open_connection("127.0.0.1", p._session_ports["s-touch"])
    await asyncio.sleep(0.2)
    w.close()
    await asyncio.sleep(0.2)
    assert p._session_last["s-touch"] > time.monotonic() - 5   # 已刷新


async def test_archive_closes_channel(client, monkeypatch):
    """归档钩子：PATCH status=archived → 会话 egress 通道即关（API 级）。"""
    r = await client.post("/api/sessions", json={"title": "egress 回收"})
    sid = r.json()["session"]["id"]
    p = EgressProxy(port=0)
    monkeypatch.setattr(egress_proxy, "_INSTANCE", p)
    port = await p.ensure_session_tcp(sid)
    assert port and sid in p._session_ports
    r = await client.patch(f"/api/sessions/{sid}", json={"status": "archived"})
    assert r.status_code == 200
    assert sid not in p._session_ports and "tcp:" + sid not in p._session_servers
