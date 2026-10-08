"""多用户鉴权（八轮批1）：安装向导/登录/属主隔离——内核走 API 直测，
浏览器层对赌登录门渲染。

cookie 通道全链（Set-Cookie → 中间件 → current_user → 属主收口 404）
全部经真 HTTP（httpx 持 cookie）验证；浏览器验证安装向导/登录门真渲染。
零 token：账号密码 + fake 引擎。
"""
from __future__ import annotations

import httpx
import pytest

pytest.importorskip("playwright", reason="playwright 未装")

pytestmark = [pytest.mark.coverage("e2e.auth")]


async def test_auth_kernel_and_owner_isolation(server_url):
    """API 直测：setup 建号+legacy 归并 → 登录发 cookie → 未登录探测 →
    属主隔离（列表过滤/详情 404）→ admin 全见 → 越权建会话不落他人。"""
    async with httpx.AsyncClient(base_url=server_url, timeout=10) as anon:
        st = (await anon.get("/api/auth/status")).json()
        assert st["needs_setup"] is True
        r = await anon.post("/api/auth/setup", json={
            "username": "admin", "password": "admin-pass-123"})
        assert r.status_code == 200, r.text
        # setup 即登录（cookie 已发）
        assert "loadn_session" in r.cookies

    admin = httpx.AsyncClient(base_url=server_url, timeout=10, cookies=r.cookies)
    try:
        # admin 建会话（cookie 通道 → owner_id 落 admin）
        ra = await admin.post("/api/sessions", json={"title": "admin的任务"})
        sid_admin = ra.json()["session"]["id"]
        # admin 建 userb（存量分支：admin cookie 放行）
        rb = await admin.post("/api/auth/setup", json={
            "username": "userb", "password": "userb-pass-123"})
        assert rb.status_code == 200, rb.text
    finally:
        await admin.aclose()

    # B 登录（独立 cookie 罐）
    userb = httpx.AsyncClient(base_url=server_url, timeout=10)
    try:
        rl = await userb.post("/api/auth/login", json={
            "username": "userb", "password": "userb-pass-123"})
        assert rl.status_code == 200
        me = (await userb.get("/api/auth/me")).json()
        assert me["username"] == "userb" and me["role"] == "user"
        # ④ 属主隔离双面
        sids_b = (await userb.get("/api/sessions")).json()["sessions"]
        assert sid_admin not in [s["id"] for s in sids_b], "列表须过滤"
        assert (await userb.get(
            f"/api/sessions/{sid_admin}")).status_code == 404, "越权=404"
        # ⑤ B 建自己的可见
        rb2 = await userb.post("/api/sessions", json={"title": "B的任务"})
        sid_b = rb2.json()["session"]["id"]
        sids_b2 = (await userb.get("/api/sessions")).json()["sessions"]
        assert sid_b in [s["id"] for s in sids_b2]
        # B 非管理面写被拒（admin 面 403）
        assert (await userb.post(
            "/api/admin/target-policy",
            json={"match": "x.com", "kind": "host", "mode": "always"}
        )).status_code == 403
    finally:
        await userb.aclose()

    # ⑥ admin 全见（含 B 的）；登出后会话失效
    admin2 = httpx.AsyncClient(base_url=server_url, timeout=10,
                               cookies=r.cookies)
    try:
        sids_a = (await admin2.get("/api/sessions")).json()["sessions"]
        ids_a = [s["id"] for s in sids_a]
        assert sid_admin in ids_a and sid_b in ids_a
        await admin2.post("/api/auth/logout")
        me2 = await admin2.get("/api/auth/me")
        assert me2.status_code == 401, "登出后会话必须失效"
    finally:
        await admin2.aclose()


async def test_browser_auth_gate_renders(server_url):
    """浏览器层对赌：首访（账号体系已建）自动弹**登录门**（auth_required
    主动弹——不靠等第一个 401）；输错密码显示真实错误。"""
    from playwright.async_api import async_playwright

    async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
        st = (await c.get("/api/auth/status")).json()
    if st["needs_setup"]:
        pytest.skip("内核测试在前会建号；单独跑本测试时无账号体系")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            page.set_default_timeout(15000)
            await page.goto(server_url)
            await page.wait_for_selector("text=登录")
            await page.fill('[placeholder="用户名"]', "nobody")
            await page.fill('[placeholder="密码"]', "wrong-pass")
            await page.click('button:has-text("登录")')
            await page.wait_for_selector("text=不正确")
        finally:
            await browser.close()
