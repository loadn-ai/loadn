"""规划模式（rev22，Claude Code plan mode 的平台化）：
  PLAN-1 触发：sessions.plan_mode 开关（默认关）+ 新任务判定（todos 在场
      /30 分钟内有活动 → 续聊不触发；未裁决计划在场 → 修订再进 plan 轮）
  PLAN-2 计划卡：plan 轮 done 收割 ```plan JSON 块 → kv plan:{sid} + SSE；
      GET /plan 读卡；POST /plan/decide approve → 步骤转 todos+投执行 turn，
      abort → 清账；修订=直接发新消息（不走 decide）
  防环：approve 后 todos 在场 → 执行轮不会被再拦进 plan 阶段
业界反面教训（人类沦为批准机器）：默认关，用户显式开启。
"""
import asyncio
import json

import pytest_asyncio

from loadn_webui import db as db_mod
from loadn_webui.engine import (
    Engine,
    _parse_plan_block,
    _plan_should_trigger,
)


@pytest_asyncio.fixture()
async def eng():
    e = Engine()
    yield e
    for t in list(e._workers.values()):
        t.cancel()
    for at in list(e.active.values()):
        at.stop.stop()


async def _wait_turn_done(sid: str, min_turns: int = 1, timeout_s: float = 25):
    t0 = asyncio.get_running_loop().time()
    rows = []
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.15)
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY id",
                (sid,)).fetchall()
        if (len(rows) >= min_turns
                and not any(r["status"] in ("queued", "running") for r in rows)):
            return rows
    raise TimeoutError(f"turn 未终态（{len(rows)}）")


async def _mk_session(client, title="规划"):
    r = await client.post("/api/sessions", json={"title": title})
    sid = r.json()["session"]["id"]
    return sid


# ---------------------------------------------------------------- 纯函数层
def test_parse_plan_block_ok():
    c = '前置说明文字\n```plan\n{"goal": "查A并写报告", "steps": ["搜资料", "写报告"],\n "files": ["artifacts/r.md"], "risks": ["来源不可靠→交叉验证"], "questions": ["报告语言？"]}\n```'
    p = _parse_plan_block(c)
    assert p["goal"] == "查A并写报告"
    assert p["steps"] == ["搜资料", "写报告"]
    assert p["questions"] == ["报告语言？"]


def test_parse_plan_block_tolerant():
    """无块/坏 JSON/非 dict → None；字段缺省补空。"""
    assert _parse_plan_block("没有计划块") is None
    assert _parse_plan_block("```plan\n{bad json}\n```") is None
    p = _parse_plan_block('```plan\n{"goal": "g"}\n```')
    assert p == {"goal": "g", "steps": [], "files": [], "risks": [], "questions": []}


def test_should_trigger_matrix(client):
    """触发判定矩阵：计划在场=修订触发；todos 在场=不触发；近期活动=不触发。"""
    import asyncio as aio
    async def run():
        r = aio.get_event_loop()
    with db_mod.conn() as c:
        sid = "plan-trig-test"
        c.execute("INSERT OR IGNORE INTO sessions(id, title) VALUES(?, 't')", (sid,))
        db_mod.kv_del(c, f"plan:{sid}")
        db_mod.kv_del(c, f"todos:{sid}")
        c.execute("DELETE FROM turns WHERE session_id=?", (sid,))
        # 全空 + 无历史 turn → 新任务触发
        assert _plan_should_trigger(c, sid) is True
        # todos 在场（任务进行中）→ 不触发
        db_mod.kv_set(c, f"todos:{sid}", json.dumps(
            [{"subject": "s", "status": "pending"}]))
        assert _plan_should_trigger(c, sid) is False
        db_mod.kv_del(c, f"todos:{sid}")
        # 30 分钟内有终态 turn → 不触发（续聊）
        from loadn_webui.util import iso
        tid = db_mod.create_turn(c, session_id=sid, status="done",
                                 finished_at=iso())
        assert _plan_should_trigger(c, sid) is False
        # 未裁决计划在场 → 触发（新消息=修订）
        db_mod.kv_set(c, f"plan:{sid}", json.dumps({"goal": "g"}))
        assert _plan_should_trigger(c, sid) is True
        db_mod.kv_del(c, f"plan:{sid}")
        c.execute("DELETE FROM turns WHERE session_id=?", (sid,))
    # 占位：测试树 sync 路由下 client 不进入 event loop 也能跑纯函数面
    del run


