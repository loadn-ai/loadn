"""「封网络不封能力」平衡组测试：SSRF 防护 + 白名单热重载/一键放行 +
审批式临时授权（任务级限时）。

- _ssrf_check：私网/回环/链路本地/保留段、平台敏感域后缀、非 http(s)、
  字面 IP 免解析、DNS 失败放行（留给 _get 报错）
- _guarded_get：重定向逐跳校验（公网页 302 → 内网 = 经典逃逸，必拦）
- fetch_page：SSRF 命中直接失败，绝不落入 CDP 兜底（浏览器不许换道绕行）
- put_egress_allow：yaml round-trip + 内存 CONFIG 热更（免重启）
- _maybe_reload_policy：手工改 yaml 的 mtime 监听热生效；坏 yaml 保旧值
- egress_grants：任务级授权/后缀匹配/惰性过期/收回；_gate 按 sid 判定
- approve egress：裁决即生效（executed 终态，无确认码步）
- cli_gateway：fetch/search 中介通道不再吃直连白名单；browser 仍守
"""
from __future__ import annotations

import time

import pytest

from loadn_webui import egress_grants, egress_proxy, resources
from loadn_webui.config import CONFIG, ROOT


# ---------------------------------------------------------------- SSRF 单元
async def test_ssrf_check_blocks_private_and_sensitive():
    blocked = [
        "http://127.0.0.1:8686/parse",          # 回环（OCR 服务本体）
        "http://localhost/x",
        "http://192.168.8.57/",                  # RFC1918
        "http://10.0.0.5/",
        "http://172.16.1.1/",
        "http://169.254.169.254/latest/meta-data",  # 云元数据（链路本地）
        "http://[::1]/",
        "http://llm-gw.internal/v1/messages",    # 平台敏感域（LLM 网关）
        "http://sub.llm-gw.internal/",           # 后缀匹配
        "https://sms.woldy.net:30443/recent",    # 平台敏感域（短信服务）
        "file:///etc/passwd",                    # 非 http(s)
        "ftp://example.com/",
    ]
    for url in blocked:
        with pytest.raises(resources.SsrfBlocked):
            await resources._ssrf_check(url)


async def test_ssrf_check_allows_public():
    await resources._ssrf_check("http://8.8.8.8/")            # 公网字面 IP
    await resources._ssrf_check("https://no-such.invalid/")  # DNS 失败 → 放行
    await resources._ssrf_check("http://Example.COM./")       # 归一化大小写/尾点


class _Resp:
    def __init__(self, status=200, location="", url=""):
        self.status_code = status
        self.headers = {"location": location} if location else {}
        self.text = "ok"
        self.url = url


async def test_guarded_get_redirect_hop_checked(monkeypatch):
    """重定向链中间跳命中内网 → 拦（httpx follow_redirects 一把梭会漏）。"""
    async def fake_get(url, **kw):
        if "pub.example.com" in url:
            return _Resp(302, location="http://10.0.0.5/x", url=url)
        raise AssertionError(f"不应请求 {url}")

    monkeypatch.setattr(resources, "_get", fake_get)
    with pytest.raises(resources.SsrfBlocked):
        await resources._guarded_get("http://pub.example.com/a")


async def test_guarded_get_follows_to_public(monkeypatch):
    seen = []

    async def fake_get(url, **kw):
        seen.append((url, kw.get("follow_redirects")))
        if "pub.example.com" in url:
            return _Resp(302, location="http://8.8.8.8/final", url=url)
        return _Resp(200, url=url)

    monkeypatch.setattr(resources, "_get", fake_get)
    resp = await resources._guarded_get("http://pub.example.com/a")
    assert resp.status_code == 200
    # 逐跳都是 follow_redirects=False 的手动跟随
    assert all(f is False for _, f in seen)


