"""A7 对抗组（P0-5）：net-policy——URL 凭证脱敏 + IP 私网分类 + DNS 重绑定。

A7-1  redact_url fixtures（openclaw case 清单语义）：userinfo/敏感 query 键/
      变体 token 键/fragment/Telegram bot 路径/干净 URL 原样
A7-2  redact 自由文本兜底 + audit 账本总闸：记账后 detail_json 无明文凭证
A7-3  IP 分类：RFC1918/CGNAT/链路本地/回环/未指定/组播/TEST-NET/v4-mapped/
      公网 None
A7-4  DNS 重绑定：白名单域名解析出私网 IP → egress 拒 + anomaly 审计；
      解析公网 IP → 放行；解析失败 → 放行（连不上自然 502）；TTL 缓存
      只解析一次
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("sec.a7")]

import pytest

from loadn_webui.config import CONFIG
from loadn_webui.security import net_policy as np


# ---------------------------------------------------------------- A7-1
@pytest.mark.parametrize("raw,must_not,must_keep", [
    ("https://user:sekret@api.example.com/v1?a=1", ["user", "sekret"],
     ["api.example.com", "a=1"]),
    ("https://api.example.com/v1?token=TOPSECRET&b=2", ["TOPSECRET"],
     ["token=***", "b=2"]),
    ("https://api.example.com/v1?api_key=AKIA123&x=1", ["AKIA123"], ["api_key=***"]),
    ("https://api.example.com/v1?client_secret=cs_9&sig=s0", ["cs_9", "s0"],
     ["client_secret=***", "sig=***"]),
    ("https://api.example.com/v1?upload_token_0123456789abcdef=hex", ["hex"],
     ["=***"]),
    ("https://api.example.com/cb#access_token=fragtok", ["fragtok", "#"], []),
    ("https://api.telegram.org/bot1234567:AAFFdeadbeefcafebabe1234/sendMessage",
     ["AAFFdeadbeefcafebabe1234"], ["bot***"]),
    ("https://api.example.com/ok?plain=yes", [], ["plain=yes", "api.example.com"]),
])
def test_a7_1_redact_url_fixtures(raw, must_not, must_keep):
    out = np.redact_url(raw)
    for s in must_not:
        assert s not in out, f"{raw!r} → {out!r} 泄漏 {s!r}"
    for s in must_keep:
        assert s in out, f"{raw!r} → {out!r} 丢了 {s!r}"


def test_a7_1b_non_url_passthrough():
    assert np.redact("plain text no url") == "plain text no url"
    assert np.redact("") == ""


# ---------------------------------------------------------------- A7-2
def test_a7_2_audit_never_stores_plaintext():
    from loadn_webui.security import audit as audit_mod
    audit_mod.audit(
        "egress_request",
        {"host": "x", "url": "https://u:pw@evil.example/a?token=LEAKED&ok=1#f",
         "note": "see https://api.example.com/?api_key=SUPERKEY"})
    row = audit_mod.tail(1, "egress_request")[0]
    d = row["detail_json"]
    for leak in ("LEAKED", "SUPERKEY", "u:pw"):
        assert leak not in d, f"审计账本泄漏 {leak!r}：{d}"
    assert "token=***" in d and "api_key=***" in d
    # 哈希链在脱敏文本上仍自洽
    assert audit_mod.verify() == []


# ---------------------------------------------------------------- A7-3
@pytest.mark.parametrize("ip,cat", [
    ("10.1.2.3", "private"), ("172.16.0.1", "private"), ("192.168.8.8", "private"),
    ("100.64.0.7", "cgnat"), ("100.127.255.255", "cgnat"),
    ("169.254.169.254", "link_local"),
    ("127.0.0.1", "loopback"), ("::1", "loopback"),
    ("0.0.0.0", "unspecified"),
    ("224.0.0.1", "multicast"),
    ("192.0.2.5", "test_net"), ("203.0.113.9", "test_net"),
    ("::ffff:10.0.0.1", "private"),           # v4-mapped 按 v4 语义分类
    ("8.8.8.8", None), ("2606:4700::1111", None),
])
def test_a7_3_ip_classification(ip, cat):
    assert np.classify_ip(ip) == cat, ip
    assert np.is_non_public_ip(ip) == (cat is not None)


def test_a7_3b_non_ip_literal():
    assert np.classify_ip("example.com") is None
    assert np.classify_ip("") is None


# ---------------------------------------------------------------- A7-4
def _fresh(monkeypatch, ips):
    np._DNS_CACHE.clear()
    monkeypatch.setattr(np, "resolve_ips", lambda host: list(ips))


async def test_a7_4_rebind_rejected_and_audited(monkeypatch):
    from loadn_webui.security import audit as audit_mod
    from loadn_webui.security import egress_proxy
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow", ["rebind.example"])
    monkeypatch.setattr(CONFIG.security, "egress_on_deny", "deny")
    _fresh(monkeypatch, ["93.184.216.34", "192.168.1.5"])  # 公网+私网混答
    px = egress_proxy.EgressProxy(port=0)
    ok, why = await px._gate("rebind.example", 443)
    assert not ok and why == "dns-rebind"
    rows = audit_mod.tail(3, "anomaly")
    assert any("dns-rebind" in r["detail_json"] for r in rows)
    assert "192.168.1.5" in rows[0]["detail_json"]          # reason 带证据 IP


async def test_a7_4b_public_and_unresolvable_pass(monkeypatch):
    from loadn_webui.security import egress_proxy
    monkeypatch.setattr(CONFIG.security, "egress_mode", "enforce")
    monkeypatch.setattr(CONFIG.security, "egress_allow", ["ok.example"])
    px = egress_proxy.EgressProxy(port=0)
    _fresh(monkeypatch, ["93.184.216.34"])
    ok, why = await px._gate("ok.example", 443)
    assert ok and why == "allow"
    _fresh(monkeypatch, [])                                 # 解析失败 → 放行（502 自然失败）
    ok, why = await px._gate("ok.example", 443)
    assert ok


async def test_a7_4c_dns_cached_once(monkeypatch):
    calls = {"n": 0}

    def counting(host):
        calls["n"] += 1
        return ["93.184.216.34"]
    np._DNS_CACHE.clear()
    monkeypatch.setattr(np, "resolve_ips", counting)
    for _ in range(3):
        ok, _ = np.rebind_check("cache.example")
        assert ok
    assert calls["n"] == 1                    # TTL 内只解析一次


def test_a7_4d_literal_ip_short_circuit():
    ok, why = np.rebind_check("127.0.0.1")
    assert not ok and "loopback" in why
    ok, why = np.rebind_check("localhost")
    assert not ok and "localhost" in why
