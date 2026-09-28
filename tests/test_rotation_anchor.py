"""断线回放 P0 组合测试：轮换 anchor（pending_anchor）+ transcript 尾部摘要
+ 中断 turn 半程内容补记账。

- summarize_transcript_tail：双信封（claude CLI message.content / loadn
  payload.content）、todos 状态机、坏行/缺文件兜底
- spec.session_tail：loadn（LOADN_HOME）与 claude（HOME 下 projects 树）
- token 轮换：pending_anchor 暂存 → 下一 turn 注入 prompt 后清空
- resume 秒拒轮换：anchor 注入轮换后的重试 prompt
- 重启中断：turn 保持 interrupted，但停机前已产出内容补进 messages
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from loadn_webui import db as db_mod
from loadn_webui.engines.base import summarize_transcript_tail
from loadn_webui.engines.claude import ClaudeSpec
from loadn_webui.engines.loadn import LoadnSpec
from tests.conftest import wait_turn

# ---------------------------------------------------------------- 尾部摘要（单元）
_TODO_CALL = {"type": "tool_use", "id": "t1", "name": "TodoWrite",
              "input": {"todos": [
                  {"content": "检索来源", "status": "completed"},
                  {"content": "写报告", "status": "in_progress"}]}}


def test_summarize_claude_envelope(tmp_path: Path):
    """claude CLI transcript 信封（message.content）：用户意图 + todos + 最后输出。"""
    p = tmp_path / "t.jsonl"

    def ev(type_: str, content: list) -> str:
        return json.dumps({"type": type_, "message": {"role": type_,
                                                      "content": content}},
                          ensure_ascii=False)

    p.write_text("\n".join([
        ev("user", [{"type": "text", "text": "调研固态电池"}]),
        ev("assistant", [{"type": "text", "text": "开始检索"}]),
        ev("assistant", [dict(_TODO_CALL)]),
        ev("user", [{"type": "tool_result", "tool_use_id": "t1",
                     "content": "ok"}]),          # 工具结果不算用户指令
        "half-line-no-json",                     # 被杀半行
        ev("assistant", [{"type": "text", "text": "已收集 12 篇文献，正在综合"}]),
    ]) + "\n", encoding="utf-8")
    s = summarize_transcript_tail(p)
    assert "【用户最后指令】调研固态电池" in s
    assert "【任务清单】" in s and "[✓] 检索来源" in s and "[▶] 写报告" in s
    assert "【最后输出】已收集 12 篇文献" in s


def test_summarize_loadn_envelope_and_taskcreate(tmp_path: Path):
    """loadn transcript 信封（payload.content）+ TaskCreate/TaskUpdate 状态机。"""
    p = tmp_path / "t.jsonl"

    def ev(type_: str, content: list) -> str:
        return json.dumps({"type": type_, "payload": {"content": content}},
                          ensure_ascii=False)

    p.write_text("\n".join([
        ev("user", [{"type": "text", "text": "拆解任务"}]),
        ev("assistant", [{"type": "tool_use", "id": "a", "name": "TaskCreate",
                          "input": {"subject": "跑实验"}}]),
        ev("assistant", [{"type": "tool_use", "id": "b", "name": "TaskCreate",
                          "input": {"subject": "分析数据"}}]),
        ev("assistant", [{"type": "tool_use", "id": "c", "name": "TaskUpdate",
                          "input": {"taskId": "1", "status": "completed"}}]),
        ev("assistant", [{"type": "text", "text": "实验跑完"}]),
    ]) + "\n", encoding="utf-8")
    s = summarize_transcript_tail(p)
    assert "[✓] 跑实验" in s and "[ ] 分析数据" in s
    assert "【用户最后指令】拆解任务" in s and "【最后输出】实验跑完" in s


def test_summarize_empty_and_truncation(tmp_path: Path):
    assert summarize_transcript_tail(tmp_path / "absent.jsonl") == ""
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "system", "message": {"content": "x"}}) + "\n")
    assert summarize_transcript_tail(p) == ""          # 无可提取内容
    big = tmp_path / "big.jsonl"
    big.write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "长" * 5000}]}}) + "\n")
    s = summarize_transcript_tail(big, max_chars=500)
    assert len(s) <= 500 and s.startswith("【最后输出】长")


def test_spec_session_tail_loadn(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path))
    sid = str(uuid.uuid4())
    d = tmp_path / "sessions" / sid
    d.mkdir(parents=True)
    (d / "transcript.jsonl").write_text(json.dumps({
        "type": "user", "payload": {"content": [{"type": "text", "text": "任务甲"}]}}
    ) + "\n")
    assert "任务甲" in LoadnSpec().session_tail(sid)
    assert LoadnSpec().session_tail(str(uuid.uuid4())) == ""


def test_spec_session_tail_claude(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))       # Path.home() 读 HOME
    sid = str(uuid.uuid4())
    d = tmp_path / ".claude" / "projects" / "proj-1"
    d.mkdir(parents=True)
    (d / f"{sid}.jsonl").write_text(json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "claude 尾部输出"}]}}
    ) + "\n")
    assert "claude 尾部输出" in ClaudeSpec().session_tail(sid)
    assert ClaudeSpec().session_tail(str(uuid.uuid4())) == ""


# ---------------------------------------------------------------- token 轮换 anchor
async def test_rotation_stores_pending_anchor_and_consumes(client, ws_root,
                                                            fake_calls):
    """bigusage 轮换 → pending_anchor 暂存（含磁盘台账指引）；下一 turn 注入
    prompt 开头并在起跑时清空（一次性，不跨 turn 泄漏）。"""
    r = await client.post("/api/sessions",
                          json={"title": "轮换anchor", "profile": "researcher"})
    sid = r.json()["session"]["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "bigusage").touch()

    t1 = (await client.post(f"/api/sessions/{sid}/messages",
                            json={"text": "大上下文"})).json()["turn"]
    await wait_turn(client, sid, t1["id"])
    with db_mod.conn() as c:
        anchor = db_mod.get_session(c, sid)["pending_anchor"]
    assert anchor and "已轮换" in anchor and "PROGRESS.md" in anchor

    # 撤掉 bigusage：第二轮正常用量，不再轮换——单测 pending_anchor 消费链
    (ctrl / "bigusage").unlink()
    t2 = (await client.post(f"/api/sessions/{sid}/messages",
                            json={"text": "第二条"})).json()["turn"]
    await wait_turn(client, sid, t2["id"])
    prompt = fake_calls()[-1]["prompt"]
    assert prompt.startswith(anchor.split("\n")[0])       # anchor 前置进 prompt
    assert "第二条" in prompt
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["pending_anchor"] is None  # 已清


async def test_resume_rejected_rotation_injects_anchor(client, ws_root, fake_calls):
    """resume 连败轮换：轮换重试的 prompt 带磁盘台账指引 anchor。"""
    r = await client.post("/api/sessions", json={"title": "秒拒轮换"})
    sid = r.json()["session"]["id"]
    t1 = (await client.post(f"/api/sessions/{sid}/messages",
                            json={"text": "先跑通"})).json()["turn"]
    await wait_turn(client, sid, t1["id"])
    n0 = len(fake_calls())
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "fastfail").touch()
    t2 = (await client.post(f"/api/sessions/{sid}/messages",
                            json={"text": "会秒拒"})).json()["turn"]
    t2 = await wait_turn(client, sid, t2["id"], timeout_s=30)
    calls = fake_calls()[n0:]
    assert len(calls) == 3                    # resume×2 + 轮换 fresh×1
    p3 = calls[2]["prompt"]
    assert "已轮换" in p3 and "PROGRESS.md" in p3 and "会秒拒" in p3


# ---------------------------------------------------------------- 中断补记账
async def test_interrupted_salvages_partial_output(client, ws_root, monkeypatch):
    """pid 死 + 无 result 的中断 turn：turn 保持 interrupted，但停机前已产出
    的 assistant 内容从输出日志补进 messages（带中断前缀）。"""
    from loadn_webui.engine import ENGINE
    monkeypatch.setenv("LOADN_FAKE_HANG_S", "1")
    r = await client.post("/api/sessions", json={"title": "中断抢救"})
    sid = r.json()["session"]["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()
    tid = (await client.post(f"/api/sessions/{sid}/messages",
                            json={"text": "长任务"})).json()["turn"]["id"]

    # 等 running（CLI 已流出部分 assistant 内容）
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < 15:
        d = (await client.get(f"/api/sessions/{sid}")).json()
        if d["turns"] and d["turns"][-1]["status"] == "running":
            break
        await asyncio.sleep(0.2)
    # 模拟 daemon 死：cancel worker（不杀子进程）
    w = ENGINE._workers.get(sid)
    assert w is not None and not w.done()
    w.cancel()
    with_context = ENGINE._workers.pop(sid, None)
    ENGINE._queues.pop(sid, None)
    for t in list(ENGINE.active):
        if ENGINE.active[t].session_id == sid:
            ENGINE.active.pop(t)
    await asyncio.sleep(1.6)                  # hang 1s 自然结束（无 result 行）

    out = ENGINE.recover_after_restart()
    assert tid not in out["adopted"]
    t = await wait_turn(client, sid, tid, timeout_s=15)
    assert t["status"] == "interrupted"
    # 补记账是 fire-and-forget：轮询等 salvage 任务落库
    t0 = asyncio.get_running_loop().time()
    salvaged = None
    while asyncio.get_running_loop().time() - t0 < 15:
        d = (await client.get(f"/api/sessions/{sid}")).json()
        salvaged = next((m for m in d["messages"]
                         if m["role"] == "assistant" and "服务重启中断" in m["content"]),
                        None)
        if salvaged is not None:
            break
        await asyncio.sleep(0.3)
    assert salvaged is not None, "中断 turn 半程内容未补记账"
    assert "开始长任务" in salvaged["content"]      # hang 场景已流出的文本
    blocks = json.loads(salvaged["blocks_json"])
    assert any(b.get("type") == "text" for b in blocks)
