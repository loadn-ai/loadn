"""产物分享：token 铸造 + 主服务上的公开只读路由 /share/<token>。"""
from __future__ import annotations

import pytest_asyncio


@pytest_asyncio.fixture()
async def share_env(client):
    """一个带产物文件的会话 + 临时启用 share 配置（路由挂在主服务测试进程里）。"""
    from loadn_webui.config import CONFIG, PATHS

    resp = await client.post("/api/sessions", json={"title": "分享测试"})
    sid = resp.json()["session"]["id"]
    arts = PATHS["workspace"] / sid / "artifacts"
    arts.mkdir(parents=True, exist_ok=True)
    (arts / "report.md").write_text("# 报告\n\n分享链路测试正文。")
    (arts / "page.html").write_text("<html><body><script>alert(1)</script><p>hi</p></body></html>")
    (PATHS["workspace"] / sid / "notes").mkdir(parents=True, exist_ok=True)
    (PATHS["workspace"] / sid / "notes" / "secret.md").write_text("内部笔记")

    old_base = CONFIG.share.base_url
    CONFIG.share.base_url = "https://example.com/share"
    try:
        yield {"client": client, "sid": sid}
    finally:
        CONFIG.share.base_url = old_base


async def _mint(client, sid, path):
    r = await client.post(f"/api/sessions/{sid}/share", json={"path": path})
    assert r.status_code == 200, r.text
    return r.json()


async def test_mint_and_serve_md(share_env):
    client, sid = share_env["client"], share_env["sid"]
    data = await _mint(client, sid, "artifacts/report.md")
    assert data["url"].startswith("https://example.com/share/")
    token = data["token"]
    # 幂等：同 path 复用同 token
    assert (await _mint(client, sid, "artifacts/report.md"))["token"] == token
    # md 渲染成 html（与站内预览同构）；HEAD 也要通（链接预览器）
    resp = await client.get(f"/share/{token}/report.md")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "分享链路测试正文" in resp.text
    assert resp.headers.get("content-security-policy") == "sandbox"
    head = await client.head(f"/share/{token}")
    assert head.status_code == 200


async def test_mint_restrictions(share_env):
    client, sid = share_env["client"], share_env["sid"]
    # 非 artifacts/ 下拒绝
    r = await client.post(f"/api/sessions/{sid}/share", json={"path": "notes/secret.md"})
    assert r.status_code == 400
    # 不存在的文件拒绝
    r = await client.post(f"/api/sessions/{sid}/share", json={"path": "artifacts/nope.md"})
    assert r.status_code == 400


async def test_serve_unknown_token_404(share_env):
    resp = await share_env["client"].get("/share/deadbeef" + "0" * 13)
    assert resp.status_code == 404


async def test_serve_html_sandbox_and_inline(share_env):
    client, sid = share_env["client"], share_env["sid"]
    token = (await _mint(client, sid, "artifacts/page.html"))["token"]
    resp = await client.get(f"/share/{token}")
    assert resp.status_code == 200
    assert "sandbox" in resp.headers.get("content-security-policy", "")
    assert "inline" in resp.headers.get("content-disposition", "")
    assert resp.headers.get("x-content-type-options") == "nosniff"


async def test_serve_gone_after_delete(share_env):
    client, sid = share_env["client"], share_env["sid"]
    from loadn_webui.config import PATHS
    token = (await _mint(client, sid, "artifacts/report.md"))["token"]
    (PATHS["workspace"] / sid / "artifacts" / "report.md").unlink()
    resp = await client.get(f"/share/{token}")
    assert resp.status_code == 404


async def test_mint_disabled_without_base_url(share_env):
    from loadn_webui.config import CONFIG
    client, sid = share_env["client"], share_env["sid"]
    old = CONFIG.share.base_url
    CONFIG.share.base_url = ""
    try:
        r = await client.post(f"/api/sessions/{sid}/share",
                              json={"path": "artifacts/page.html"})
        assert r.status_code == 400
    finally:
        CONFIG.share.base_url = old
