"""浏览器级端到端（playwright chromium headless + 真 uvicorn + fake 引擎）。

补 UI 运行时面：SSE 事件订阅/分发、resync 快照消费——这些只有真浏览器
渲染才能验证（六轮实证：resync 空载荷清空会话是纯前端运行时 bug，类型
检查与 build 都抓不住）。零 token：conftest 顶层 LOADN_CLAUDE_BIN=fake。

旅程：打开首页 → 新建任务 → 发消息 → 等 fake 引擎的 assistant 回复渲染
（SSE 流面）→ API 侧删最后一条消息（浏览器保持 SSE 连接）→ 断言其余
消息仍在渲染（resync 快照消费不清空——六轮修 A1 的运行时对赌）。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

pytest.importorskip("playwright", reason="playwright 未装（pip install playwright）")

pytestmark = [pytest.mark.coverage("e2e.ui")]


async def _latest_sid(base: str) -> str:
    async with httpx.AsyncClient(base_url=base, timeout=10) as c:
        d = (await c.get("/api/sessions")).json()
        return d["sessions"][0]["id"]


async def test_browser_sse_stream_and_resync_keeps_state(server_url, ws_root,
                                                         logged_context):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await logged_context(browser)).new_page()
            page.set_default_timeout(20000)
            await page.goto(server_url)
            # ① 侧栏渲染（token 宽限期内无凭证放行）
            await page.wait_for_selector("text=新任务")
            # ② 新建任务 + 发消息
            await page.click("text=新任务")
            await page.wait_for_selector("textarea.cinput")
            await page.fill("textarea.cinput", "浏览器e2e锚点句")
            await page.keyboard.press("Enter")
            # ③ SSE 流面：user 消息渲染 + fake 引擎回显到达（事件流驱动）
            await page.wait_for_selector("text=浏览器e2e锚点句")
            sid = await _latest_sid(server_url)
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - t0 < 20:
                async with httpx.AsyncClient(base_url=server_url,
                                             timeout=10) as c:
                    d = (await c.get(f"/api/sessions/{sid}")).json()
                turns = d["turns"]
                if turns and turns[-1]["status"] in ("done", "error"):
                    break
                await asyncio.sleep(0.5)
            else:
                pytest.fail("浏览器会话的 turn 未终态")
            # ④ SSE 消费端就位证据：聊天区渲染了 assistant 回复
            await asyncio.sleep(1.0)          # 等 turn_done 后的渲染收尾
            # ⑤ API 侧删最后一条（assistant 回显）——浏览器 SSE 收 resync
            async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
                d = (await c.get(f"/api/sessions/{sid}")).json()
                mid = d["messages"][-1]["id"]
                r = await c.delete(f"/api/messages/{mid}")
                assert r.status_code == 200
            # ⑥ resync 快照消费面：user 锚点句**仍在页面**（六轮前的旧形态：
            # 空 resync 会把 messages 清成空，整屏消失）
            await asyncio.sleep(1.5)
            body_text = await page.inner_text("body")
            assert "浏览器e2e锚点句" in body_text, \
                "resync 后消息流不得清空（A1 运行时对赌失败）"
        finally:
            await browser.close()


async def test_browser_approval_card_live(server_url, ws_root,
                                          logged_context):
    """审批卡实时出现（六轮修 A2 的浏览器对赌）：agent 侧发起审批 →
    SSE approval 事件 → 前端 loadApprovals → 卡片渲染——**不刷新页面**。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await logged_context(browser)).new_page()
            page.set_default_timeout(20000)
            await page.goto(server_url)
            await page.wait_for_selector("text=新任务")
            await page.click("text=新任务")
            await page.wait_for_selector("textarea.cinput")
            sid = await _latest_sid(server_url)
            # agent 侧发起审批（approve.create 经钩子推 SSE——直接驱动）
            from loadn_webui.engine import ENGINE
            from loadn_webui.security import approve as approve_mod
            out = approve_mod.create(sid, "mail_send", {"to": "e2e@x.y"})
            ENGINE.publish(sid, "approval", {"kind": "request", **out})
            # 审批卡在不刷新的页面上渲染（旧形态：事件未订阅永不出现）
            await page.wait_for_selector("text=需确认", timeout=15000)
            await page.wait_for_selector("text=发邮件", timeout=15000)
        finally:
            await browser.close()


