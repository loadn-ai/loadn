"""随时插话（steering）：运行中消息下一轮 LLM 调用前注入，不等排队。

链路：API append .steer.<sid>.jsonl + at.steers 记账 → hahaness 主循环轮询注入
（发 steer 消费回执事件）→ _consume 标记 consumed → turn 结束仍未消费的
（插话落在最后一轮后）由 _finish 回队列为新 turn——不丢话。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from loadn_webui.claude_runner import StopHandle, TurnCall
from loadn_webui.engine import ENGINE, ActiveTurn


# ---------------------------------------------------------------- 单元层
def test_hahaness_argv_carries_steer_file(tmp_path):
    """loadn 引擎 spawn env 恒带 LOADN_STEER_FILE（指向 workspace）。"""
    from loadn_webui.engines.loadn import LoadnSpec
    ws = tmp_path / "ws"
    call = TurnCall(prompt="x", cwd=ws, session_id="", engine="hahaness", sid="s1")
    _, env = LoadnSpec().build_argv(call)
    assert env.get("LOADN_STEER_FILE") == str(ws / ".steer.s1.jsonl")


async def test_consume_steer_receipt_marks_consumed():
    """hahaness 的 steer 消费回执 → at.steers 对应条目标 consumed。"""
    sid, tid = "steer-unit", 99001
    at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(), started_at=0.0)
    at.steers = [{"text": "改打 game jam", "consumed": False},
                 {"text": "第二条", "consumed": False}]
    await ENGINE._consume(sid, tid, at, {"type": "steer", "text": "改打 game jam"})
    assert at.steers[0]["consumed"] is True
    assert at.steers[1]["consumed"] is False


# ---------------------------------------------------------------- e2e 层
async def _collect(client, sid: str, want: set[str], timeout_s: float = 25):
    """SSE 收齐目标事件（复用 e2e 模式：event:/data: 行协议）。"""
    got: dict[str, dict] = {}
    import time
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
                if etype in want:
                    got.setdefault(etype, dict(data or {}))
                    if want <= set(got):
                        return got
                etype = data = None
    return got


async def _wait_running(sid: str, tid: int, timeout_s: float = 10) -> ActiveTurn:
    """等 turn 进入 ENGINE.active（running 占位完成）。"""
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        if tid in ENGINE.active:
            return ENGINE.active[tid]
        await asyncio.sleep(0.05)
    raise TimeoutError("turn 未进入 running")


async def _wait_tool_round(at: ActiveTurn, timeout_s: float = 15) -> None:
    """等首轮 LLM 完成（at.blocks 出现 tool 块）——此后插话必然落在首轮
    轮询之后、次轮轮询之前的窗口里（工具还在执行），注入时机确定。"""
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        if any(b.get("type") == "tool" for b in at.blocks):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("首轮工具未出现（steer 注入窗口无法确定）")


async def test_steer_injected_midrun(client, ws_root, hahaness_engine):
    """运行中插话全链：API 写 .steer.jsonl → hahaness 下一轮注入（fake 回声
    末条 user 文本，插话出现在最终回复里）→ SSE steer 事件 → 不产生新 turn。"""
    r = await client.post("/api/sessions", json={"title": "插话直达"})
    sid = r.json()["session"]["id"]
    # 慢工具撑开注入窗口：首轮 tool_use → Bash sleep（执行期发插话）→ 次轮回声
    fake = ws_root / sid / ".fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "sleep 4 && echo slow"}}))

    sse = asyncio.create_task(_collect(client, sid, {"steer", "turn_done"}))
    await asyncio.sleep(0.4)
    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "调研比赛"})
    tid = r.json()["turn"]["id"]
    at = await _wait_running(sid, tid)
    await _wait_tool_round(at)          # 首轮 LLM 已回：首轮轮询已过、工具执行中

    r = await client.post(f"/api/sessions/{sid}/steer",
                          json={"text": "别打 kaggle，改打 game jam"})
    assert r.json() == {"ok": True, "steered": True}
    assert "别打 kaggle" in (ws_root / sid / f".steer.{sid}.jsonl").read_text()

    got = await asyncio.wait_for(sse, timeout=30)
    assert got["steer"]["text"] == "别打 kaggle，改打 game jam"
    assert got["steer"]["turn_id"] == tid
    assert "turn_done" in got
    # 注入证明：hahaness transcript 落了插话 user 事件（含转向指引文案）
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        hh_sid = db_mod.get_session(c, sid)["claude_session_id"]
    ts = (hahaness_engine / "sessions" / hh_sid / "transcript.jsonl").read_text(
        encoding="utf-8")
    assert "用户插话" in ts and "game jam" in ts
    # 已消费 → 不回队列：仍只有一个用户消息、一个 turn
    r = await client.get(f"/api/sessions/{sid}")
    assert len([m for m in r.json()["messages"] if m["role"] == "user"]) == 1
    assert len(r.json()["turns"]) == 1


async def test_steer_missed_requeued_as_turn(client, ws_root, hahaness_engine):
    """插话没赶上注入窗口（turn 最后一轮后才轮询）→ _finish 按原话回队列，
    新 turn 正常执行——插话不丢话。以删 steer 文件模拟「轮询前消失」。"""
    r = await client.post("/api/sessions", json={"title": "插话回队"})
    sid = r.json()["session"]["id"]
    fake = ws_root / sid / ".fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "sleep 2 && echo slow"}}))

    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "干活"})
    tid = r.json()["turn"]["id"]
    at = await _wait_running(sid, tid)

    r = await client.post(f"/api/sessions/{sid}/steer", json={"text": "转去写周报"})
    assert r.json()["steered"] is True
    (ws_root / sid / f".steer.{sid}.jsonl").unlink()      # hahaness 永远读不到 → 未消费
    assert any(not s["consumed"] for s in at.steers)

    # 原 turn 完成 + 回队 turn 也完成（fake 回声插话原文）
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        turns = (await client.get(f"/api/sessions/{sid}")).json()["turns"]
        if len(turns) >= 2 and all(t["status"] == "done" for t in turns[:2]):
            break
        await asyncio.sleep(0.2)
    else:
        pytest.fail(f"回队 turn 未完成: {turns}")
    msgs = (await client.get(f"/api/sessions/{sid}")).json()["messages"]
    user_texts = [m["content"] for m in msgs if m["role"] == "user"]
    assert any("转去写周报" in t for t in user_texts)   # 原话成为新 turn 输入


async def test_steer_no_running_turn_falls_back(client, ws_root):
    """无运行中 turn（或非 hahaness）→ steered=false，前端回落正常排队。"""
    r = await client.post("/api/sessions", json={"title": "无运行"})
    sid = r.json()["session"]["id"]
    r = await client.post(f"/api/sessions/{sid}/steer", json={"text": "没人在跑"})
    assert r.json() == {"ok": True, "steered": False}
    assert not (ws_root / sid / f".steer.{sid}.jsonl").exists()
    # 空 text 拒绝
    r = await client.post(f"/api/sessions/{sid}/steer", json={"text": "  "})
    assert r.status_code == 400


@pytest.fixture()
def hahaness_engine(tmp_path, monkeypatch):
    """默认引擎切 hahaness + HAHANESS_HOME 隔离（与 e2e_hahaness 同构）。"""
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.engines, "default", "hahaness")
    home = tmp_path / "hahaness_home"
    home.mkdir()
    monkeypatch.setenv("HAHANESS_HOME", str(home))
    monkeypatch.setenv("LOADN_HOME", str(home))
    monkeypatch.setenv("HAHANESS_PROVIDER", "fake")
    return home
