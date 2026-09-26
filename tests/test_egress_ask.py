"""egress 三态档位 + 弹卡确认（ask）+ 会话级覆盖 组测试。

- _gate 三态：off=直通+审计（allow-open）/ warn=放行告警（allow-warn）/
  enforce=白名单外按 on_deny 处理
- ask 流（_ask_and_wait）：同域 pending 去重、批准→当连接直接放行
  （allow-ask，decide 已落授权）、拒绝→denied-by-user、超时→ask-timeout
- 会话覆盖：params.egress=off 在全局 enforce 下本会话直通；坏数据
  fail-open 回全局
- put_security_egress：枚举/范围校验 + CONFIG 热更 + yaml round-trip
- config load：非法 mode/on_deny/ask_wait_s 拒启（fail-closed，同 sandbox）
- 管理面 PUT /admin/egress/policy 与 GET /sessions/{sid}/egress 契约
"""
from __future__ import annotations

import asyncio
import json

import pytest

from loadn_webui import approve, egress_proxy
from loadn_webui import params as params_mod
from loadn_webui.config import CONFIG


def _gate(px, *a, **k):
    return asyncio.run(px._gate(*a, **k))


# ---------------------------------------------------------------- 三态档位
def test_gate_off_and_warn(monkeypatch):
    px = egress_proxy.EgressProxy(port=0)
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    monkeypatch.setattr(CONFIG.security, "egress_mode", "off")
    ok, why = _gate(px, "anywhere.example.com", 443)
    assert ok and why == "allow-open"                  # 全局放开（仍审计）
    monkeypatch.setattr(CONFIG.security, "egress_mode", "warn")
    ok, why = _gate(px, "anywhere.example.com", 443)
    assert ok and why == "allow-warn"                  # 放行 + 告警记录


# ---------------------------------------------------------------- 弹卡确认
async def test_ask_approved_frees_connection(monkeypatch):
    """批准 → 本连接直接放行（无需 agent 重试）。"""
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "ask")
    monkeypatch.setattr(CONFIG.security, "egress_ask_wait_s", 20)
    sid, host = "sess-ask-ok", "api.ask-example.com"

    async def approver():
        for _ in range(100):                     # 等弹卡落地即批准
            aid = approve.pending_egress_id(sid, host)
            if aid:
                approve.decide(aid, True)
                return
            await asyncio.sleep(0.1)

    px = egress_proxy.EgressProxy(port=0)
    ok, why = await asyncio.wait_for(
        _gather(px._gate(host, 443, sid), approver()), timeout=15)
    assert ok and why == "allow-ask"


async def test_ask_denied_by_user(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "ask")
    monkeypatch.setattr(CONFIG.security, "egress_ask_wait_s", 20)
    sid, host = "sess-ask-no", "api.ask-deny.com"

    async def denier():
        for _ in range(100):
            aid = approve.pending_egress_id(sid, host)
            if aid:
                approve.decide(aid, False)
                return
            await asyncio.sleep(0.1)

    px = egress_proxy.EgressProxy(port=0)
    ok, why = await _gather(px._gate(host, 443, sid), denier())
    assert not ok and why == "denied-by-user"


async def test_ask_timeout_and_dedupe(monkeypatch):
    """超时 → fail-closed 403；同域只挂一张 pending 卡（去重）。"""
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "ask")
    monkeypatch.setattr(CONFIG.security, "egress_ask_wait_s", 1)
    sid, host = "sess-ask-timeout", "api.ask-timeout.com"
    px = egress_proxy.EgressProxy(port=0)
    ok, why = await asyncio.wait_for(px._gate(host, 443, sid), timeout=10)
    assert not ok and why == "ask-timeout"
    aid = approve.pending_egress_id(sid, host)
    assert aid is not None                           # 超时后卡仍 pending（TTL 内）
    # 第二次同域被拦 → 复用同一张卡，不刷屏
    ok2, why2 = await asyncio.wait_for(px._gate(host, 443, sid), timeout=10)
    assert not ok2 and why2 == "ask-timeout"
    assert approve.pending_egress_id(sid, host) == aid


async def _gather(main, side):
    """并发跑 gate 协程与旁路协程（approver/denier），返回 gate 结果。"""
    side_t = asyncio.create_task(side)
    try:
        return await main
    finally:
        await side_t


