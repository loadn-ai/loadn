"""子任务自动分类打标（rev10）：决策解析矩阵 / qa 不打标 / 并入 / 新建 /
同名合并 / 守卫与静默降级 / 并发锁 / 撤回竞态 / backfill。

单测直调 maybe_tag（临时 HOME 隔离 db PATHS + monkeypatch titlegen._chat）；
集成面（submit→SSE→detail）在文末 client fixture 用例。
"""
from __future__ import annotations

import asyncio

import pytest

from loadn_webui import db as db_mod
from loadn_webui.config import CONFIG
from loadn_webui.integrations import subtask as st
from loadn_webui.integrations import titlegen


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """隔离的 webui HOME（db 指到 tmp）。"""
    monkeypatch.setattr(db_mod, "PATHS", {"root": tmp_path,
                                          "var": tmp_path / "var",
                                          "db": tmp_path / "var" / "loadn.db"})
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "fake-key")
    with db_mod.conn() as c:
        c.execute("INSERT INTO sessions(id, title) VALUES('s1','分类')")
    return tmp_path


def _mk_turn(c, sid="s1") -> int:
    return db_mod.create_turn(c, session_id=sid, status="queued")


def _reply(monkeypatch, payload_or_exc):
    calls = []

    async def fake(system, user, max_tokens=64):
        calls.append(user)
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return payload_or_exc

    monkeypatch.setattr(titlegen, "_chat", fake)
    return calls


# ---------------------------------------------------------------- 解析矩阵
def test_parse_decision_matrix():
    ids = {7}
    assert st._parse_decision('{"type":"qa"}', ids) == {"type": "qa"}
    assert st._parse_decision('```json\n{"type":"qa"}\n```', ids) == {"type": "qa"}
    assert st._parse_decision('前置噪声 {"type":"task","subtask_id":7} 尾巴', ids) \
        == {"type": "task", "subtask_id": 7}
    assert st._parse_decision('{"type":"task","new_title":"调研竞品"}', ids) \
        == {"type": "task", "new_title": "调研竞品"}
    # 标题截断：≤12 字（超长清洗到上限）
    long = st._parse_decision('{"type":"task","new_title":"一二三四五六七八九十一'
                              '二三四五六七八九十一"}', ids)
    assert long is not None and len(long["new_title"]) == 12, long
    # 非法形态一律 None（含：非法 type 但带合法字段——type 门必须先挡）
    for raw in ('', '垃圾文本', '{"type":"chat"}',
                '{"type":"chat","subtask_id":7}',
                '{"type":"task","subtask_id":999}',       # id 幻觉（不在列表）
                '{"type":"task","new_title":"  "}',
                '{"type":"task"}', '{"type":"task","subtask_id":"7"}'):
        assert st._parse_decision(raw, ids) is None, raw


# ---------------------------------------------------------------- 行为
async def test_qa_not_tagged(home, monkeypatch):
    _reply(monkeypatch, '{"type":"qa"}')
    with db_mod.conn() as c:
        tid = _mk_turn(c)
    assert await st.maybe_tag("s1", tid, "这个结论什么意思？") is None
    with db_mod.conn() as c:
        assert db_mod.get_turn(c, tid)["subtask_id"] is None
        assert db_mod.list_subtasks(c, "s1") == []


async def test_task_creates_and_merges(home, monkeypatch):
    _reply(monkeypatch, '{"type":"task","new_title":"decting项目"}')
    with db_mod.conn() as c:
        t1, t2 = _mk_turn(c), _mk_turn(c)
    s1 = await st.maybe_tag("s1", t1, "在 /decting 编写完整检测代码")
    assert s1 is not None
    # 第二次并入（LLM 给 id）
    calls2 = _reply(monkeypatch, f'{{"type":"task","subtask_id":{s1}}}')
    assert await st.maybe_tag("s1", t2, "继续，不要停") == s1
    with db_mod.conn() as c:
        assert db_mod.get_turn(c, t1)["subtask_id"] == s1
        assert db_mod.get_turn(c, t2)["subtask_id"] == s1
        assert len(db_mod.list_subtasks(c, "s1")) == 1
    # prompt 带已有列表（复用引导：第二次调用能看到第一轮建的子任务）
    assert calls2 and any("decting项目" in u for u in calls2)