async def test_fetch_page_ssrf_never_falls_to_cdp(monkeypatch):
    async def no_cdp(*a, **kw):
        raise AssertionError("SSRF 命中不得落入 CDP 兜底")

    monkeypatch.setattr(resources, "_fetch_via_cdp", no_cdp)
    with pytest.raises(RuntimeError, match="SSRF"):
        await resources.fetch_page(f"http://192.168.1.{int(time.time()) % 200}/x")


def test_valid_host():
    assert egress_grants.valid_host("Api.Example.COM.") == "api.example.com"
    assert egress_grants.valid_host("api.example.com") == "api.example.com"
    for bad in ("", "localhost", "http://x.com", "x.com/path", "x.com:443",
                "a b.com", "x..", ".x.com"):
        assert egress_grants.valid_host(bad) is None, bad


# ---------------------------------------------------------------- 授权生命周期
def test_grant_scoped_and_expiry():
    sid = "sess-grant-test"
    egress_grants.grant(sid, "api.example.com", 7200, approval_id=7)
    assert egress_grants.allowed(sid, "api.example.com")
    assert egress_grants.allowed(sid, "sub.api.example.com")   # 后缀语义
    assert not egress_grants.allowed("other-sid", "api.example.com")  # 任务级
    assert not egress_grants.allowed(sid, "evil.com")
    act = egress_grants.list_active()
    assert any(g["sid"] == sid and g["host"] == "api.example.com" for g in act)
    assert egress_grants.revoke(sid, "api.example.com")
    assert not egress_grants.allowed(sid, "api.example.com")
    assert not egress_grants.revoke(sid, "api.example.com")    # 幂等拒绝

    # 过期（惰性）：直接把到期戳改到过去
    egress_grants.grant(sid, "expired.example.com", 7200)
    egress_grants._GRANTS[sid]["expired.example.com"] = time.time() - 1
    assert not egress_grants.allowed(sid, "expired.example.com")
    assert all(g["host"] != "expired.example.com"
               for g in egress_grants.list_active())           # 顺手清


def test_gate_honors_session_grant(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow", ["base.example.org"])
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "deny")  # 弹卡流另测
    px = egress_proxy.EgressProxy(port=0)
    sid = "sess-gate-test"

    def gate(*a, **k):
        import asyncio as _aio
        return _aio.run(px._gate(*a, **k))

    ok, why = gate("base.example.org", 443)
    assert ok and why == "allow"                               # 白名单
    ok, why = gate("grant.example.net", 443)
    assert not ok and why == "not-in-allowlist"                # 无 sid 不放
    ok, _ = gate("grant.example.net", 443, sid="nosuch")
    assert not ok                                               # 无授权
    egress_grants.grant(sid, "grant.example.net", 7200)
    ok, why = gate("grant.example.net", 443, sid=sid)
    assert ok and why == "allow-grant"                          # 授权放行
    ok, _ = gate("grant.example.net", 443, sid="other")
    assert not ok                                               # 不跨任务
    egress_grants.revoke(sid, "grant.example.net")


# ---------------------------------------------------------------- 审批 egress
def test_approve_egress_grants_on_decision():
    from loadn_webui import approve
    sid = "sess-approve-egress"
    with pytest.raises(ValueError):
        approve.create(sid, "egress", {"host": "not a host"})   # 创建即验

    out = approve.create(sid, "egress", {"host": "api.example.com",
                                         "ttl_s": 3600}, note="调 X API")
    res = approve.decide(out["id"], True)
    assert res["ok"] and res["status"] == "executed"            # 裁决即生效
    assert egress_grants.allowed(sid, "api.example.com")
    assert res["granted"]["ttl_s"] == 3600
    # 终态不可重复裁决
    again = approve.decide(out["id"], True)
    assert not again["ok"]
    egress_grants.revoke(sid, "api.example.com")


