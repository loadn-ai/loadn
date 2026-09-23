"""凭证巡检（P2-3）+ 台账分裂检查（P2-1）。"""
import json

import pytest

from loadn_webui import verify as verify_mod
from loadn_webui.artifacts import ledger_split_check


@pytest.fixture()
async def reg_session(client, ws_root):
    r = await client.post("/api/sessions", json={"title": "凭证巡检"})
    sid = r.json()["session"]["id"]
    entries = [
        {"id": 1, "platform": "google", "title": "GA 基础", "cert_id": "G-123",
         "verify_url": "https://credly.com/badges/1", "holder": "Woldy",
         "issued": "2026-09-13", "expires": "2027-09-13", "account": "google"},
        {"id": 2, "platform": "saylor", "title": "CS101", "cert_id": "S-9",
         "verify_url": "https://saylor.org/verify/9", "holder": "Woldy"},
        {"id": 3, "platform": "broken", "title": "无证书号", "verify_url": ""},
    ]
    d = ws_root / sid / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "credentials.json").write_text(json.dumps(entries, ensure_ascii=False))
    return sid


async def test_offline_checks(reg_session, tmp_path, monkeypatch):
    from datetime import date, timedelta
    sid = reg_session
    # 无 expires = 永久；把 #1 改成明天到期 → 预警命中
    d = tmp_path / "r1.md"
    res = await verify_mod.run(sid, online=False, out_path=str(d))
    assert res["entries"] == 3
    assert any("缺字段" in p for p in res["schema_problems"])       # #3 缺 cert_id 不算，缺 verify_url 是
    assert "credentials.json" in res["report"] or "凭证巡检" in res["report"]
    assert d.exists()
    # 到期窗口逻辑（纯函数直测）
    today = date(2026, 9, 15)
    assert "已过期" in verify_mod.check_expiry({"expires": "2026-09-01"}, today, 30)
    assert "天后到期" in verify_mod.check_expiry(
        {"expires": str(today + timedelta(days=10))}, today, 30)
    assert "无法解析" in verify_mod.check_expiry({"expires": "soon"}, today, 30)


async def test_online_verify(reg_session, monkeypatch):
    sid = reg_session

    async def fake_fetch(url, **kw):
        assert url.startswith("https://")
        text = "Badge awarded to Woldy G-123" if "1" in url else "Page Not Found"
        return {"text": text, "title": "x", "chars": len(text)}
    from loadn_webui import resources
    monkeypatch.setattr(resources, "fetch_page", fake_fetch)
    res = await verify_mod.run(sid, online=True)
    rows = {r["id"]: r for r in res["rows"]}
    assert rows[1]["online"]["ok"] is True               # 命中 holder+cert_id
    assert rows[2]["online"]["ok"] is False              # 页面无持有者信息


async def test_ledger_split(client, ws_root):
    sid = (await client.post("/api/sessions", json={"title": "台账"})).json()["session"]["id"]
    ws = ws_root / sid
    (ws / "artifacts").mkdir(parents=True, exist_ok=True)
    assert ledger_split_check(sid) == []
    (ws / "PROGRESS.md").write_text("# 根台账\n")
    (ws / "artifacts" / "PROGRESS.md").write_text("# 副本台账\n" * 5)
    (ws / "credentials.md").write_text("a")
    (ws / "artifacts" / "certificates").mkdir(parents=True, exist_ok=True)
    (ws / "artifacts" / "certificates" / "credentials.md").write_text("b")
    problems = ledger_split_check(sid)
    assert any("PROGRESS.md 双轨" in p for p in problems)
    assert any("credentials 多副本" in p for p in problems)