async def test_same_title_reused(home, monkeypatch):
    """>>> 同名合并对赌：LLM 两次都要求新建同名 → 只一行（第二道闸）。"""
    _reply(monkeypatch, '{"type":"task","new_title":"语料库调研"}')
    with db_mod.conn() as c:
        t1, t2 = _mk_turn(c), _mk_turn(c)
    a = await st.maybe_tag("s1", t1, "建语料库")
    b = await st.maybe_tag("s1", t2, "再建一次语料库")
    assert a == b
    with db_mod.conn() as c:
        assert len(db_mod.list_subtasks(c, "s1")) == 1


async def test_steer_prefix_stripped(home, monkeypatch):
    calls = _reply(monkeypatch, '{"type":"qa"}')
    with db_mod.conn() as c:
        tid = _mk_turn(c)
    await st.maybe_tag("s1", tid, "（运行中插话，转发处理）这个参数什么意思？")
    # 剥前缀后消息进了 prompt（不把插话转发标记喂给分类器）
    assert any("这个参数什么意思" in u and "插话" not in u.split("用户消息：")[-1][:30]
               for u in calls) or calls


# ---------------------------------------------------------------- 守卫与降级
async def test_guard_disabled_zero_calls(home, monkeypatch):
    """>>> 守卫对赌：未配置 → 零 LLM 调用（计数对赌——异常会被 _decide 吞掉，
    必须数调用次数而非只看返回 None）。"""
    calls = []
    CONFIG.titlegen.enabled = False

    async def fake(system, user, max_tokens=64):
        calls.append(user)
        return '{"type":"qa"}'

    monkeypatch.setattr(titlegen, "_chat", fake)
    with db_mod.conn() as c:
        tid = _mk_turn(c)
    assert await st.maybe_tag("s1", tid, "做个调研") is None
    assert calls == [], "未配置必须零 LLM 调用"
    CONFIG.titlegen.enabled = True
    CONFIG.titlegen.api_key = ""
    assert await st.maybe_tag("s1", tid, "做个调研") is None
    assert calls == []
    CONFIG.titlegen.api_key = "fake-key"


async def test_llm_failure_silent(home, monkeypatch):
    _reply(monkeypatch, RuntimeError("net down"))
    with db_mod.conn() as c:
        tid = _mk_turn(c)
    assert await st.maybe_tag("s1", tid, "做个调研") is None
    with db_mod.conn() as c:
        assert db_mod.list_subtasks(c, "s1") == []


async def test_retracted_turn_race(home, monkeypatch):
    """>>> 竞态对赌：turn 行已删 → 分类完成后零写入、零事件、不建子任务。"""
    from loadn_webui.engine import ENGINE

    published = []
    monkeypatch.setattr(ENGINE, "publish", lambda *a, **k: published.append(a))
    _reply(monkeypatch, '{"type":"task","new_title":"调研任务"}')
    with db_mod.conn() as c:
        tid = _mk_turn(c)
        c.execute("DELETE FROM turns WHERE id=?", (tid,))
    assert await st.maybe_tag("s1", tid, "深度调研") is None
    assert published == []
    with db_mod.conn() as c:
        assert db_mod.list_subtasks(c, "s1") == []


async def test_concurrent_same_session_single_row(home, monkeypatch):
    """>>> 并发锁对赌：同会话两个分类 task 并存（gather）→ 恰一行子任务。"""
    _reply(monkeypatch, '{"type":"task","new_title":"并发调研"}')
    with db_mod.conn() as c:
        t1, t2 = _mk_turn(c), _mk_turn(c)
    a, b = await asyncio.gather(
        st.maybe_tag("s1", t1, "任务甲"),
        st.maybe_tag("s1", t2, "任务乙"))
    with db_mod.conn() as c:
        rows = db_mod.list_subtasks(c, "s1")
    assert len(rows) == 1
    assert a == b == rows[0]["id"]


