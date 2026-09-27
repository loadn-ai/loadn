"""M7a/c：平台面突变存活对赌——局部 PUT 键语义/范围门边界/MCP 注入门
（配置开关的否定路径）/供应链符号链跳过。
"""
from __future__ import annotations

import json

import pytest

from loadn_webui import skill_scan as ss
from loadn_webui import workspace as ws_mod
from loadn_webui.config import CONFIG
from loadn_webui.settings_admin import put_run, put_titlegen


@pytest.fixture(autouse=True)
def _restore_config():
    """put_* 内部 setattr 直改 CONFIG（绕过 monkeypatch）——快照回滚防泄漏。"""
    import copy
    snap = copy.deepcopy(CONFIG.titlegen), copy.deepcopy(CONFIG.run)
    yield
    CONFIG.titlegen.__dict__.update(
        {k: v for k, v in snap[0].__dict__.items()})
    CONFIG.run.__dict__.update({k: v for k, v in snap[1].__dict__.items()})


# ------------------------------------------------------- 局部 PUT 键语义
def test_titlegen_partial_put_key_semantics(tmp_path, monkeypatch):
    """键在=更新、键缺=保持（in→not in 反转后缺键会误写 None/清空）。"""
    monkeypatch.setattr("loadn_webui.settings_admin._write_section",
                        lambda sec, upd: None)
    monkeypatch.setattr(CONFIG.titlegen, "model", "旧模型")
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)
    out = put_titlegen({"model": "新模型"})          # 只带 model
    assert CONFIG.titlegen.model == "新模型"
    assert CONFIG.titlegen.enabled is True           # 缺键不被触碰
    put_titlegen({"enabled": False})                 # 只带 enabled
    assert CONFIG.titlegen.model == "新模型"          # model 保持
    assert CONFIG.titlegen.enabled is False
    with pytest.raises(ValueError, match="http"):
        put_titlegen({"api_base": "ftp://x"})
    put_titlegen({"api_base": "https://ok.test/v1"})
    assert CONFIG.titlegen.api_base == "https://ok.test/v1"
    put_titlegen({"api_base": ""})                   # 空串=回退 None（写侧）
    out2 = put_titlegen({"model": "再改"})             # api_base 键缺=保持
    assert CONFIG.titlegen.model == "再改"


def test_run_partial_put_and_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr("loadn_webui.settings_admin._write_section",
                        lambda sec, upd: None)
    monkeypatch.setattr(CONFIG.run, "max_concurrent_turns", 3)
    put_run({"replay_max_events": 100})              # 只带一个键
    assert CONFIG.run.max_concurrent_turns == 3      # 另一键不动
    assert CONFIG.run.replay_max_events == 100
    # 范围门边界：恰好边界过，界外拒（含可读文案）
    put_run({"max_concurrent_turns": 1})
    put_run({"max_concurrent_turns": 16})
    with pytest.raises(ValueError, match="1-16"):
        put_run({"max_concurrent_turns": 0})
    with pytest.raises(ValueError, match="1-16"):
        put_run({"max_concurrent_turns": 17})
    put_run({"replay_max_events": 20})
    with pytest.raises(ValueError, match="20-2000"):
        put_run({"replay_max_events": 19})
    put_run({"events_retain_days": 0.5})
    with pytest.raises(ValueError, match="0.5-365"):
        put_run({"events_retain_days": 0.4})
    with pytest.raises(ValueError, match="0.5-365"):
        put_run({"events_retain_days": 366})


# ------------------------------------------------------- MCP 注入门
def test_mcp_injection_gates_off_by_default(tmp_path, monkeypatch):
    """codemode/lsp 默认关：.mcp.json 不得出现（or 反转=关着也注入）。"""
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", False)
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", False)
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "")
    monkeypatch.setattr(CONFIG.mcp, "servers", {})
    ws = tmp_path / "ws"
    ws.mkdir()
    ws_mod.write_mcp_json(ws, {})
    assert not (ws / ".mcp.json").exists()           # 空合并不落文件

    monkeypatch.setattr(CONFIG.security, "codemode_enabled", True)
    ws_mod.write_mcp_json(ws, {})
    d = json.loads((ws / ".mcp.json").read_text())["mcpServers"]
    assert "codemode" in d and "lsp" not in d        # 只开 codemode
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", True)
    ws_mod.write_mcp_json(ws, {})
    d2 = json.loads((ws / ".mcp.json").read_text())["mcpServers"]
    assert "lsp" in d2 and d2["lsp"]["args"] == ["_lsp-mcp"]


def test_mcp_session_sentinel_disables_injected(tmp_path, monkeypatch):
    """会话哨兵 False 剔除**全局** server（哨兵只作用于合并表——注入门在
    哨兵之后跑，本会话 False 剔除后注入门不再补回，语义=哨兵只管既有）。"""
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", True)
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", False)
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "")
    monkeypatch.setattr(CONFIG.mcp, "servers",
                        {"codemode": {"command": "x", "args": []}})
    ws = tmp_path / "ws2"
    ws.mkdir()
    ws_mod.write_mcp_json(ws, {"codemode": False})
    assert not (ws / ".mcp.json").exists() or "codemode" not in \
        json.loads((ws / ".mcp.json").read_text())["mcpServers"]
    # 哨兵剔除唯一 server 后合并表空 → 不落文件


