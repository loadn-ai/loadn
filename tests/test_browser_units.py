"""T5：browser_mcp 工具面单测（31.5%→70%+）。

playwright CDP 是外部依赖——fake page 注入 _SESSION（stub 依赖非 mock
被测物：被测的是域策略/审计/文案/参数传递逻辑）。协议面（stdio server）
已由 tests/security/test_browser_cua.py 子进程用例覆盖，不重复。
"""
from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from loadn_webui.config import CONFIG
from loadn_webui.integrations import browser_mcp as bm

_PNG1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBg"
    "AAAABQABh6FO1AAAAABJRU5ErkJggg==")


class FakePage:
    def __init__(self, logs=None, title="测试页"):
        self.calls = []
        self._logs = logs
        self._title = title

    def goto(self, url, **kw):
        self.calls.append(("goto", url, kw))
        return None

    def title(self):
        return self._title

    def click(self, sel, **kw):
        self.calls.append(("click", sel, kw))

    def fill(self, sel, val, **kw):
        self.calls.append(("fill", sel, val, kw))

    def screenshot(self, path, **kw):
        self.calls.append(("screenshot", path, kw))
        Path(path).write_bytes(_PNG1PX)

    def evaluate(self, expr):
        self.calls.append(("evaluate", expr))
        return self._logs


@pytest.fixture()
def fake_session(monkeypatch):
    def _mount(page: FakePage):
        monkeypatch.setattr(bm, "_SESSION", SimpleNamespace(page=lambda: page))
    return _mount


# ---------------------------------------------------------------- tool_open
def test_open_local_zone_allows(fake_session):
    page = FakePage()
    fake_session(page)
    out = bm.tool_open({"url": "http://localhost:5173/"})
    assert "已打开" in out and "测试页" in out
    assert page.calls[0][0] == "goto"
    assert page.calls[0][1] == "http://localhost:5173/"
    assert page.calls[0][2]["wait_until"] == "domcontentloaded"


def test_open_whitelisted_public_domain(fake_session, monkeypatch):
    monkeypatch.setattr(CONFIG.security, "egress_allow",
                        ["trusted.example"])
    page = FakePage(title="公网站点")
    fake_session(page)
    out = bm.tool_open({"url": "https://trusted.example/x"})
    assert "公网站点" in out


def test_open_rejects_non_allowlisted(fake_session):
    page = FakePage()
    fake_session(page)
    out = bm.tool_open({"url": "https://evil.example/"})
    assert "不在出口白名单" in out and "绕网后门" in out
    assert page.calls == []                        # 未碰浏览器（先拒后动）


def test_open_rejects_bad_url(fake_session):
    page = FakePage()
    fake_session(page)
    out = bm.tool_open({"url": "https://"})
    assert "URL 非法" in out
    assert page.calls == []


# ---------------------------------------------------------------- 其余工具
def test_click_and_fill_pass_through(fake_session):
    page = FakePage()
    fake_session(page)
    assert "已点击" in bm.tool_click({"selector": "#go"})
    assert "已填写" in bm.tool_fill({"selector": "#q", "value": "关键词"})
    assert ("click", "#go", (("timeout", 10000),)) or \
        page.calls[0][0] == "click" and page.calls[0][1] == "#go"
    fill_call = next(c for c in page.calls if c[0] == "fill")
    assert fill_call[1] == "#q" and fill_call[2] == "关键词"


def test_screenshot_base64_roundtrip(fake_session):
    page = FakePage()
    fake_session(page)
    out = bm.tool_screenshot({"full_page": True})
    assert out.startswith("data:image/png;base64,")
    assert base64.b64decode(out.split(",", 1)[1]) == _PNG1PX
    shot = next(c for c in page.calls if c[0] == "screenshot")
    assert shot[2].get("full_page") is True        # 参数透传


def test_read_console_logs_and_empty(fake_session):
    fake_session(FakePage(logs=[{"level": "error", "text": "boom"}]))
    out = bm.tool_read_console({})
    assert "boom" in out
    fake_session(FakePage(logs=None))
    assert bm.tool_read_console({}) == "（无注入日志）"


def test_read_console_tail_50(fake_session):
    logs = [{"i": i} for i in range(80)]
    fake_session(FakePage(logs=logs))
    out = bm.tool_read_console({})
    import json
    assert json.loads(out) == logs[-50:]           # 只回尾 50 条


# ---------------------------------------------------------------- 会话对象
def test_session_ensure_requires_cdp(monkeypatch):
    """cdp 未配置 → RuntimeError（T5 修：先于 playwright import 的 fail-fast）。"""
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "")
    s = bm.BrowserSession()
    with pytest.raises(RuntimeError, match="cdp_url 未配置"):
        s._ensure()


def test_session_close_tolerates_failures():
    """close 三段全容错（任何一段抛异常也全清——会话可重建）。"""
    s = bm.BrowserSession()

    class _Boom:
        def close(self):
            raise RuntimeError("炸")

    s._page = _Boom()
    s._browser = _Boom()
    s._pw = _Boom()
    s.close()                                      # 不抛
    assert s._page is None and s._browser is None and s._pw is None


def test_session_ensure_reuse():
    s = bm.BrowserSession()
    s._browser = object()                          # 已连接：直接复用零重建
    assert s._ensure() is None and s._browser is not None


# ---------------------------------------------------------------- 审批挂卡
async def test_require_approval_creates_card(tmp_path, monkeypatch):
    """公网域首开 → 审批卡挂起且本轮拒（headless fail-closed）。"""
    monkeypatch.setattr("loadn_webui.security.approve._ensured", False)
    sid = "br-appr-1"
    out = await bm._require_approval("novel.example", sid)
    assert out is False                            # 本轮拒
    from loadn_webui.security import approve as ap
    rows = ap.list_pending(sid)
    assert rows and rows[0]["action_type"] == "browser_open"
    assert "novel.example" in rows[0]["summary"]


# ---------------------------------------------------------------- 能力门
def test_tools_payload_empty_without_playwright(monkeypatch):
    """能力门对赌（2026-10-09 生产实证）：venv 缺 playwright 时 tools/list
    必须空表——死工具不上工具面（9 工具广告+调用必死曾诱发逐像素 50 连败
    搅动循环）；能力在时全量广告。"""
    monkeypatch.setattr(bm, "_playwright_ok", lambda: False)
    assert bm._tools_payload() == []
    monkeypatch.setattr(bm, "_playwright_ok", lambda: True)
    names = [t["name"] for t in bm._tools_payload()]
    assert names and "browser_click" in names
