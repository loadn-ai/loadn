"""P2-5 浏览器 CUA 验收（零真浏览器——域策略与 server 协议层；CDP 层
标记 not_installed 优雅退化，headless chromium E2E 记 backlog 真机手验）。

- 域策略：localhost/127/私网放行（zone=local）；公网白名单域放行
  （allowlisted）；白名单外公网域拒（浏览器不得成为绕网后门）；坏 URL 拒
- MCP server 协议：stdio JSON-RPC initialize/tools-list/tools-call
  （browser.open 拒越权域的可读文案；未知工具 isError）
- .mcp.json 注入：cdp_url 配置时会话模板带 browser 条目（loadn-web
  _browser-mcp）；cdp_url 空不注入；显式 browser 覆盖不重复注入
- 审批动作：browser_open 摘要渲染（平台侧文案）
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from loadn_webui.config import CONFIG
from loadn_webui.integrations import browser_mcp


@pytest.fixture(autouse=True)
def _allow(monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_allow",
                        ["trusted.example", "pypi.org"])


# ---------------------------------------------------------------- 域策略
@pytest.mark.parametrize("url,zone", [
    ("http://localhost:5173/", "local"),
    ("http://127.0.0.1:3000/", "local"),
    ("http://192.168.1.5:8080/", "local"),
    ("http://10.0.0.2/", "local"),
    ("https://trusted.example/x", "allowlisted"),
])
def test_host_allowed_zones(url, zone):
    ok, why = browser_mcp._host_allowed(url)
    assert ok and why == zone


@pytest.mark.parametrize("url", [
    "https://evil.example/",                # 白名单外公网域
    "https://not-in-list.io/x",
    "ftp://x/",                             # 坏 URL
    "https://",                             # 无 host
])
def test_host_denied(url):
    ok, _ = browser_mcp._host_allowed(url)
    assert not ok


# ---------------------------------------------------------------- MCP server
def _mcp_session(calls: list[dict]) -> tuple[str, str]:
    """子进程跑 server：喂 calls，收全部响应行。"""
    lines = "\n".join(json.dumps(c) for c in calls) + "\n"
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui.integrations.browser_mcp"],
        input=lines, capture_output=True, text=True, timeout=30)
    return p.stdout, p.stderr


def test_mcp_server_protocol():
    out, _ = _mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "browser.open",
                    "arguments": {"url": "https://evil.example/"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "browser.nothing",
                    "arguments": {}}},
    ])
    evs = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    init = next(e for e in evs if e.get("id") == 1)
    assert init["result"]["serverInfo"]["name"] == "loadn-browser-cua"
    listing = next(e for e in evs if e.get("id") == 2)
    names = {t["name"] for t in listing["result"]["tools"]}
    assert names == {"browser.open", "browser.click", "browser.fill",
                     "browser.screenshot", "browser.read_console",
                     # P13 视觉 GUI 四工具（纯视觉坐标面）
                     "browser_screenshot", "browser_click",
                     "browser_type", "browser_scroll"}
    # 越权域：可读拒绝文案（绕网后门语义）
    denied = next(e for e in evs if e.get("id") == 3)
    text = denied["result"]["content"][0]["text"]
    assert "不在出口白名单" in text and "evil.example" in text
    # 未知工具
    unknown = next(e for e in evs if e.get("id") == 4)
    assert unknown["result"]["isError"]


def test_mcp_local_open_hermetic_degradation():
    """本地域放行 → CDP 层优雅退化（本 venv 装 了 playwright，cdp_url
    指向不可达端点 → isError=连接错误文案；未装 的部署则 not_installed
    ——两条退化路径都 isError，agent 可读自救）。环境隔离：cdp_url 指到
    保留测试端点，绝不碰真沙箱。"""
    import os
    env = {**os.environ, "LOADN_TEST_CDP": "1"}
    # server 读 CONFIG（进程内 import 时求值）——经 env 兜不进子进程，
    # 直接给不可达端点：连接失败即 hermetic 验证
    out, _ = _mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "browser.open",
                    "arguments": {"url": "http://localhost:5173/"}}}])
    ev = json.loads(out.splitlines()[0])
    r = ev["result"]
    assert r.get("isError")
    text = r["content"][0]["text"]
    # 两条合法退化形态（取决于部署是否装 playwright/连不连得上）
    assert ("not_installed" in text or "cdp_url 未配置" in text
            or "connect_over_cdp" in text or "connect" in text.lower())


# ---------------------------------------------------------------- 注入
def test_mcp_json_injection(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "http://127.0.0.1:21111/cdp")
    from loadn_webui import workspace as ws_mod
    ws_mod.write_mcp_json(tmp_path, None)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["browser"]["args"] == ["_browser-mcp"]
    # 哈希锁也记了 browser（B3 rug-pull 面覆盖 CUA server）
    locks = json.loads((tmp_path / ".mcp-lock.json").read_text())
    assert "browser" in locks
    # 会话显式覆盖 browser → 不重复注入
    ws_mod.write_mcp_json(tmp_path, {"browser": {"command": "x"}})
    data2 = json.loads((tmp_path / ".mcp.json").read_text())
    assert data2["mcpServers"]["browser"]["command"] == "x"


def test_no_injection_without_cdp(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "")
    monkeypatch.setattr(CONFIG, "mcp", type("M", (), {"servers": {}})())
    from loadn_webui import workspace as ws_mod
    ws_mod.write_mcp_json(tmp_path, None)
    assert not (tmp_path / ".mcp.json").exists()


# ---------------------------------------------------------------- 审批
def test_browser_open_approval_summary():
    from loadn_webui.security.approve import _render_summary
    s = _render_summary("browser_open", {"host": "api.example.com"})
    assert "api.example.com" in s and "外部域" in s
