"""P13 视觉 GUI 工具层（fake playwright 页面）：四工具链/敏感冻结→审批→
单步语义/预算超限/审计完整/自纠回图。

敏感冻结的「单步放行」语义：审批批准后**该单步**放行（冻结只对当次
动作解除，下一个动作重新冻结）——通过 URL 仍敏感+内存票已批的窄窗
验证（票由审批 decide 写入，本测试直接模拟批准面写票）。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from loadn_webui.config import PATHS
from loadn_webui.integrations import browser_mcp as bm


class FakeMouse:
    def __init__(self, log):
        self.log = log

    def click(self, x, y):
        self.log.append(("click", x, y))

    def wheel(self, x, y):
        self.log.append(("scroll", y))

    def type(self, text, delay=0):
        self.log.append(("type", len(text)))


class FakeKeyboard:
    def __init__(self, log):
        self.log = log

    def type(self, text, delay=0):
        self.log.append(("type", len(text)))


class FakePage:
    def __init__(self, url="https://example.com/page"):
        self.url = url
        self.log: list = []
        self.mouse = FakeMouse(self.log)
        self.keyboard = FakeKeyboard(self.log)
        self.shots = 0

    def screenshot(self, path=None, full_page=False, **_):
        self.shots += 1
        self.log.append(("shot", full_page))
        from pathlib import Path
        Path(path).write_bytes(b"\x89PNG-fake")


@pytest.fixture(autouse=True)
def _fake_session(monkeypatch, tmp_path):
    monkeypatch.setenv("LOADN_BROWSER_SID", "")
    page = FakePage()
    monkeypatch.setattr(bm, "_SESSION", type("S", (), {
        "page": lambda self: page, "close": lambda self: None})())
    bm._budget.update(screenshot=0, click=0)     # 预算每测试重置
    yield page


def _cua_audit() -> list[dict]:
    p = PATHS["var"] / "audit.db"
    if not p.exists():
        return []
    out = []
    with sqlite3.connect(p) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'audit_events%'")]
        for t in sorted(tables):
            for r in c.execute(f"SELECT detail_json FROM {t} "
                               "WHERE type='browser_cua' ORDER BY rowid"):
                out.append(json.loads(r[0]))
    return out


# ---------------------------------------------------------------- ① 四工具链
def test_four_tools_chain(_fake_session):
    page = _fake_session
    # screenshot：纯视觉（viewport 非 full_page；返回 base64 data URI）
    data = bm.tool_p13_screenshot({})
    assert data.startswith("data:image/png;base64,")
    assert ("shot", False) in page.log            # viewport（无 DOM 注入）
    # scroll
    assert "300" in bm.tool_p13_scroll({"dy": 300})
    assert ("scroll", 300) in page.log
    # type（非敏感页直过）
    assert "5 字符" in bm.tool_p13_type({"text": "hello"})
    assert ("type", 5) in page.log
    # click：坐标点击 + 自动补 screenshot 自纠
    out = bm.tool_p13_click({"x": 120, "y": 40})
    assert ("click", 120, 40) in page.log
    assert out.startswith("data:image/png;base64,")   # 自纠回图
    # 负坐标拒
    with pytest.raises(RuntimeError, match="非负像素坐标"):
        bm.tool_p13_click({"x": -1, "y": 0})
    # 审计完整：每次视觉动作（坐标/域名/结果）入账本
    acts = [a["action"] for a in _cua_audit()]
    assert {"screenshot", "click", "type", "scroll"} <= set(acts)
    click_ev = next(a for a in _cua_audit()
                    if a["action"] == "click" and a.get("x") == 120)
    assert click_ev["domain"] == "example.com"


# ---------------------------------------------------------------- ② 敏感冻结
def test_sensitive_freeze_then_approval_single_step(monkeypatch, _fake_session):
    page = _fake_session
    page.url = "https://bank.example.com/pay?amount=100"
    n0 = len(_cua_audit())
    with pytest.raises(RuntimeError, match="敏感页冻结"):
        bm.tool_p13_click({"x": 10, "y": 10})
    assert ("click", 10, 10) not in page.log      # 未执行
    evs = _cua_audit()[n0:]
    assert any(e["action"] == "sensitive_freeze" for e in evs)
    # 审批请求已建（browser_open 类型；批准面由用户走——单步票窄窗在
    # 批准后仍重冻结下一个动作：即本工具永不连续放行两步敏感操作）
    with pytest.raises(RuntimeError, match="敏感页冻结"):
        bm.tool_p13_type({"text": "123456"})      # 紧接的第二步同样冻结
    assert ("type", 6) not in page.log
    # 非敏感 URL 不冻结
    page.url = "https://example.com/normal"
    assert "3 字符" in bm.tool_p13_type({"text": "abc"})
    # screenshot 只读不吃冻结（敏感页也能看，不能动）
    page.url = "https://bank.example.com/pay"
    assert bm.tool_p13_screenshot({}).startswith("data:image/png")


# ---------------------------------------------------------------- ③ 预算
def test_budget_exceeded(monkeypatch, _fake_session):
    monkeypatch.setattr(bm, "MAX_SCREENSHOTS", 3)
    monkeypatch.setattr(bm, "MAX_CLICKS", 2)
    page = _fake_session
    for _ in range(3):
        bm.tool_p13_screenshot({})
    with pytest.raises(RuntimeError, match="截图预算耗尽"):
        bm.tool_p13_screenshot({})
    bm.tool_p13_click({"x": 1, "y": 1})
    bm.tool_p13_click({"x": 2, "y": 2})
    with pytest.raises(RuntimeError, match="点击预算耗尽"):
        bm.tool_p13_click({"x": 3, "y": 3})
    assert ("click", 3, 3) not in page.log


# ---------------------------------------------------------------- ④ MCP 面
def test_mcp_tools_list_has_vision_tools():
    """TOOLS 表注册四视觉工具（tools/list 面——引擎经 .mcp.json 可见）。"""
    names = {v[0] for v in bm.TOOLS.values()}
    assert {"browser_screenshot", "browser_click", "browser_type",
            "browser_scroll"} <= names
    # click 描述带敏感冻结提示（模型面知情）
    click_desc = next(v[2] for v in bm.TOOLS.values() if v[0] == "browser_click")
    assert "冻结" in click_desc or "敏感" in click_desc


def test_mutation_blind_spots_budget_edges(monkeypatch, _fake_session):
    """突变补杀：预算边界恰好可越（=MAX 允许、+1 拒）；text 空/缺拒；
    自纠回图不越预算（预算尽时退文本提示）。"""
    monkeypatch.setattr(bm, "MAX_SCREENSHOTS", 2)
    monkeypatch.setattr(bm, "MAX_CLICKS", 1)
    page = _fake_session
    # 边界：第 2 张（=MAX）仍允许
    assert bm.tool_p13_screenshot({}).startswith("data:image/png")
    assert bm.tool_p13_screenshot({}).startswith("data:image/png")
    with pytest.raises(RuntimeError, match="截图预算耗尽"):
        bm.tool_p13_screenshot({})
    # click=1 次预算 + 截图已尽：点击成功但自纠退文本提示（不炸不越预算）
    out = bm.tool_p13_click({"x": 5, "y": 5})
    assert out == "已点击（截图预算已尽，无法自动回图）"
    assert ("click", 5, 5) in page.log
    with pytest.raises(RuntimeError, match="点击预算耗尽"):
        bm.tool_p13_click({"x": 6, "y": 6})
    # 空文本拒
    with pytest.raises(RuntimeError, match="text 不能为空"):
        bm.tool_p13_type({"text": ""})
    with pytest.raises(RuntimeError, match="text 不能为空"):
        bm.tool_p13_type({})
    # dy=0 合法（原地滚动零像素也是动作）
    assert bm.tool_p13_scroll({"dy": 0}) is not None


def test_fix_single_step_ticket_release(monkeypatch, _fake_session, tmp_path):
    """复查修#4 对赌：批准落票 → 冻结验票放行该单步（一次性消费）。"""
    import time as _t
    page = _fake_session
    page.url = "https://bank.example.com/pay"
    ticket = tmp_path / ".loadn" / "browser-allow-once.json"
    ticket.parent.mkdir(parents=True, exist_ok=True)
    ticket.write_text(json.dumps(
        {"host": "bank.example.com", "exp": _t.time() + 300}))
    monkeypatch.chdir(tmp_path)                  # MCP server cwd=工作区
    # 票在 → 该步放行（click 执行 + 自纠回图）
    out = bm.tool_p13_click({"x": 9, "y": 9})
    assert ("click", 9, 9) in page.log
    assert not ticket.exists()                   # 票已消费
    evs = [a["action"] for a in _cua_audit()]
    assert "single_step_released" in evs
    # 下一步（无票）重新冻结
    with pytest.raises(RuntimeError, match="敏感页冻结"):
        bm.tool_p13_type({"text": "pwd"})
    # 过期票不放行
    ticket.write_text(json.dumps(
        {"host": "bank.example.com", "exp": _t.time() - 1}))
    with pytest.raises(RuntimeError, match="敏感页冻结"):
        bm.tool_p13_type({"text": "pwd2"})
    # 异域票不放行
    ticket.write_text(json.dumps(
        {"host": "other.com", "exp": _t.time() + 300}))
    with pytest.raises(RuntimeError, match="敏感页冻结"):
        bm.tool_p13_type({"text": "pwd3"})