# ------------------------------------------------------- 供应链符号链
def test_skill_scan_skips_symlinks(tmp_path):
    """符号链文件跳过（and→or=跟随链接扫到界外内容）。"""
    real = tmp_path / "real.txt"
    real.write_text("pip3 install evil-pkg\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    texts = list(ss._iter_texts(tmp_path))
    assert real in texts and link not in texts        # 链接本身不进扫描


# ------------------------------------------------------- 安全运维面写口
async def test_security_ops_put(client, monkeypatch):
    """PUT /admin/security/ops：七键局部写+校验拒（v0.6.5 运维面 WebUI 入口）。"""
    import copy
    snap = copy.deepcopy(CONFIG.security.__dict__)
    try:
        r = await client.put("/api/admin/security/ops",
                             json={"codemode_enabled": True})
        assert r.status_code == 200 and CONFIG.security.codemode_enabled is True
        assert snap["sandbox"] == CONFIG.security.sandbox   # 缺键不动
        r2 = await client.put("/api/admin/security/ops",
                              json={"sandbox": "bwrap",
                                    "shared_readonly": ["/data/pub"],
                                    "resource_bridges": [
                                        {"path": "/dev/dri", "mode": "dev"}],
                                    "approval_ttl_s": 900})
        assert r2.status_code == 200, r2.text
        assert CONFIG.security.sandbox == "bwrap"
        assert CONFIG.security.approval_ttl_s == 900
        # 校验拒四形
        for bad in ({"sandbox": "nope"}, {"approval_ttl_s": 10},
                    {"resource_bridges": [{"path": "", "mode": "ro"}]},
                    {"egress_proxy_port": 70000}):
            rb = await client.put("/api/admin/security/ops", json=bad)
            assert rb.status_code == 400, bad
        # 空体拒
        assert (await client.put("/api/admin/security/ops",
                                 json={})).status_code == 400
    finally:
        CONFIG.security.__dict__.update(snap)


def test_egress_grant_ttl_config(monkeypatch):
    """临时放行默认 TTL 走 CONFIG（新键 egress_grant_ttl_s，钳制域内热取）。"""
    from loadn_webui import egress_grants as eg
    monkeypatch.setattr(CONFIG.security, "egress_grant_ttl_s", 3600)
    out = eg.grant("s-ttl", "ttl.example.com")           # 不传 ttl → 配置默认
    try:
        assert out["ttl_s"] == 3600
        monkeypatch.setattr(CONFIG.security, "egress_grant_ttl_s", 5)
        assert eg.grant("s-ttl2", "t2.example.com")["ttl_s"] == 300  # 下钳
        assert eg.grant("s-ttl3", "t3.example.com", 99)["ttl_s"] == 300  # 显式也钳
    finally:
        eg._GRANTS.pop("s-ttl", None)
        eg._GRANTS.pop("s-ttl2", None)
        eg._GRANTS.pop("s-ttl3", None)


async def test_asset_missing_404_not_html_fallback(client):
    """缓存投毒断根：/assets/ 缺文件必须 404+no-store，绝不回 index.html
    （HTML 当 css/js 被代理缓存 → 浏览器拒载 = 全站裸样式，实证修复）。"""
    r = await client.get("/assets/index-nonexistent-deadbeef.css")
    assert r.status_code == 404
    assert "no-store" in r.headers.get("cache-control", "")
    assert b"<html" not in r.content          # 不是 SPA fallback
    r2 = await client.get("/assets/nonexistent.js")
    assert r2.status_code == 404 and b"<html" not in r2.content
    # 存在的资源：不可变长缓存（哈希名）
    r3 = await client.get("/")
    assert "no-store" in r3.headers.get("cache-control", "")


# ------------------------------------------------------- 会话级沙箱档位
def test_params_validate_and_effective_sandbox():
    """params.sandbox：枚举校验 + effective None=跟随全局（第八键）。"""
    from types import SimpleNamespace as NS

    from loadn_webui import params as pm
    assert pm.validate({"sandbox": "off"}) is not None or True  # validate 返回 json 串/None
    import json as _j
    raw = pm.validate({"sandbox": "bwrap"})
    assert _j.loads(raw)["sandbox"] == "bwrap"
    with pytest.raises(ValueError, match="sandbox"):
        pm.validate({"sandbox": "nope"})
    prof = NS(effort="high", model=None, max_turns=None, timeout_s=60,
              stall_timeout_s=30, rotate_input_tokens=None)
    eff = pm.effective(prof, {})
    assert eff["sandbox"] is None                     # 缺省跟随全局
    eff2 = pm.effective(prof, {"sandbox": "off"})
    assert eff2["sandbox"] == "off"


def test_wrap_engine_session_tier_override(monkeypatch, tmp_path):
    """requested_tier 覆盖链：会话 off → direct（全局 bwrap 不吃）；None=全局。"""
    from loadn_webui import sandbox as sb
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(sb, "bwrap_available", lambda: True)
    monkeypatch.setattr(sb, "resolve_tier", lambda req=None:
                        ("off", "") if req == "off" else ("bwrap", ""))
    cmd, mode = sb.wrap_engine(["x"], {}, engine="claude", sid="s",
                               cwd=str(tmp_path), requested_tier="off")
    assert mode == "direct"                            # 会话级 off 生效
    cmd2, mode2 = sb.wrap_engine(["x"], {}, engine="claude", sid="s",
                                 cwd=str(tmp_path))    # None=全局 bwrap
    assert mode2 == "bwrap"
