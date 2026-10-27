"""自动续跑（rev18）：turn 完成后 todo 未完自动投「继续」接力，直到清单完成
/连续上限/用户手动接管。hahaness fake todos 旋钮=turn done 且 todos 全 pending
的完美道具（无限未完成——恰好逼出上限路径）。

对赌：
  ① PATCH auto_continue roundtrip（0↔1）
  ② 链式续跑：开开关 → 原始 turn done → 自动续跑 ×AUTO_CONT_MAX → 停
     （turns=1+MAX；续跑消息带【自动续跑 N/MAX】；kv 计数=MAX）
  ③ 关开关：todo 同样未完但不续（turns=1）
  ④ 手动消息清零计数：续 1 次后手动发消息（autocont 归零）
"""
from __future__ import annotations

import time

import pytest

from loadn_webui import db as db_mod
from loadn_webui.config import CONFIG


@pytest.fixture()
def hahaness_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.engines, "default", "hahaness")
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


def _wait_turns(sid: str, n: int, timeout_s: float = 30) -> list:
    """轮询 DB 直到该会话 turn 数 ≥n 且全部终态。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT id,status FROM turns WHERE session_id=? ORDER BY id",
                (sid,)).fetchall()
        if len(rows) >= n and all(r["status"] in ("done", "error", "stopped")
                                  for r in rows):
            return rows
        time.sleep(0.3)
    return rows


async def test_auto_continue_patch_roundtrip(client, hahaness_engine):
    r = await client.post("/api/sessions", json={"title": "ac 开关"})
    sid = r.json()["session"]["id"]
    r = await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})
    assert r.status_code == 200
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["auto_continue"] == 1
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 0})
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert not d["auto_continue"]


async def test_auto_continue_chain(client, ws_root, hahaness_engine, monkeypatch):
    """>>> 开关开：todos 恒 pending → 连续续跑到上限 2 次停（1+2 turns）。"""
    from loadn_webui import engine as engine_mod
    monkeypatch.setattr(engine_mod, "AUTO_CONT_MAX", 2)
    r = await client.post("/api/sessions", json={"title": "ac 链"})
    sid = r.json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})
    _fake_dir(ws_root, sid, todos="")

    await client.post(f"/api/sessions/{sid}/messages", json={"text": "列清单并执行"})
    rows = _wait_turns(sid, 3)
    assert len(rows) == 3, f"应 1 原始+2 续跑，实得 {len(rows)}"
    assert all(r["status"] == "done" for r in rows)
    # 上限后不再有第 4 个（再等 2s 确认静默）
    time.sleep(2)
    with db_mod.conn() as c:
        n4 = c.execute("SELECT count(*) FROM turns WHERE session_id=?",
                       (sid,)).fetchone()[0]
        cont = db_mod.kv_get(c, f"autocont:{sid}")
    assert n4 == 3, "达上限后必须停"
    assert cont == "2"
    # 续跑消息带标记（第 2 条用户消息=【自动续跑 1/2】）
    with db_mod.conn() as c:
        texts = [r["content"] for r in c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='user' "
            "ORDER BY id", (sid,)).fetchall()]
    assert any("【自动续跑 1/2】" in t for t in texts), texts
    assert any("【自动续跑 2/2】" in t for t in texts), texts


async def test_auto_continue_off_by_default(client, ws_root, hahaness_engine):
    """>>> 默认关：todo 同样未完，但只 1 个 turn，无续跑消息。"""
    r = await client.post("/api/sessions", json={"title": "ac 默认关"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, todos="")
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "列清单"})
    rows = _wait_turns(sid, 1)
    time.sleep(2)
    with db_mod.conn() as c:
        n = c.execute("SELECT count(*) FROM turns WHERE session_id=?",
                      (sid,)).fetchone()[0]
    assert len(rows) == 1 and n == 1, "未开开关绝不能自动续"


async def test_auto_continue_manual_reset(client, ws_root, hahaness_engine, monkeypatch):
    """>>> 手动消息清零计数：续 1 次后用户接管 → 计数重开（再次出现 1/2 标记）。"""
    from loadn_webui import engine as engine_mod
    monkeypatch.setattr(engine_mod, "AUTO_CONT_MAX", 2)
    r = await client.post("/api/sessions", json={"title": "ac 接管"})
    sid = r.json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})
    _fake_dir(ws_root, sid, todos="")

    await client.post(f"/api/sessions/{sid}/messages", json={"text": "开工"})
    rows = _wait_turns(sid, 2)                       # 原始+自动续 1（1/2 标记已落）
    assert len(rows) >= 2
    # 手动消息=接管：计数清零（post_message 同步 kv_del，返回即生效）
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "我来处理"})
    # 等链彻底静默：turns 数 4s 不变且全终态（上限 2 的链自然走完）
    t0 = time.monotonic()
    last_n = -1
    while time.monotonic() - t0 < 40:
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT id,status FROM turns WHERE session_id=? ORDER BY id",
                (sid,)).fetchall()
        n = len(rows)
        if n == last_n and all(r["status"] in ("done", "error", "stopped")
                               for r in rows):
            time.sleep(4)
            with db_mod.conn() as c:
                rows2 = c.execute(
                    "SELECT count(*) FROM turns WHERE session_id=?",
                    (sid,)).fetchone()[0]
            if rows2 == n:
                break
        last_n = n
        time.sleep(0.5)
    with db_mod.conn() as c:
        marks = [r["content"] for r in c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='user' "
            "ORDER BY id", (sid,)).fetchall()]
    n1 = sum(1 for m in marks if "【自动续跑 1/2】" in m)
    assert n1 >= 2, f"清零重计实证失败（1/2 标记应≥2 次）: {marks}"
    assert len(rows) >= 4, "接管后应有新链（手动 turn+续跑）"