# ---------------------------------------------------------------- 会话级覆盖
async def test_session_override_and_fail_open(client, monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_allow", [])
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "ask")
    # 全局 wait 上限（默认 120s）：防变异路径把 gate 打进 ask 全程等满，
    # 撞突变 runner 超时被误判存活（第二处显式 1s 保持原语义）
    monkeypatch.setattr(CONFIG.security, "egress_ask_wait_s", 2)
    r = await client.post("/api/sessions", json={"title": "egress 覆盖"})
    sid = r.json()["session"]["id"]

    # params.egress=off：全局 enforce 下本任务放开（allow-open）
    r = await client.patch(f"/api/sessions/{sid}",
                           json={"params": {"egress": "off"}})
    assert r.status_code == 200
    d = (await client.get(f"/api/sessions/{sid}/egress")).json()
    assert d["mode"] == "enforce" and d["override"] == "off"
    assert d["effective"] == "off"
    px = egress_proxy.EgressProxy(port=0)
    ok, why = await px._gate("unlisted.example.com", 443, sid)
    assert ok and why == "allow-open"

    # 清除覆盖 → 回全局 enforce（未列域走 ask；小等待直接超时）
    await client.patch(f"/api/sessions/{sid}", json={"params": {}})
    monkeypatch.setattr(CONFIG.security, "egress_ask_wait_s", 1)
    ok, why = await px._gate("unlisted2.example.com", 443, sid)
    assert not ok and why == "ask-timeout"

    # 坏数据 fail-open：直接改库塞非法值 → 回全局档
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET params_json=? WHERE id=?",
                  (json.dumps({"egress": "bogus"}), sid))
    assert egress_proxy._session_mode(sid) == "enforce"


# ---------------------------------------------------------------- 配置面
def test_params_validate_egress_key():
    out = params_mod.validate({"egress": "off", "effort": "low"})
    assert json.loads(out) == {"egress": "off", "effort": "low"}
    assert params_mod.validate({"egress": None}) is None
    with pytest.raises(ValueError, match="egress"):
        params_mod.validate({"egress": "open"})
    # 覆盖链单元：None=跟随全局，坏数据 fail-open
    assert params_mod.session_egress_override(None) is None
    assert params_mod.session_egress_override('{"egress":"warn"}') == "warn"
    assert params_mod.session_egress_override('{"egress":"junk"}') is None
    assert params_mod.session_egress_override("not-json") is None


def test_config_rejects_bad_egress_keys(tmp_path, monkeypatch):
    """启动通道 fail-closed：非法枚举/越界拒绝启动（同 sandbox 档位语义）。"""
    from loadn_webui import config as config_mod
    for bad in ({"egress_mode": "open"},
                {"egress_on_deny": "please"},
                {"egress_ask_wait_s": 5},
                {"egress_ask_wait_s": 9999}):
        (tmp_path / "config.yaml").write_text(
            "security:\n" + "".join(f"  {k}: {v}\n" for k, v in bad.items()))
        monkeypatch.setattr(config_mod, "ROOT", tmp_path)
        with pytest.raises(ValueError):
            config_mod.load_config()
    (tmp_path / "config.yaml").write_text(
        "security:\n  egress_mode: off\n  egress_on_deny: ask\n"
        "  egress_ask_wait_s: 60\n")
    cfg = config_mod.load_config()
    assert cfg.security.egress_mode == "off"          # off 是合法档
    assert cfg.security.egress_ask_wait_s == 60


async def test_admin_policy_put_and_hot_effect(client, monkeypatch):
    """PUT /admin/egress/policy：校验 + 热生效 + 持久化。"""
    r = await client.put("/api/admin/egress/policy",
                         json={"mode": "warn", "ask_wait_s": 45})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert CONFIG.security.egress_mode == "warn"       # CONFIG 原地热更
    assert CONFIG.security.egress_ask_wait_s == 45
    r = (await client.get("/api/admin/egress")).json()
    assert r["mode"] == "warn" and r["on_deny"] and r["ask_wait_s"] == 45
    p = (await client.get("/api/admin/security")).json()
    assert p["egress"]["mode"] == "warn" and p["egress"]["on_deny"] == "ask"
    # yaml 持久化（round-trip 落盘）
    import yaml as _yaml

    from loadn_webui.config import ROOT
    sec = _yaml.safe_load((ROOT / "config.yaml").read_text())["security"]
    assert sec["egress_mode"] == "warn" and sec["egress_ask_wait_s"] == 45
    # 非法值 400
    for bad in ({"mode": "open"}, {"on_deny": "pray"}, {"ask_wait_s": 5}, {}):
        r = await client.put("/api/admin/egress/policy", json=bad)
        assert r.status_code == 400, bad
    # 收尾回出厂态（测试服务与用例共享同一进程的 CONFIG）
    r = await client.put("/api/admin/egress/policy",
                         json={"mode": "enforce", "ask_wait_s": 120})
    assert r.status_code == 200