def test_r2_budget_reset_on_open(_fake_session, monkeypatch):
    """二轮修#19 对赌：新导航=新任务面——预算随 open 重置（原进程级计数，
    第二个任务的预算被第一个任务吃剩的腰斩）；敏感冻结拒步不烧预算。"""
    monkeypatch.setattr(bm, "MAX_SCREENSHOTS", 1)
    monkeypatch.setattr(bm, "MAX_CLICKS", 1)
    page = _fake_session
    # FakePage 无 goto——open 的导航动作打桩（重置逻辑不依赖导航结果）
    page.goto = lambda *a, **k: None
    page.title = lambda: "fake"
    bm._budget.update(screenshot=1, click=1)      # 模拟上一任务吃满
    with pytest.raises(RuntimeError, match="截图预算耗尽"):
        bm.tool_p13_screenshot({})                # 确认旧预算确实拦着
    bm.tool_open({"url": "http://localhost:5173/"})   # open → 重置
    assert bm._budget == {"screenshot": 0, "click": 0}
    assert bm.tool_p13_screenshot({}).startswith("data:image/png")   # 新任务可用
    # 冻结拒步不烧预算：把 click 预算塞满后走 _p13_page 拒步路径在
    # 预算检查之后（先预算后冻结的次序对赌见 tool_p13_click 源序）
    bm._budget["click"] = bm.MAX_CLICKS           # 塞满 → 预算门先拒
    with pytest.raises(RuntimeError, match="点击预算耗尽"):
        bm.tool_p13_click({"x": 1, "y": 1})
