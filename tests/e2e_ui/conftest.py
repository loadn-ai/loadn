"""浏览器 e2e 公共：登录态浏览器上下文工厂。

auth 测试先跑会建立账号体系——后续 browser 测试需要登录态（登录门
overlay 挡交互）。工厂经 API 登录 admin（无则 setup 建）→ Set-Cookie
注入 playwright context（httponly 可设）。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

# playwright 包缺席 → 整目录按既有 importorskip 语义跳过；
# 包在席但 chromium 二进制未装（playwright install 缺步——CI test
# 矩阵 job 不装浏览器）→ collect_ignore 整目录，不假红。
try:
    from playwright.sync_api import sync_playwright as _spw
    with _spw() as _p:
        with _p.chromium.launch():
            pass
    _CHROMIUM_READY = True
except Exception:  # noqa: BLE001 — 任何启动失败都归为"环境不可用"
    _CHROMIUM_READY = False

if not _CHROMIUM_READY:
    collect_ignore = [p.name for p in
                      Path(__file__).parent.glob("test_*.py")]


@pytest.fixture()
def logged_context(server_url):
    """返回 async 工厂：() -> BrowserContext（带 admin 登录 cookie）。"""

    async def _make(browser):
        async with httpx.AsyncClient(base_url=server_url, timeout=10) as c:
            r = await c.post("/api/auth/login", json={
                "username": "admin", "password": "admin-pass-123"})
            if r.status_code == 401:
                # 首跑：账号体系尚未建立（auth 测试不在前）——setup 建
                r = await c.post("/api/auth/setup", json={
                    "username": "admin", "password": "admin-pass-123"})
                assert r.status_code == 200, r.text
                r = await c.post("/api/auth/login", json={
                    "username": "admin", "password": "admin-pass-123"})
            assert r.status_code == 200, r.text
            setc = r.headers.get("set-cookie", "")
        # "loadn_session=xxx; HttpOnly; Path=/; ..." → playwright cookie
        kv = setc.split(";")[0]
        name, _, val = kv.partition("=")
        ctx = await browser.new_context()
        await ctx.add_cookies([{
            "name": name, "value": val,
            "url": server_url,
        }])
        return ctx

    return _make
