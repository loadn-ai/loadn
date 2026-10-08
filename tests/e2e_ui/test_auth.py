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


async def test_multiuser_batch2_isolation(server_url):
    """批2对赌：webhooks/projects/schedules/categories 的 owner 过滤+落主；
    用户管理面（admin 建号/禁用踢下线/防锁死自禁）；审批 decide 越权 404。"""
    async with httpx.AsyncClient(base_url=server_url, timeout=10) as anon:
        if (await anon.get("/api/auth/status")).json()["needs_setup"]:
            r = await anon.post("/api/auth/setup", json={
                "username": "admin2", "password": "admin2-pass-123"})
            assert r.status_code == 200
            admin_cookies = r.cookies
        else:
            admin_cookies = None
    # 复用批1 admin（全量序）
    if admin_cookies is None:
        async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
            r = await c.post("/api/auth/login", json={
                "username": "admin", "password": "admin-pass-123"})
            assert r.status_code == 200, r.text
            admin_cookies = r.cookies

    admin = httpx.AsyncClient(base_url=server_url, timeout=10,
                              cookies=admin_cookies)
    userc = httpx.AsyncClient(base_url=server_url, timeout=10)
    try:
        # admin 经管理面建 userc（批2 新面）
        r = await admin.post("/api/auth/users", json={
            "username": "userc", "password": "userc-pass-123"})
        assert r.status_code in (200, 409), r.text
        rl = await userc.post("/api/auth/login", json={
            "username": "userc", "password": "userc-pass-123"})
        assert rl.status_code == 200

        # ① webhook：userc 建的（owner 落主）——admin 可见，userc 自己可见
        rh = await userc.post("/api/hooks", json={
            "name": "c的钩", "prompt_template": "{{payload}}"})
        assert rh.status_code == 200, rh.text
        hid = rh.json()["hook"]["id"]
        # ② schedule：userc 建
        rs = await userc.post("/api/schedules", json={
            "kind": "new_session", "prompt": "p", "at": "2030-01-01 08:00"})
        assert rs.status_code == 200, rs.text
        jid = rs.json()["job"]["id"]
        # ③ category：userc 建
        rc = await userc.post("/api/categories", json={"name": "c的分类"})
        assert rc.status_code in (200, 409), rc.text

        # ④ 第三个用户（userd）看不到 userc 的 webhook/schedule/category
        r = await admin.post("/api/auth/users", json={
            "username": "userd", "password": "userd-pass-123"})
        assert r.status_code in (200, 409)
        userd = httpx.AsyncClient(base_url=server_url, timeout=10)
        await userd.post("/api/auth/login", json={
            "username": "userd", "password": "userd-pass-123"})
        try:
            hooks_d = (await userd.get("/api/hooks")).json()["hooks"]
            assert hid not in [h["id"] for h in hooks_d], "webhook 须过滤"
            jobs_d = (await userd.get("/api/schedules")).json()["schedules"]
            mine_d = [j for j in jobs_d if not j.get("is_system")]
            assert jid not in [j["id"] for j in mine_d], "job 须过滤"
            cats_d = (await userd.get("/api/categories")).json()["categories"]
            assert "c的分类" not in [c_["name"] for c_ in cats_d]
        finally:
            await userd.aclose()
        # admin 全见
        hooks_a = (await admin.get("/api/hooks")).json()["hooks"]
        assert hid in [h["id"] for h in hooks_a]
        jobs_a = (await admin.get("/api/schedules")).json()["schedules"]
        assert jid in [j["id"] for j in jobs_a]

        # ⑤ 审批 decide 越权：userc 会话的审批，userd 裁决 → 404
        rses = await userc.post("/api/sessions", json={"title": "c的任务"})
        sid_c = rses.json()["session"]["id"]
        from loadn_webui.security import approve as approve_mod
        out = approve_mod.create(sid_c, "mail_send", {"to": "c@x.y"})
        me_d_cookies = None       # userd 已关——用 admin 非属主？admin 放行。
        # userd 重新登一次做裁决面
        userd2 = httpx.AsyncClient(base_url=server_url, timeout=10)
        await userd2.post("/api/auth/login", json={
            "username": "userd", "password": "userd-pass-123"})
        try:
            rd = await userd2.post(f"/api/approvals/{out['id']}/decide",
                                   json={"approve": True})
            assert rd.status_code == 404, f"越权裁决须 404（{rd.status_code}）"
        finally:
            await userd2.aclose()

        # ⑥ 禁用即踢下线：userc 被禁 → 其 cookie 下次请求 401
        uid_c = next(u["id"] for u in
                     (await admin.get("/api/auth/users")).json()["users"]
                     if u["username"] == "userc")
        r = await admin.patch(f"/api/auth/users/{uid_c}",
                              json={"disabled": True})
        assert r.status_code == 200
        me_c = await userc.get("/api/auth/me")
        assert me_c.status_code == 401, "禁用后须立即失效"
        # ⑦ 防锁死：admin 不能禁用自己
        me_a = (await admin.get("/api/auth/me")).json()
        r = await admin.patch(f"/api/auth/users/{me_a['id']}",
                              json={"disabled": True})
        assert r.status_code == 400
        # 还原 userc（后续全量序）
        await admin.patch(f"/api/auth/users/{uid_c}", json={"disabled": False})
    finally:
        await admin.aclose()
        await userc.aclose()
