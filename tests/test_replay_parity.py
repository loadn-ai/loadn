"""回放与实时渲染对齐（2026-10-19 用户拍板「统一成实时的那种格式」）：

重载后聊天记录此前缺三样（数据层根因——只落 text/thinking/tool 三类块）：
① 任务清单 TodoList ② 插话卡 SteerNote ③ 子代理思考 cum 累计行。
本文件对赌引擎侧新块生成：
- todos：TaskCreate/TaskUpdate/TodoWrite 三路径每次变更落全量快照块
  （深拷贝，后续变更不污染已落快照；回放端取最后一块）
- steer：steer_if_running 落块进 blocks 时间线；retract_steer 摘块
- subagent_thinking：cum 透传落块
"""
from __future__ import annotations

import json

from loadn_webui.claude_runner import StopHandle
from loadn_webui.engine import ENGINE, ActiveTurn


def _at(sid: str = "replay-parity", tid: int = 99010) -> ActiveTurn:
    return ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(), started_at=0.0)


def _todo_blocks(at: ActiveTurn) -> list[dict]:
    return [b for b in at.blocks if b.get("type") == "todos"]


# ------------------------------------------------------------ todos 快照块

async def test_todos_snapshot_three_paths_and_isolation():
    """TaskCreate→TodoWrite(覆盖)→TaskUpdate(更新) 三路径均落快照块；
    每块是当时全量的深拷贝——后续变更不回写污染已落块。"""
    at = _at()
    await ENGINE._consume("replay-parity", at.turn_id, at, {
        "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "TaskCreate",
             "input": {"subject": "装环境"}},
            {"type": "tool_use", "id": "t2", "name": "TaskCreate",
             "input": {"subject": "写报告"}},
        ]}})
    snap1 = _todo_blocks(at)
    assert len(snap1) == 2
    assert snap1[0]["todos"] == [{"subject": "装环境", "status": "pending"}]
    assert snap1[1]["todos"] == [{"subject": "装环境", "status": "pending"},
                                 {"subject": "写报告", "status": "pending"}]

    await ENGINE._consume("replay-parity", at.turn_id, at, {
        "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t3", "name": "TodoWrite",
             "input": {"todos": [{"content": "新清单A", "status": "in_progress"},
                                 {"content": "新清单B", "status": "pending"}]}},
        ]}})
    snap2 = _todo_blocks(at)
    assert len(snap2) == 3
    assert snap2[-1]["todos"][0]["status"] == "in_progress"

    await ENGINE._consume("replay-parity", at.turn_id, at, {
        "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t4", "name": "TaskUpdate",
             "input": {"taskId": 2, "status": "completed"}},
        ]}})
    snap3 = _todo_blocks(at)
    assert snap3[-1]["todos"][1]["status"] == "completed"
    # 深拷贝对赌：最早的快照仍停在首个任务 pending（后续 TaskCreate/TodoWrite/
    # TaskUpdate 均未回写污染它）
    assert snap3[0]["todos"] == [{"subject": "装环境", "status": "pending"}]


async def test_todos_block_json_roundtrip():
    """blocks 含 todos 块序列化→解析无损（前端 parseBlocks 同语义消费）。"""
    at = _at()
    await ENGINE._consume("replay-parity", at.turn_id, at, {
        "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "TaskCreate",
             "input": {"subject": "s"}},
            {"type": "text", "text": "正文"},
        ]}})
    raw = json.loads(json.dumps(at.blocks, ensure_ascii=False))
    kinds = [b["type"] for b in raw]
    assert kinds == ["tool", "todos", "text"]   # 快照块落在变更时的时间线位置


# ------------------------------------------------------------ steer 块

async def test_steer_block_appended_and_retracted(tmp_path, monkeypatch):
    """插话落块进 blocks（时间线原位）；撤回同步摘块（等同没说过）。"""
    from loadn_webui import engine as engine_mod
    sid, tid = "replay-steer", 99011
    at = _at(sid, tid)
    ENGINE.active[tid] = at
    try:
        monkeypatch.setattr(ENGINE, "publish", lambda *a, **k: None)   # SSE/落库链 e2e 另测
        monkeypatch.setattr(engine_mod.db_mod, "conn", lambda: _ctx({"engine": "loadn"}))
        monkeypatch.setattr(engine_mod.db_mod, "get_turn",
                            lambda c, t: {"engine": "loadn"})
        monkeypatch.setattr(engine_mod.ws_mod, "ws_of",
                            lambda s: tmp_path / "ws" / s)
        assert ENGINE.steer_if_running(sid, "先把测试跑了") == tid
        assert at.blocks == [{"type": "steer", "text": "先把测试跑了",
                              "id": at.blocks[0]["id"]}]
        steer_id = at.blocks[0]["id"]

        ok, _ = ENGINE.retract_steer(sid, steer_id)
        assert ok
        assert at.blocks == []                       # 撤回=摘块，回放无痕

        ok2, _ = ENGINE.retract_steer(sid, "nonexist")
        assert not ok2                               # 重复撤回拒（幂等护栏）
    finally:
        ENGINE.active.pop(tid, None)


# ------------------------------------------------------------ cum 落块

async def test_subagent_thinking_cum_persisted():
    """subagent_thinking 块带 cum（回放收起行「已 N 字」显示用）。"""
    at = _at()
    await ENGINE._consume("replay-parity", at.turn_id, at, {
        "type": "subagent_thinking", "n": 2, "text": "思考片段…", "cum": 12345})
    blk = next(b for b in at.blocks if b.get("type") == "thinking")
    assert blk["cum"] == 12345
    assert blk["agent_n"] == 2


# ------------------------------------------------------------ helper

class _ctx:
    """nullcontext 替身：with db_mod.conn() as c → c=row（不走真库）。"""
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self._row

    def __exit__(self, *exc):
        return False
