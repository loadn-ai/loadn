"""聊天框内核切换：PATCH engine 会话级覆盖 + 跨引擎 e2e（零 token）。

claude 引擎走 conftest 的假 CLI（WORKDADDY_FAKE_LOG 记 argv），hahaness 走
HAHANESS_PROVIDER=fake；两引擎各有独立会话 id 域，切换经 _align_engine
存档/取回——切回应自动续接旧引擎会话。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from loadn_webui import db as db_mod


@pytest.fixture()
def hahaness_env(tmp_path, monkeypatch):
    """hahaness 引擎可用环境（不动全局默认引擎——本文件测的是覆盖切换）。"""
    home = tmp_path / "hahaness_home"
    home.mkdir()
    monkeypatch.setenv("HAHANESS_HOME", str(home))
    monkeypatch.setenv("LOADN_HOME", str(home))
    monkeypatch.setenv("HAHANESS_PROVIDER", "fake")
    return home


def _fake_dir(ws_root, sid, **knobs):
    fake = ws_root / sid / ".fake"
    fake.mkdir(parents=True, exist_ok=True)
    for name, content in knobs.items():
        (fake / name).write_text(content if isinstance(content, str) else "")
    return fake


async def _wait_terminal(client, sid: str, tid: int, timeout_s: float = 30) -> dict:
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.2)
        resp = await client.get(f"/api/sessions/{sid}")
        t = next((x for x in resp.json()["turns"] if x["id"] == tid), None)
        if t and t["status"] in ("done", "error", "stopped", "interrupted"):
            return t
    raise TimeoutError("turn 未到终态")


# ---------------------------------------------------------------- PATCH 校验
async def test_patch_engine_set_validate_clear(client):
    r = await client.post("/api/sessions", json={"title": "引擎切换"})
    sid = r.json()["session"]["id"]

    r = await client.patch(f"/api/sessions/{sid}", json={"engine": "hahaness"})
    assert r.status_code == 200
    assert r.json()["session"]["engine_override"] == "hahaness"

    r = await client.patch(f"/api/sessions/{sid}", json={"engine": "no-such-engine"})
    assert r.status_code == 400
    assert "未知引擎" in r.json()["detail"]

    r = await client.patch(f"/api/sessions/{sid}", json={"engine": None})
    assert r.status_code == 200
    assert r.json()["session"]["engine_override"] is None


# ---------------------------------------------------------------- 跨引擎 e2e
async def test_switch_engine_mid_session(client, ws_root, hahaness_env, fake_calls):
    """claude 起步 → 切 hahaness（会话 id 迁移）→ 切回默认（claude 续接）。"""
    r = await client.post("/api/sessions", json={"title": "跨内核"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, reply="claude 首答")

    # 第一轮：默认引擎（claude 假 CLI）
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "问一"})
    tid1 = r.json()["turn"]["id"]
    t1 = await _wait_terminal(client, sid, tid1)
    assert t1["status"] == "done"
    claude_calls_after_1 = len(fake_calls())
    assert claude_calls_after_1 >= 1
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    assert sess["engine"] == "claude" and sess["claude_session_id"]

    # 切 hahaness：下一 turn 走 hahaness，claude 完全不动
    r = await client.patch(f"/api/sessions/{sid}", json={"engine": "hahaness"})
    assert r.status_code == 200
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "问二"})
    tid2 = r.json()["turn"]["id"]
    t2 = await _wait_terminal(client, sid, tid2)
    assert t2["status"] == "done"
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    assert sess["engine"] in ("hahaness", "loadn")
    hh_sid = sess["claude_session_id"]
    assert hh_sid and hh_sid != ""
    assert (hahaness_env / "sessions" / hh_sid / "transcript.jsonl").exists()
    assert len(fake_calls()) == claude_calls_after_1      # claude 一次都没多跑
    # 旧引擎 id 已存档（切回时可取回续接）
    archive = json.loads(sess["engine_session_ids"] or "{}")
    assert "claude" in archive

    # 切回默认：清除覆盖 → claude 恢复执行
    r = await client.patch(f"/api/sessions/{sid}", json={"engine": None})
    assert r.status_code == 200
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "问三"})
    tid3 = r.json()["turn"]["id"]
    t3 = await _wait_terminal(client, sid, tid3)
    assert t3["status"] == "done"
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    assert sess["engine"] == "claude"
    assert sess["claude_session_id"] == archive["claude"]  # 取回存档续接
    assert len(fake_calls()) > claude_calls_after_1