async def test_browser_always_allow_creates_policy(server_url, ws_root,
                                                         logged_context):
    """七轮 P10 补 UI 对赌：审批卡「始终允许·确切」→ from-approval →
    策略落库（list 可见）——此前 per-target 三档全链零 UI。
    from-approval 在 admin 面：先往浏览器注入 token（前端 api() 自动带
    双头——生产用户配 token 后即此形态）。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await logged_context(browser)).new_page()
            page.set_default_timeout(20000)
            await page.goto(server_url)
            await page.wait_for_selector("text=新任务")
            await page.click("text=新任务")
            await page.wait_for_selector("textarea.cinput")
            sid = await _latest_sid(server_url)
            from loadn_webui.engine import ENGINE
            from loadn_webui.security import approve as approve_mod
            out = approve_mod.create(sid, "mail_send", {"to": "always@x.y"})
            ENGINE.publish(sid, "approval", {"kind": "request", **out})
            await page.wait_for_selector("text=需确认", timeout=15000)
            await page.click("text=始终允许·确切")
            await page.wait_for_selector("text=已建常设放行策略", timeout=10000)
            async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
                d = (await c.get("/api/admin/target-policy")).json()
            assert any(p["created_from"] == f"approval:{out['id']}"
                       for p in d["policies"]), d["policies"]
        finally:
            await browser.close()


async def test_browser_subagent_dispatch_card_and_chips(server_url, ws_root,
                                                        logged_context):
    """多 Agent 工作台 UI 对赌（rev10 概念模型更新）：.fake/subagent 场景 →
    SSE 带归属字段 → 派发卡（人名+状态）+ agent chips 渲染；agent 不再占
    tab（tab=子任务）——子代理工具流水在主控合集里可见。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await logged_context(browser)).new_page()
            page.set_default_timeout(20000)
            await page.goto(server_url)
            await page.wait_for_selector("text=新任务")
            await page.click("text=新任务")
            await page.wait_for_selector("textarea.cinput")
            sid = await _latest_sid(server_url)
            ctrl = ws_root / sid / ".fake"
            ctrl.mkdir(parents=True, exist_ok=True)
            (ctrl / "subagent").touch()
            await page.fill("textarea.cinput", "派两个子代理")
            await page.keyboard.press("Enter")
            # 派发卡：引擎人名渲染（马洛）
            await page.wait_for_selector(".dispatch-card:has-text('马洛')")
            # agent chips 出现；agent 视图按需开——初始无 agent tab
            await page.wait_for_selector(".chip-agent:has-text('马洛')")
            assert await page.locator(".mtab.agent").count() == 0
            # 点击 chip → 该 agent 的钻取视图（tab + 头部 + 工具流水）
            await page.click(".chip-agent:has-text('马洛')")
            await page.wait_for_selector(".mtab.agent:has-text('马洛')")
            await page.wait_for_selector(".agent-head:has-text('马洛')")
            await page.wait_for_selector(".agent-tab .tool-card .tool-subtag:"
                                         "has-text('子1')")
        finally:
            await browser.close()


async def test_browser_subtask_tab_appears(server_url, ws_root, logged_context,
                                           monkeypatch):
    """rev10 子任务自动分类 UI 对赌：mock 分类器（in-process monkeypatch 对
    同进程 uvicorn 线程生效）→ 发消息 → SSE subtask 事件 → 子任务 tab 弹出
    → 点击看筛选视图（该 turn 的用户消息+agent 活动都在 tab 内）。"""
    from playwright.async_api import async_playwright

    from loadn_webui.config import CONFIG
    from loadn_webui.integrations import titlegen

    old_enabled, old_key = CONFIG.titlegen.enabled, CONFIG.titlegen.api_key

    async def fake_chat(system, user, max_tokens=64):
        return '{"type":"task","new_title":"调研竞品"}'

    monkeypatch.setattr(titlegen, "_chat", fake_chat)
    CONFIG.titlegen.enabled = True
    CONFIG.titlegen.api_key = "fake-key"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await logged_context(browser)).new_page()
            page.set_default_timeout(20000)
            await page.goto(server_url)
            await page.wait_for_selector("text=新任务")
            await page.click("text=新任务")
            await page.wait_for_selector("textarea.cinput")
            ctrl = ws_root / (await _latest_sid(server_url)) / ".fake"
            ctrl.mkdir(parents=True, exist_ok=True)
            (ctrl / "subagent").touch()
            await page.fill("textarea.cinput", "帮我深度调研竞品并写报告")
            await page.keyboard.press("Enter")
            # 子任务 tab 弹出（分类器打标 → SSE subtask → turns patch）
            await page.wait_for_selector(".mtab.subtask:has-text('调研竞品')")
            # 点击 → 筛选视图：该 turn 的用户消息在 tab 内
            await page.click(".mtab.subtask:has-text('调研竞品')")
            await page.wait_for_selector(".agent-tab .msg.user .bubble:"
                                         "has-text('深度调研竞品')")
            # 派发卡也在子任务视图内（agent 活动归组）
            await page.wait_for_selector(".agent-tab .dispatch-card:has-text('马洛')")
        finally:
            CONFIG.titlegen.enabled = old_enabled
            CONFIG.titlegen.api_key = old_key
            await browser.close()
