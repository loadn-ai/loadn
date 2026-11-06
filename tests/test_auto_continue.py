"""自动续跑（rev18）：turn 完成后 todo 未完自动投「继续」接力，直到清单完成
/连续上限/用户手动接管。loadn fake todos 旋钮=turn done 且 todos 全 pending
的完美道具（无限未完成——恰好逼出上限路径）。

对赌：
  ① PATCH auto_continue roundtrip（0↔1）
  ② 链式续跑：开开关 → 原始 turn done → 自动续跑 ×AUTO_CONT_MAX → 停
     （turns=1+MAX；续跑消息带【自动续跑 N/MAX】；kv 计数=MAX）
  ③ 关开关：todo 同样未完但不续（turns=1）
  ④ 手动消息清零计数：续 1 次后手动发消息（autocont 归零）
"""
from __future__ import annotations

import json as _json
import time

import pytest

from loadn_webui import db as db_mod
from loadn_webui.config import CONFIG


@pytest.fixture()
def loadn_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.engines, "default", "loadn")
    home = tmp_path / "loadn_home"
    home.mkdir()
    monkeypatch.setenv("LOADN_HOME", str(home))
    monkeypatch.setenv("LOADN_PROVIDER", "fake")
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


async def test_auto_continue_patch_roundtrip(client, loadn_engine):
    r = await client.post("/api/sessions", json={"title": "ac 开关"})
    sid = r.json()["session"]["id"]
    r = await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})
    assert r.status_code == 200
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["auto_continue"] == 1
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 0})
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert not d["auto_continue"]


async def test_auto_continue_chain(client, ws_root, loadn_engine, monkeypatch):
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


async def test_auto_continue_off_by_default(client, ws_root, loadn_engine):
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


async def test_auto_continue_manual_reset(client, ws_root, loadn_engine, monkeypatch):
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


async def test_auto_continue_cross_turn_kv_fallback(client, loadn_engine, monkeypatch):
    """>>> 跨 turn 回落（v0.7.56）：清单是早前 turn 建的（kv todos:{sid}
    持久快照在场），本 turn 模型不重发 TodoWrite（at.todos 空）——仍应续。
    生产实证根因：20261026_0253 会话首续即断（清单昨日建，今日 turn
    blocks 无 todos 块 → at.todos 空 → 误判无未完成不投递）。
    """
    import json as _json
    from types import SimpleNamespace

    from loadn_webui import engine as engine_mod

    r = await client.post("/api/sessions", json={"title": "ac 跨turn"})
    sid = r.json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})
    # 早前 turn 的持久快照：2 项未完成（_todos_persist 写入形态）
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"todos:{sid}", _json.dumps(
            [{"subject": "回测实验 A", "status": "in_progress"},
             {"subject": "论文写作", "status": "pending"}],
            ensure_ascii=False))

    submitted: list[str] = []

    async def fake_submit(s, text):
        submitted.append(text)
        return None

    monkeypatch.setattr(engine_mod.ENGINE, "submit", fake_submit)
    at = SimpleNamespace(todos=[])          # 本 turn 未动清单
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    assert len(submitted) == 1, "kv 快照有未完成项，at.todos 空也必须续"
    assert "回测实验 A" in submitted[0] and "【自动续跑 1/25】" in submitted[0]

    # 快照全完成 → 不续（清单完成即清 kv 的对赌在 _todos_persist 侧）
    with db_mod.conn() as c:
        db_mod.kv_del(c, f"todos:{sid}")
    submitted.clear()
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    assert not submitted, "无快照且 at.todos 空=无未完成证据，绝不续"


async def test_todos_persist_kv_lifecycle(client, ws_root, loadn_engine):
    """>>> _todos_persist 持久层生命周期：未完成写 kv / 全完成清 kv。"""
    import json as _json
    from types import SimpleNamespace

    from loadn_webui import engine as engine_mod

    r = await client.post("/api/sessions", json={"title": "kv 生命周期"})
    sid = r.json()["session"]["id"]
    at = SimpleNamespace(
        todos=[{"subject": "任务一", "status": "in_progress"}],
        blocks=[], quiet=True)
    engine_mod.ENGINE._todos_persist(at, sid, 1)
    with db_mod.conn() as c:
        raw = db_mod.kv_get(c, f"todos:{sid}")
    assert raw and "任务一" in _json.loads(raw)[0]["subject"]

    at.todos = [{"subject": "任务一", "status": "completed"}]
    engine_mod.ENGINE._todos_persist(at, sid, 1)
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, f"todos:{sid}") is None, "全完成必须清 kv"