# ---------------------------------------------------------------- API 面
async def test_plan_mode_switch_roundtrip(client):
    r = await client.patch("/api/sessions/nonexistent-session-x", json={})
    assert r.status_code == 404
    sid = await _mk_session(client)
    r = await client.patch(f"/api/sessions/{sid}", json={"plan_mode": True})
    assert r.status_code == 200
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["plan_mode"] == 1
    await client.patch(f"/api/sessions/{sid}", json={"plan_mode": False})
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["plan_mode"] == 0


async def test_decide_approve_injects_todos_and_exec(client, eng):
    """approve：计划步骤转 todos（TodoWrite 同构）+ 执行 turn 投递（消息带
    批准头与步骤）；abort 清账；无计划 404；action 校验。"""
    sid = await _mk_session(client)
    plan = {"goal": "查A", "steps": ["第一步", "第二步"], "files": ["a.md"],
            "risks": [], "questions": [], "task": "帮我查A", "turn_id": 1,
            "revision": 1}
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"plan:{sid}", json.dumps(plan, ensure_ascii=False))
    r = await client.get(f"/api/sessions/{sid}/plan")
    assert r.json()["plan"]["goal"] == "查A"
    # action 校验
    r = await client.post(f"/api/sessions/{sid}/plan/decide",
                          json={"action": "revise"})
    assert r.status_code == 400
    # approve → todos + 执行 turn
    r = await client.post(f"/api/sessions/{sid}/plan/decide",
                          json={"action": "approve"})
    assert r.status_code == 200 and r.json()["exec_turn"]
    with db_mod.conn() as c:
        todos = json.loads(db_mod.kv_get(c, f"todos:{sid}"))
        assert [t["subject"] for t in todos] == ["第一步", "第二步"]
        assert all(t["status"] == "pending" for t in todos)
        assert db_mod.kv_get(c, f"plan:{sid}") is None      # 账清
        msg = c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='user'"
            " ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        assert "已获用户批准" in msg["content"] and "第二步" in msg["content"]
        # 防环：todos 在场 → 执行轮不会进 plan 阶段
        assert _plan_should_trigger(c, sid) is False
    await _wait_turn_done(sid)


async def test_decide_abort_and_404(client, eng):
    sid = await _mk_session(client)
    r = await client.post(f"/api/sessions/{sid}/plan/decide",
                          json={"action": "approve"})
    assert r.status_code == 404                                   # 无计划
    plan = {"goal": "g", "steps": [], "task": "t", "turn_id": 1, "revision": 1}
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"plan:{sid}", json.dumps(plan, ensure_ascii=False))
    r = await client.post(f"/api/sessions/{sid}/plan/decide",
                          json={"action": "abort"})
    assert r.status_code == 200
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, f"plan:{sid}") is None
        assert db_mod.kv_get(c, f"todos:{sid}") is None           # 不投执行


async def test_harvest_plan_block_and_fallback(client, eng):
    """收割：plan 块解析落 kv+SSE；无块 fallback 卡（raw 摘要可见不静默丢）。"""
    sid = await _mk_session(client)
    e = eng
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"planq:{sid}", json.dumps({"tid": 99, "task": "查B"}))
    published = []
    e.publish = lambda *a, **k: published.append(a)               # 打桩 SSE
    e._maybe_harvest_plan(sid, 99,
                          '说明\n```plan\n{"goal": "查B", "steps": ["s1"]}\n```')
    with db_mod.conn() as c:
        plan = json.loads(db_mod.kv_get(c, f"plan:{sid}"))
        assert plan["goal"] == "查B" and plan["steps"] == ["s1"]
        assert plan["task"] == "查B" and plan["turn_id"] == 99
        assert plan["revision"] == 1
        assert db_mod.kv_get(c, f"planq:{sid}") is None           # 标记清
    assert published and published[0][1] == "plan"
    # 无块 → fallback（须重置 planq 标记——收割即清是设计语义）
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"planq:{sid}", json.dumps({"tid": 100, "task": "查B"}))
    e._maybe_harvest_plan(sid, 100, "模型没守约只有散文")
    with db_mod.conn() as c:
        plan2 = json.loads(db_mod.kv_get(c, f"plan:{sid}"))
        assert "未产出结构化计划" in plan2["goal"] and plan2["raw"]
        assert plan2["revision"] == 2                             # 轮次递增
    # 非 plan 轮（无标记）→ 零动作
    e._maybe_harvest_plan(sid, 999, "```plan\n{}\n```")
    with db_mod.conn() as c:
        assert json.loads(db_mod.kv_get(c, f"plan:{sid}"))["turn_id"] == 100