def test_approve_egress_bad_host_at_decision(monkeypatch):
    """绕过 create 校验直插库行（坏 host）→ 裁决拒绝生效、状态不动。"""
    from loadn_webui import approve
    sid = "sess-approve-bad"
    import json as _json
    with approve._conn() as c:
        approve._ensure(c)
        cur = c.execute(
            "INSERT INTO approvals (sid, action_type, summary, params_json,"
            " params_hash, status, created_at, ttl_s)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (sid, "egress", "x", _json.dumps({"host": "bad host"}),
             "ph", "pending", approve._now(), 600))
        aid = cur.lastrowid
    res = approve.decide(aid, True)
    assert not res["ok"]
    assert not egress_grants.allowed(sid, "bad host")


# ---------------------------------------------------------------- 白名单管理面
def test_put_egress_allow_roundtrip():
    from loadn_webui import settings_admin
    # put_egress_allow 直接改进程级 CONFIG + 落盘共享 config.yaml——
    # 必须 finally 恢复/清理，否则污染同进程后续所有测试（如隧道用例）
    old_allow = list(CONFIG.security.egress_allow)
    try:
        settings_admin.put_egress_allow("add", "api.example.com")
        assert "api.example.com" in CONFIG.security.egress_allow
        # yaml 未写过 egress_allow 键时以内存现行清单为底（默认域不清零）
        assert old_allow and set(old_allow) <= set(CONFIG.security.egress_allow)
        import yaml as _yaml
        disk = _yaml.safe_load((ROOT / "config.yaml").read_text())
        assert "api.example.com" in disk["security"]["egress_allow"]
        settings_admin.put_egress_allow("add", "api.example.com")   # 幂等
        assert CONFIG.security.egress_allow.count("api.example.com") == 1
        with pytest.raises(ValueError):
            settings_admin.put_egress_allow("add", "not a host")
        settings_admin.put_egress_allow("remove", "api.example.com")
        assert "api.example.com" not in CONFIG.security.egress_allow
        assert CONFIG.security.egress_allow == old_allow          # 完整还原
        disk = _yaml.safe_load((ROOT / "config.yaml").read_text())
        assert "api.example.com" not in disk["security"]["egress_allow"]
    finally:
        CONFIG.security.egress_allow = old_allow
        (ROOT / "config.yaml").unlink(missing_ok=True)
        egress_proxy._POLICY_MTIME = None


def test_maybe_reload_policy(monkeypatch):
    """手工改 config.yaml → mtime 热重载；坏 yaml 保旧值。"""
    import os as _os

    import yaml as _yaml
    p = ROOT / "config.yaml"
    old_allow = list(CONFIG.security.egress_allow)
    old_mode = CONFIG.security.egress_mode
    egress_proxy._POLICY_MTIME = None
    try:
        p.write_text(_yaml.safe_dump(
            {"security": {"egress_allow": ["reload.example.com"],
                          "egress_mode": "warn"}}))
        egress_proxy._maybe_reload_policy()
        assert CONFIG.security.egress_allow == ["reload.example.com"]
        assert CONFIG.security.egress_mode == "warn"
        # 内存改动（无文件变更）不被回滚
        CONFIG.security.egress_allow = ["mem.example.com"]
        egress_proxy._maybe_reload_policy()
        assert CONFIG.security.egress_allow == ["mem.example.com"]
        # 坏 yaml → 保旧值
        p.write_text("::: not yaml [\n")
        _os.utime(p, ns=(time.time_ns() + 10_000_000,) * 2)
        egress_proxy._maybe_reload_policy()
        assert CONFIG.security.egress_allow == ["mem.example.com"]
    finally:
        p.unlink(missing_ok=True)
        CONFIG.security.egress_allow = old_allow
        CONFIG.security.egress_mode = old_mode
        egress_proxy._POLICY_MTIME = None


# ---------------------------------------------------------------- 中介/直连分治
def test_cli_gateway_mediated_vs_direct():
    from loadn_webui import policy as policy_mod
    # fetch（宿主中介 GET，SSRF 防护在内层）：任意公网域可走
    d = policy_mod.cli_gateway(
        ["r", "fetch", "https://arbitrary-site.example.com/page"], "fetch")
    assert d.ok
    # search：查询词不吃白名单
    d = policy_mod.cli_gateway(["r", "search", "任意主题"], "search")
    assert d.ok
    # browser（富通道）：保守沿用白名单
    d = policy_mod.cli_gateway(
        ["r", "browser", "https://not-allowed.example.org/"], "browser")
    assert not d.ok
    # 直连 Bash（curl）仍由 check_command 的 L1 段守
    d = policy_mod.check_command("curl -s https://not-allowed.example.org/x",
                                 source="test")
    assert not d.ok


