"""P3-7 审批面板三要素 + 压缩时间线验收。

- 引擎面：mark_compact 落 tokens_cropped（0/None 省略）；compactor
  compact 后 last_dropped_tokens 估算 > 0（chars/4）
- 时间线端点：transcript 的 result/compact 事件 → markers（两次 compact
  两个标记，各带 tokens_cropped 与摘要首行；turn 刻度带 token 数）；
  无 transcript → 空数组
- 审批三要素 enrich：list_pending 带 params/justification/turn_changes
  （消费 P3-1 result 事件 diffs）；无 diff → turn_changes=None（UI 降级
  「本 turn 无文件改动」）
"""
from __future__ import annotations

import json
from pathlib import Path


def _write_transcript(sid: str, events: list[dict]) -> None:
    import os
    home = Path(os.environ["LOADN_HOME"])
    p = home / "sessions" / sid
    p.mkdir(parents=True, exist_ok=True)
    (p / "transcript.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8")


def _ev(type_: str, payload: dict) -> dict:
    return {"type": type_, "ts": 1.0, "session_id": "s", "uuid": "u",
            "parent_uuid": None, "payload": payload}


# ---------------------------------------------------------------- 引擎面
def test_mark_compact_tokens_cropped(tmp_path):
    from loadn.core.session import SessionManager
    sess = SessionManager.create(tmp_path, home=tmp_path / "home")
    sess.mark_compact("摘要第一行\n细节", tokens_cropped=12_345)
    sess.mark_compact("无裁量的压缩")                     # None → 省略键
    compacts = [e for e in sess.transcript.read_events()
                if e["type"] == "compact"]
    assert compacts[0]["payload"]["tokens_cropped"] == 12_345
    assert "tokens_cropped" not in compacts[1]["payload"]


async def test_compactor_dropped_tokens_estimate():
    from loadn.core.compactor import Compactor
    from loadn.types import Message, TextBlock

    class _Boom:
        async def chat(self, *a, **kw):                 # 摘要必失败→fallback
            raise RuntimeError("no provider")

    msgs: list[Message] = []
    for i in range(40):                    # 40 轮×~4k chars > 保留预算→有裁切
        body = "x" * 2_000
        msgs.append(Message(role="user", content=[TextBlock(
            text=f"指令{i} {body}")]))
        msgs.append(Message(role="assistant", content=[TextBlock(
            text=f"答复{i} {body}")]))
    c = Compactor(_Boom())
    new_msgs, did = await c.compact(msgs, context_window=200_000)
    assert did is True
    assert c.last_dropped_tokens > 0                    # chars/4 估算
    assert len(new_msgs) < len(msgs)


# ---------------------------------------------------------------- 时间线
async def test_timeline_two_compact_markers(client):
    r = await client.post("/api/sessions", json={"title": "时间线"})
    sid = r.json()["session"]["id"]
    _write_transcript(sid, [
        _ev("result", {"num_turns": 1, "usage": {"input_tokens": 100,
                                                 "output_tokens": 50}}),
        _ev("compact", {"summary": "第一行：改了 a.py\n第二行细节",
                        "tokens_cropped": 8000}),
        _ev("result", {"num_turns": 2, "usage": {"input_tokens": 300,
                                                 "output_tokens": 60}}),
        _ev("compact", {"summary": "第二段交接", "tokens_cropped": 5000}),
        _ev("result", {"num_turns": 3, "usage": {"input_tokens": 700,
                                                 "output_tokens": 80}}),
    ])
    d = (await client.get(f"/api/sessions/{sid}/timeline")).json()
    marks = d["timeline"]
    compacts = [m for m in marks if m["kind"] == "compact"]
    turns = [m for m in marks if m["kind"] == "turn"]
    assert len(compacts) == 2                            # 两次 compact 两标记
    assert compacts[0]["tokens_cropped"] == 8000
    assert compacts[0]["summary_first"].startswith("第一行：改了 a.py")
    assert compacts[1]["tokens_cropped"] == 5000
    assert len(turns) == 3 and turns[2]["tokens"] == 780


async def test_timeline_empty_without_transcript(client):
    r = await client.post("/api/sessions", json={"title": "无台账"})
    sid = r.json()["session"]["id"]
    d = (await client.get(f"/api/sessions/{sid}/timeline")).json()
    assert d["timeline"] == []


# ---------------------------------------------------------------- 三要素
async def test_approval_three_elements(client):
    r = await client.post("/api/sessions", json={"title": "审批"})
    sid = r.json()["session"]["id"]
    # 本 turn 有文件改动（P3-1 result 事件 diffs）
    _write_transcript(sid, [
        _ev("result", {"num_turns": 1, "usage": {}, "diffs": [
            {"path": "src/app.py", "hash": "ab12", "lines": "+12 -3"},
            {"path": "src/util.py", "hash": "cd34", "lines": "+5 -1"}]}),
    ])
    r = await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "bash_allow",
        "params": {"prefix": ["pip", "install"],
                   "justification": "装依赖完成构建（P0-4 规则文案）"},
        "note": "agent 附注"})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/sessions/{sid}/approvals")).json()
    a = d["approvals"][0]
    assert a["justification"].startswith("装依赖完成构建")
    assert a["params"]["prefix"] == ["pip", "install"]
    assert a["turn_changes"] and len(a["turn_changes"]) == 2
    assert a["turn_changes"][0]["path"] == "src/app.py"


async def test_approval_degrades_without_diff(client):
    """无 transcript/无 diffs → turn_changes=None（UI 降级文件清单文案）。"""
    r = await client.post("/api/sessions", json={"title": "降级"})
    sid = r.json()["session"]["id"]
    await client.post(f"/api/sessions/{sid}/approvals", json={
        "action_type": "bash_allow",
        "params": {"prefix": ["make"], "justification": ""}})
    d = (await client.get(f"/api/sessions/{sid}/approvals")).json()
    a = d["approvals"][0]
    assert a["turn_changes"] is None
    assert a["justification"] == ""
    # params_json 不再外泄原列（已解析成 params）
    assert "params_json" not in a