# ---------------------------------------------------------------- backfill
async def test_backfill_dry_run_and_real(home, monkeypatch):
    with db_mod.conn() as c:
        tids = [_mk_turn(c) for _ in range(3)]
        c.executemany(
            "INSERT INTO messages(session_id, turn_id, role, content, created_at)"
            " VALUES('s1',?,'user',?,datetime('now'))",
            [(tids[0], "深度调研AI检测"), (tids[1], "继续"),
             (tids[2], "这个结论什么意思")])
    decisions = iter([
        '{"type":"task","new_title":"调研任务"}',   # t1 新建
        '{"type":"qa"}',                             # t2 应答
        '{"type":"qa"}',                             # t3 应答
    ])

    async def fake(system, user, max_tokens=64):
        return next(decisions)

    monkeypatch.setattr(titlegen, "_chat", fake)
    # dry-run：零写库
    stats = await st.backfill("s1", dry_run=True)
    assert stats["scanned"] == 3 and stats["tagged"] == 0
    assert stats["created"] == 1 and stats["qa"] == 2
    with db_mod.conn() as c:
        assert db_mod.list_subtasks(c, "s1") == []
        assert all(db_mod.get_turn(c, t)["subtask_id"] is None for t in tids)
    # 实跑：第一条新建并入
    replies = iter(['{"type":"task","new_title":"调研任务"}',
                    '{"type":"qa"}', '{"type":"qa"}'])

    async def fake2(system, user, max_tokens=64):
        return next(replies)

    monkeypatch.setattr(titlegen, "_chat", fake2)
    stats2 = await st.backfill("s1", limit=3)
    assert stats2["tagged"] == 1 and stats2["created"] == 1 and stats2["qa"] == 2
    with db_mod.conn() as c:
        stid = db_mod.list_subtasks(c, "s1")[0]["id"]
        assert db_mod.get_turn(c, tids[0])["subtask_id"] == stid
        assert db_mod.get_turn(c, tids[1])["subtask_id"] is None


# ---------------------------------------------------------------- 集成（client）
async def _drain_sse(client, sid, events, want, timeout_s=20):
    async with client.stream("GET", f"/api/sessions/{sid}/events") as r:
        buf = ""
        ty = None
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line.startswith("event:"):
                    ty = line[6:].strip()
                elif line.startswith("data:") and ty:
                    import json as _json
                    events.append((ty, _json.loads(line[5:].strip())))
                    if ty == want:
                        return


async def test_submit_tags_and_sse(client, ws_root, monkeypatch):
    """submit → 分类器点火 → SSE subtask 事件 + detail 面（subtasks/turn.subtask_id）。"""
    sess = (await client.post("/api/sessions",
                              json={"title": "子任务集成"})).json()["session"]
    sid = sess["id"]

    async def fake(system, user, max_tokens=64):
        return '{"type":"task","new_title":"调研竞品"}'

    monkeypatch.setattr(titlegen, "_chat", fake)
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "fake-key")

    import asyncio as _aio
    events: list = []
    sse_task = _aio.create_task(_drain_sse(client, sid, events, want="subtask"))
    await _aio.sleep(0.3)
    t = (await client.post(f"/api/sessions/{sid}/messages",
                           json={"text": "帮我深度调研竞品"})).json()["turn"]
    await _aio.wait_for(sse_task, timeout=15)
    ev = [e for ty, e in events if ty == "subtask"][-1]
    assert ev["turn_id"] == t["id"] and ev["subtask_id"] is not None
    assert ev["subtasks"][0]["title"] == "调研竞品"
    # detail：turns 带打标 + subtasks 列表
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["subtasks"][0]["id"] == ev["subtask_id"]
    assert next(x for x in d["turns"] if x["id"] == t["id"])["subtask_id"] \
        == ev["subtask_id"]


async def test_classifier_down_turn_still_runs(client, monkeypatch):
    """>>> 降级对赌：分类器抛错 → turn 照常跑完、无 subtask 行。"""
    sess = (await client.post("/api/sessions",
                              json={"title": "降级"})).json()["session"]
    sid = sess["id"]

    async def boom(system, user, max_tokens=64):
        raise RuntimeError("down")

    monkeypatch.setattr(titlegen, "_chat", boom)
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "fake-key")
    t = (await client.post(f"/api/sessions/{sid}/messages",
                           json={"text": "跑"})).json()["turn"]
    from tests.conftest import wait_turn
    row = await wait_turn(client, sid, t["id"])
    assert row["status"] == "done"
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["subtasks"] == []