async def test_contlike_takeover(client, loadn_engine, monkeypatch):
    """>>> v0.7.59 无清单证据的接管（AC-2 放宽口径）：继续类短催促连发 ≥2 次
    → 下轮 done 自动续；长复杂指令/普通消息不算。生产实证 0253：清单被
    全标 completed 清掉后十几轮零 todo 汇报，用户被迫人工续跑。"""
    from types import SimpleNamespace

    from loadn_webui import engine as engine_mod
    from loadn_webui.api.routes import turns as turns_mod

    r = await client.post("/api/sessions", json={"title": "contlike"})
    sid = r.json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})
    # 场景预设：清单建立过且已全部完成（0253 形态）——todos 快照已被
    # _todos_persist 清掉，todos_ever 永久标记在场（AC-3 维持 ≥2 阈值的对象）
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"todos_ever:{sid}", "1")

    # 判定边界（_is_contlike：开头匹配/含「续跑」+≤24 字）：裸催促与带语气
    # 催促命中（AC-2 生产实证：6bfa 用户「继续啊，自动续跑」v0.7.59 不计
    # 入、contlike 恒 0、兜底够不着）；长复杂指令/普通消息不命中
    assert turns_mod._is_contlike("继续")
    assert turns_mod._is_contlike("继续！")
    assert turns_mod._is_contlike("Continue")
    assert turns_mod._is_contlike("继续啊，自动续跑")  # 6bfa 实证形态
    assert turns_mod._is_contlike("自动续跑呢？")
    assert not turns_mod._is_contlike(
        "继续优化 A 然后写报告，重点是图表和结论部分都要再打磨")  # >24 字
    assert not turns_mod._is_contlike("帮我看下这个")

    submitted: list[str] = []

    async def fake_submit(s, text, *a, **kw):
        submitted.append(text)
        return 1

    monkeypatch.setattr(engine_mod.ENGINE, "submit", fake_submit)
    at = SimpleNamespace(todos=[])

    async def _msg(text):
        await client.post(f"/api/sessions/{sid}/messages", json={"text": text})

    # 单次「继续」：不够——不续（submitted 里用户消息本身在，只查续跑标记）
    await _msg("继续")
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    assert not [t for t in submitted if "【自动续跑" in t], "单次继续不构成接管"

    # 连续第二次「继续」：接管成立——续（无清单分支文案）
    await _msg("继续")
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    cont = [t for t in submitted if "【自动续跑" in t]
    assert len(cont) == 1 and "TodoWrite" in cont[0]
    assert "【自动续跑 1/25】" in cont[0]

    # 长复杂指令（任务内容非催促，>24 字）：意图断开——清零后不续
    submitted.clear()
    await _msg("继续优化回测脚本，重点看滑点模型、手续费假设和成交率三处")
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, f"contlike:{sid}") is None
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    assert not [t for t in submitted if "【自动续跑" in t]


async def test_contlike_first_urge_never_listed(client, loadn_engine, monkeypatch):
    """>>> AC-3 降阈：清单信号从未建立（todos:{sid} 快照不存在——生产实证
    6bfa：agent 三 turn 零 TodoWrite，auto_continue=1 形同虚设）时，用户
    催 1 次即投「建清单引导」续跑轮，不必等第二次；清单建立过（快照在）
    的会话维持 ≥2 阈值。"""
    from types import SimpleNamespace

    from loadn_webui import engine as engine_mod

    r = await client.post("/api/sessions", json={"title": "ac3 降阈"})
    sid = r.json()["session"]["id"]
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": 1})

    submitted: list[str] = []

    async def fake_submit(s, text, *a, **kw):
        submitted.append(text)
        return 1

    monkeypatch.setattr(engine_mod.ENGINE, "submit", fake_submit)
    at = SimpleNamespace(todos=[])  # 本 turn 无清单；kv 也从未写过 → never_listed

    # 用户催 1 次（6bfa 实证形态）：立即接管投引导轮
    await client.post(f"/api/sessions/{sid}/messages",
                      json={"text": "继续啊，自动续跑"})
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    cont = [t for t in submitted if "【自动续跑" in t]
    assert len(cont) == 1 and "TodoWrite" in cont[0], "从未建清单+催 1 次即接管"

    # 对照：清单信号建立过的会话（todos_ever 标记在场，快照已全完成清
    # 掉）——单次催促不够，维持 v0.7.59 的 ≥2 阈值；连催第二次接管
    with db_mod.conn() as c:
        db_mod.kv_del(c, f"todos:{sid}")
        db_mod.kv_set(c, f"todos_ever:{sid}", "1")
        db_mod.kv_del(c, f"contlike:{sid}")
        db_mod.kv_del(c, f"autocont:{sid}")
    submitted.clear()
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "继续"})
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    assert not [t for t in submitted if "【自动续跑" in t], "建过清单：单次不接管"
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "接着干"})
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    cont = [t for t in submitted if "【自动续跑" in t]
    assert len(cont) == 1 and "TodoWrite" in cont[0], "建过清单：连催第二次接管"

    # 主轴回归：todos 快照在场且有未完成项 → 单次催促即走清单续跑文案
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"todos:{sid}",
                      _json.dumps([{"subject": "任务一", "status": "in_progress"}],
                                  ensure_ascii=False))
        db_mod.kv_del(c, f"contlike:{sid}")
        db_mod.kv_del(c, f"autocont:{sid}")
    submitted.clear()
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "继续"})
    await engine_mod.ENGINE._maybe_auto_continue(sid, at)
    cont = [t for t in submitted if "【自动续跑" in t]
    assert len(cont) == 1 and "继续执行任务清单" in cont[0]
