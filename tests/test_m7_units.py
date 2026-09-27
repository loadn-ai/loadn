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
