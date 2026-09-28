"""workdaddy ↔ hahaness 灰度 e2e：engine=hahaness 走通 API→SSE→记账全链。

hahaness 经 HAHANESS_PROVIDER=fake 驱动（零 token），.fake 控制文件落在
workspace/<sid>/.fake/（fake provider 默认读 cwd/.fake）。HAHANESS_HOME
指向临时目录隔离 transcript。
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from loadn_webui import db as db_mod
from loadn_webui.config import CONFIG


@pytest.fixture()
def hahaness_engine(tmp_path, monkeypatch):
    """默认引擎切 hahaness + HAHANESS_HOME 隔离（结束还原）。"""
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


async def _collect_events(client, sid: str, want: set[str], timeout_s: float = 25):
    """SSE 收齐目标事件（event:/data: 行协议；超时软截止）。"""
    got: dict[str, dict] = {}
    t0 = time.monotonic()
    etype = data = None
    async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if time.monotonic() - t0 > timeout_s:
                break
            if line.startswith("event: "):
                etype = line[7:]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    data = None
            elif not line.strip() and etype:
                if etype in want:
                    got.setdefault(etype, dict(data or {}))
                    if want <= set(got):
                        return got
                etype = data = None
    return got


async def _wait_terminal(client, sid: str, tid: int, timeout_s: float = 30) -> dict:
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.2)
        resp = await client.get(f"/api/sessions/{sid}")
        t = next((x for x in resp.json()["turns"] if x["id"] == tid), None)
        if t and t["status"] in ("done", "error", "stopped", "interrupted"):
            return t
    raise TimeoutError("turn 未到终态")


async def test_hahaness_happy_path_resume(client, ws_root, hahaness_engine):
    """首条消息全链（SSE/记账/transcript）+ 第二条消息 resume 簿记。"""
    r = await client.post("/api/sessions", json={"title": "hh 灰度"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, reply="hahaness 首答")

    sse_task = asyncio.create_task(
        _collect_events(client, sid, {"text", "turn_done"}))
    await asyncio.sleep(0.4)                     # 先订阅
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "问一"})
    tid = r.json()["turn"]["id"]
    got = await asyncio.wait_for(sse_task, timeout=25)
    assert "turn_done" in got and "text" in got
    assert "hahaness 首答" in got["text"]["text"]

    with db_mod.conn() as c:
        turn = db_mod.get_turn(c, tid)
        sess = db_mod.get_session(c, sid)
    assert turn["status"] == "done"
    assert turn["usage_json"] and json.loads(turn["usage_json"])["input_tokens"] > 0
    assert turn["models_json"] and "fake" in turn["models_json"]
    assert turn["num_turns"] >= 1
    assert sess["engine"] in ("hahaness", "loadn")
    hh_sid = sess["claude_session_id"]
    assert hh_sid
    # transcript 落在隔离的 HAHANESS_HOME
    assert (hahaness_engine / "sessions" / hh_sid / "transcript.jsonl").exists()

    # 第二条消息：resume 同一 hahaness 会话
    _fake_dir(ws_root, sid, reply="hahaness 二答")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "问二"})
    tid2 = r.json()["turn"]["id"]
    t2 = await _wait_terminal(client, sid, tid2)
    assert t2["status"] == "done"
    with db_mod.conn() as c:
        turn2 = db_mod.get_turn(c, tid2)
    assert turn2["claude_session_id"] == hh_sid and turn2["resume"] == 1


async def test_hahaness_tools_and_todos_sse(client, ws_root, hahaness_engine):
    """工具链路 + todos（TodoWrite 分支）经 SSE 可见。"""
    r = await client.post("/api/sessions", json={"title": "hh 工具"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, tools=json.dumps(
        {"name": "Bash", "input": {"command": "echo tool-ok"}}))

    want = {"tool_use", "tool_result", "turn_done"}
    sse_task = asyncio.create_task(_collect_events(client, sid, want))
    await asyncio.sleep(0.4)
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "跑工具"})
    got = await asyncio.wait_for(sse_task, timeout=25)
    assert want <= set(got), f"缺事件: {want - set(got)}"
    assert "tool-ok" in got["tool_result"]["result"]
    assert got["tool_use"]["name"] == "Bash"


async def test_hahaness_todowrite_branch(client, ws_root, hahaness_engine):
    """fake 的 todos 旋钮（TodoWrite 工具）→ workdaddy todos 事件。"""
    r = await client.post("/api/sessions", json={"title": "hh todos"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, todos="", reply="清单好了")

    sse_task = asyncio.create_task(_collect_events(client, sid, {"todos", "turn_done"}))
    await asyncio.sleep(0.4)
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "列清单"})
    got = await asyncio.wait_for(sse_task, timeout=25)
    assert "todos" in got and got["todos"]["todos"]


async def test_hahaness_resume_reject_rotates(client, ws_root, hahaness_engine):
    """resume 秒拒（Session not found）×2 → 轮换 → fresh 也秒拒 → error（有界）。"""
    r = await client.post("/api/sessions", json={"title": "hh 秒拒"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, reply="先正常")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "第一轮"})
    t1 = await _wait_terminal(client, sid, r.json()["turn"]["id"])
    assert t1["status"] == "done"                  # fresh=0

    _fake_dir(ws_root, sid, fastfail="")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "秒拒"})
    tid = r.json()["turn"]["id"]
    t2 = await _wait_terminal(client, sid, tid, timeout_s=40)
    assert t2["status"] == "error"
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        turns = c.execute("SELECT status, error FROM turns WHERE session_id=?",
                          (sid,)).fetchall()
    assert any("Session not found" in (x["error"] or "") for x in turns)
    assert sess["session_fresh"] == 1              # 轮换过：下次 fresh


async def test_hahaness_inuse_flip_to_resume(client, ws_root, hahaness_engine):
    """fresh + transcript 已存在 → CLI 锁拒 already in use → 翻 resume 成功。

    模拟重启打断场景：手动把 session_fresh 翻回 1（transcript 仍在）。
    """
    r = await client.post("/api/sessions", json={"title": "hh 翻转"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, reply="第一轮正常")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "先跑"})
    await _wait_terminal(client, sid, r.json()["turn"]["id"])

    with db_mod.conn() as c:
        db_mod.update_session(c, sid, session_fresh=1)   # 模拟未及翻位的中断

    _fake_dir(ws_root, sid, reply="翻转后续上")
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "再来"})
    tid = r.json()["turn"]["id"]
    t = await _wait_terminal(client, sid, tid, timeout_s=40)
    assert t["status"] == "done"                   # 翻 resume 后成功续作
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    assert sess["session_fresh"] == 0


async def test_hahaness_bigusage_rotation(client, ws_root, hahaness_engine):
    """input 600k > assistant.rotate 阈值 300k → 会话轮换（session_rotated）。"""
    r = await client.post("/api/sessions", json={"title": "hh 轮换",
                                                 "profile": "assistant"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, bigusage="", reply="大上下文")

    sse_task = asyncio.create_task(
        _collect_events(client, sid, {"turn_done", "session_rotated"}))
    await asyncio.sleep(0.4)
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "胀"})
    got = await asyncio.wait_for(sse_task, timeout=25)
    assert "session_rotated" in got
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
    assert sess["session_fresh"] == 1              # 已轮换：下次 fresh


async def test_hahaness_stop(client, ws_root, hahaness_engine):
    """hang 场景外部 stop → turn_stopped 终态。"""
    r = await client.post("/api/sessions", json={"title": "hh 停止"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, hang="")

    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "挂住"})
    tid = r.json()["turn"]["id"]
    await asyncio.sleep(1.5)   # 让 turn 进入 running
    await client.post(f"/api/turns/{tid}/stop")
    t = await _wait_terminal(client, sid, tid)
    assert t["status"] == "stopped"


async def _collect_ordered(client, sid: str, stop_at: str, timeout_s: float = 25):
    """按序收集全部事件（type, data）直到 stop_at——delta 流测试用。"""
    out: list[tuple[str, dict]] = []
    t0 = time.monotonic()
    etype = data = None
    async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
        async for line in resp.aiter_lines():
            if time.monotonic() - t0 > timeout_s:
                break
            if line.startswith("event: "):
                etype = line[7:]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    data = None
            elif not line.strip() and etype:
                out.append((etype, dict(data or {})))
                if etype == stop_at:
                    return out
                etype = data = None
    return out


async def test_hahaness_live_text_deltas(client, ws_root, hahaness_engine):
    """--verbose 的 stream_event → 实时 text delta 事件（delta:true），先于
    turn_done；整块不重复直播；持久化消息内容不翻倍。"""
    r = await client.post("/api/sessions", json={"title": "delta 流"})
    sid = r.json()["session"]["id"]
    _fake_dir(ws_root, sid, reply="流式正文内容一二三四五")

    sse_task = asyncio.create_task(
        _collect_ordered(client, sid, "turn_done"))
    await asyncio.sleep(0.4)
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "说点啥"})
    tid = r.json()["turn"]["id"]
    evs = await asyncio.wait_for(sse_task, timeout=25)

    text_evs = [d for t, d in evs if t == "text" and d.get("turn_id") == tid]
    assert text_evs, "没有收到任何 text 事件"
    assert all(e.get("delta") for e in text_evs), \
        f"text 事件应全部带 delta 标记: {text_evs}"
    joined = "".join(e.get("text") or "" for e in text_evs)
    assert "流式正文内容一二三四五" in joined          # delta 拼回完整正文
    # 整块直播被去重（streamed_kinds 命中 → 无非 delta 的 text 事件）
    assert not any(not e.get("delta") for e in text_evs)
    # turn_done 在 delta 之后（顺序保证：生成中可见）
    idx_text = next(i for i, (t, d) in enumerate(evs)
                    if t == "text" and d.get("turn_id") == tid)
    idx_done = next(i for i, (t, _) in enumerate(evs) if t == "turn_done")
    assert idx_text < idx_done
    # 持久化内容不翻倍：assistant 消息正文恰一次
    r = await client.get(f"/api/sessions/{sid}")
    texts = [m for m in r.json()["messages"] if m["role"] == "assistant"]
    joined_hist = "\n\n".join(m["content"] for m in texts)
    assert joined_hist.count("流式正文内容一二三四五") == 1