# ---------------------------------------------------------------- 会话归属（属性面板）
def test_record_sid_attribution():
    """_record 带 sid → 审计行有归属（tail sid 过滤可见）；无 sid 不进会话视图。"""
    import json as _json

    from loadn_webui import audit as audit_mod
    egress_proxy._record("sid-attr.example.com", "deny", "enforce", 443,
                         sid="sess-attr")
    rows = audit_mod.tail(5, "egress_request", sid="sess-attr")
    assert rows, "带 sid 的外联事件应可按会话过滤"
    d = _json.loads(rows[0]["detail_json"])
    assert d["host"] == "sid-attr.example.com" and d["decision"] == "deny"
    # 无 sid 调用（共享 TCP 通道）不落会话归属
    egress_proxy._record("nosid.example.com", "allow", "enforce")
    assert all(_json.loads(r["detail_json"])["host"] != "nosid.example.com"
               for r in audit_mod.tail(20, "egress_request", sid="sess-attr"))


def test_tail_sid_filter_chain_mixed():
    """新旧行混合（无 sid 旧形态 + 带 sid 新形态）：过滤只出新行，链校验全量过。"""
    import json as _json

    from loadn_webui import audit as audit_mod
    egress_proxy._record("legacy.example.com", "allow", "enforce")
    egress_proxy._record("owned.example.com", "deny", "enforce",
                         sid="sess-mix")
    only = audit_mod.tail(10, "egress_request", sid="sess-mix")
    assert [_json.loads(r["detail_json"])["host"] for r in only] \
        == ["owned.example.com"]
    assert audit_mod.verify() == []                      # 混合链仍自洽


def test_list_active_sid_filter_keeps_gc():
    """sid 过滤只影响输出；他人会话的过期项仍被惰性回收（GC 唯一入口）。"""
    egress_grants.grant("sess-own", "own.example.com", 7200)
    egress_grants.grant("sess-other", "other.example.com", 7200)
    egress_grants.grant("sess-other", "dead.example.com", 7200)
    egress_grants._GRANTS["sess-other"]["dead.example.com"] = time.time() - 1
    mine = egress_grants.list_active(sid="sess-own")
    assert [g["host"] for g in mine] == ["own.example.com"]
    assert "dead.example.com" not in egress_grants._GRANTS["sess-other"]
    egress_grants.revoke("sess-own", "own.example.com")
    egress_grants.revoke("sess-other", "other.example.com")


# ---------------------------------------------------------------- 会话级 socket
async def test_session_uds_preferred(monkeypatch, tmp_path):
    from loadn_webui import sandbox
    from loadn_webui.config import PATHS
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    run = tmp_path / "run"
    run.mkdir()
    (run / "egress.sock").write_text("")
    monkeypatch.setitem(PATHS, "run", run)
    monkeypatch.setattr(egress_proxy, "_POLICY_MTIME", None)

    px = egress_proxy.EgressProxy(port=0, uds_path=str(run / "egress.sock"))
    assert sandbox._egress_uds("sid-x") == run / "egress.sock"   # 无会话 socket 回落共享
    await px.ensure_session_uds("sid-x")
    per = px._session_path("sid-x")
    assert per.exists()
    assert sandbox._egress_uds("sid-x") == per                    # 会话级优先
    assert sandbox._egress_uds("sid-y") == run / "egress.sock"   # 他人不受影响
    again = await px.ensure_session_uds("sid-x")                  # 幂等
    assert again == str(per)
    await px.stop()
    per.unlink(missing_ok=True)
