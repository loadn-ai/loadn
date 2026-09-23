"""属性面板后端面（会话视图直达）：/sessions/{sid}/egress 端点 + MCP 哨兵。

- egress 端点：模式/白名单/本会话授权过滤/事件 sid 过滤/404；
  快速放行与收回复用既有 /admin/egress/*（不重复测）
- MCP 哨兵：PATCH mcp {srv: false} → .mcp.json 剔除该全局 server；
  rematerialize（全局变更）保持；删键恢复
"""
from __future__ import annotations

import json


async def test_session_egress_endpoint(client):
    from loadn_webui import egress_grants, egress_proxy
    r = await client.post("/api/sessions", json={"title": "外联视图"})
    sid = r.json()["session"]["id"]

    r = await client.get(f"/api/sessions/{sid}/egress")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] and isinstance(d["allow"], list)

    # 授权：只出本会话的（他人的不串台）
    egress_grants.grant(sid, "props.example.com", 7200)
    egress_grants.grant("other-sess", "not-mine.example.com", 7200)
    try:
        d = (await client.get(f"/api/sessions/{sid}/egress")).json()
        assert [g["host"] for g in d["grants"]] == ["props.example.com"]
    finally:
        egress_grants.revoke(sid, "props.example.com")
        egress_grants.revoke("other-sess", "not-mine.example.com")

    # 事件归属：带 sid 的行进本会话视图，无 sid 的不进
    from loadn_webui import audit as audit_mod
    egress_proxy._record("mine.example.com", "deny", "enforce", sid=sid)
    egress_proxy._record("stray.example.com", "allow", "enforce")
    d = (await client.get(f"/api/sessions/{sid}/egress?n=50")).json()
    hosts = [e["host"] for e in d["events"]]
    assert "mine.example.com" in hosts
    assert "stray.example.com" not in hosts
    assert audit_mod.verify() == []

    # 404
    r = await client.get("/api/sessions/no-such/egress")
    assert r.status_code == 404


async def test_mcp_session_disable_sentinel(client, monkeypatch, tmp_path):
    from loadn_webui import mcp_admin
    from loadn_webui import workspace as ws_mod
    from loadn_webui.config import CONFIG
    real_servers = CONFIG.mcp.servers
    monkeypatch.setattr(CONFIG.mcp, "servers", {
        "global-a": {"command": "echo", "args": ["a"]},
        "global-b": {"command": "echo", "args": ["b"]}})
    try:
        r = await client.post("/api/sessions", json={"title": "MCP 哨兵"})
        sid = r.json()["session"]["id"]
        ws = ws_mod.ws_of(sid)
        assert set(json.loads(
            (ws / ".mcp.json").read_text())["mcpServers"]) == {"global-a", "global-b"}

        # 哨兵 false = 本会话禁用该全局 server
        r = await client.patch(f"/api/sessions/{sid}", json={"mcp": {"global-b": False}})
        assert r.status_code == 200
        merged = json.loads((ws / ".mcp.json").read_text())["mcpServers"]
        assert set(merged) == {"global-a"}
        # DB 侧哨兵原样保留（三态还原依据）
        d = (await client.get(f"/api/sessions/{sid}")).json()
        assert json.loads(d["mcp_json"]) == {"global-b": False}

        # 全局 server 变更触发的 rematerialize 走同一合并——哨兵语义保持
        assert mcp_admin.rematerialize_sessions() >= 1
        merged = json.loads((ws / ".mcp.json").read_text())["mcpServers"]
        assert set(merged) == {"global-a"}

        # 删键（mcp 覆盖清空）恢复全局
        r = await client.patch(f"/api/sessions/{sid}", json={"mcp": {}})
        assert r.status_code == 200
        merged = json.loads((ws / ".mcp.json").read_text())["mcpServers"]
        assert set(merged) == {"global-a", "global-b"}
    finally:
        # 还原全局配置并重物化，别把 monkeypatch 的假 server 留在共享工作区树
        CONFIG.mcp.servers = real_servers
        mcp_admin.rematerialize_sessions()
