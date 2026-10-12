"""子代理思考低频转发（2026-10-12）：聚合→节流→归属→渲染全链。

实况归因：子代理 thinking 此前完全不转发（「噪声不转」），主控端派发
高峰期只见工具卡刷屏，过程感为零。改造：fwd_emit 聚合缓冲 + 时间/字数
双阈值冲刷 subagent_thinking 事件（stream_json 透传）→ 宿主 _consume
派生归属（agent_id=sub_N + 学习表人名）落 blocks + SSE thinking 事件
（带 agent_n/cum 扩展字段）→ 前端「子N 思考中… 已 N 字」折叠卡与
Agent 视角思考时间线。
"""
from __future__ import annotations

import asyncio
import json

import tests.helpers as H
from loadn.core.session import SessionManager
from loadn.core.subagent import SubagentManager, TaskTool
from loadn.providers import Chunk
from loadn.tools.base import ToolContext


# ---------------------------------------------------------------- 构造件
def _think_round(text: str) -> list[Chunk]:
    """纯思考轮（无 text/tool）：终局 assistant content 只含 ThinkingBlock。"""
    return [Chunk(kind="thinking_delta", text=text),
            Chunk(kind="usage", usage={"input_tokens": 100}),
            Chunk(kind="stop", usage={"input_tokens": 100, "output_tokens": 20},
                  stop_reason="end_turn")]


def _think_tool_round(text: str, tid: str = "t1") -> list[Chunk]:
    """思考+工具混合轮：无 tool_use 的轮会被 loop 视为终局，思考必须
    搭工具才能多轮连续（真实子代理形态也是思考→调工具→再思考）。"""
    import json as _j
    raw = _j.dumps({"msg": "x"})
    return [Chunk(kind="thinking_delta", text=text),
            Chunk(kind="input_json_delta", tool_use_id=tid,
                  tool_name="Echo", partial_json=raw),
            Chunk(kind="stop", usage={"input_tokens": 100, "output_tokens": 30},
                  stop_reason="tool_use")]


class _Registry:
    def __init__(self, tools=None):
        self._tools = tools or {"Echo": H.EchoTool()}

    def get(self, name):
        return self._tools.get(name)


def _mgr(tmp_path, rounds):
    session = SessionManager.create(tmp_path, home=tmp_path)
    return SubagentManager(
        registry=_Registry(),
        provider_factory=lambda: H.ScriptedProvider(rounds),
        cwd=tmp_path, session_id=session.session_id, home=tmp_path)


# ---------------------------------------------------------------- 转发聚合
async def test_fwd_aggregates_and_flushes_by_chars(tmp_path, monkeypatch):
    import loadn.core.subagent as sub_mod
    monkeypatch.setattr(sub_mod, "SUB_THINK_FLUSH_CHARS", 10)
    monkeypatch.setattr(sub_mod, "SUB_THINK_FLUSH_S", 9999.0)
    a, b = "思考甲" * 8, "思考乙" * 8          # 各 24 字 > 阈值 10
    mgr = _mgr(tmp_path, [_think_tool_round(a, "t1"), _think_tool_round(b, "t2"),
                          H.text_round("结论")])
    events: list[dict] = []

    async def emit(ev: dict) -> None:
        events.append(ev)

    await mgr.task("调研", emit=emit)
    subs = [e for e in events if e.get("type") == "subagent_thinking"]
    assert len(subs) == 2, [e.get("type") for e in events]
    assert subs[0]["n"] == 1 and subs[0]["text"] == a and subs[0]["cum"] == len(a)
    assert subs[1]["n"] == 1 and subs[1]["text"] == b
    assert subs[1]["cum"] == len(a) + len(b)   # cum 为累计，非块内计数
    # text 不转发（final text 由 Task 结果卡回传，不重复）
    assert not any(e.get("type") == "text" for e in events)
    # Task 开始卡仍在（assistant + ToolUseBlock name=Task）
    assert any(e.get("type") == "assistant"
               and any(getattr(b2, "name", "") == "Task"
                       for b2 in e["message"].content) for e in events)


async def test_fwd_tail_flush_on_finish(tmp_path, monkeypatch):
    """短思考（低于双阈值）→ 收尾兜底冲刷一条，不静默丢失。"""
    import loadn.core.subagent as sub_mod
    monkeypatch.setattr(sub_mod, "SUB_THINK_FLUSH_CHARS", 10_000)
    monkeypatch.setattr(sub_mod, "SUB_THINK_FLUSH_S", 9999.0)
    mgr = _mgr(tmp_path, [_think_tool_round("短", "t1"), H.text_round("好")])
    events: list[dict] = []

    async def emit(ev: dict) -> None:
        events.append(ev)

    out = await mgr.task("小任务", emit=emit)
    subs = [e for e in events if e.get("type") == "subagent_thinking"]
    assert len(subs) == 1 and subs[0]["text"] == "短" and subs[0]["cum"] == 1
    assert out == "好"   # 主返回值不受转发影响


async def test_fwd_block_cap_truncates_but_cum_counts_all(
        tmp_path, monkeypatch):
    import loadn.core.subagent as sub_mod
    monkeypatch.setattr(sub_mod, "SUB_THINK_FLUSH_CHARS", 4)
    monkeypatch.setattr(sub_mod, "SUB_THINK_BLOCK_CAP", 6)
    big = "深" * 20
    mgr = _mgr(tmp_path, [_think_tool_round(big, "t1"), H.text_round("完")])
    events: list[dict] = []

    async def emit(ev: dict) -> None:
        events.append(ev)

    await mgr.task("大思考", emit=emit)
    subs = [e for e in events if e.get("type") == "subagent_thinking"]
    assert len(subs) == 1
    assert subs[0]["text"] == "深" * 6          # 单块截断
    assert subs[0]["cum"] == 20                 # 累计仍按全量


# ---------------------------------------------------------------- 单发接线
async def test_task_tool_wires_emit_via_ctx_extras(tmp_path):
    """单发 Task 也走转发：emit 从 ctx.extras 取（loop.run_turn 每轮注入）。"""
    mgr = _mgr(tmp_path, [H.text_round("子结果")])
    tool = TaskTool(mgr)
    ctx = ToolContext(cwd=tmp_path)
    events: list[dict] = []

    async def emit(ev: dict) -> None:
        events.append(ev)

    ctx.extras["emit"] = emit
    out = await tool.execute({"prompt": "做事"}, ctx)
    assert out == "子结果"
    # Task 开始卡经主 emit 外发（此前 emit=None 全程黑箱）
    assert any(e.get("type") == "assistant"
               and any(getattr(b, "name", "") == "Task"
                       for b in e["message"].content) for e in events)
    # ctx=None 兼容（既有单测路径 execute(args, None) 不炸）
    out2 = await tool.execute({"prompt": "再来"}, None)
    assert out2  # ScriptedProvider 耗尽后回放 "(scripted done)"


# ---------------------------------------------------------------- 输出层
async def test_stream_json_passes_subagent_thinking(capsys):
    """async 同构（asyncio_mode=auto 每测试独立 loop）——此前同步版用
    get_event_loop().run_until_complete，全量跑时被先前的测试污染挂掉。"""
    from loadn.cli.stream_json import StreamJsonEmitter
    emitter = StreamJsonEmitter(model="fake", tools=[])
    emitter.session_id = "s-think"
    await emitter({
        "type": "subagent_thinking", "n": 2, "text": "想", "cum": 7})
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines()
             if ln.strip()]
    assert lines and lines[-1]["type"] == "subagent_thinking"
    assert lines[-1]["n"] == 2 and lines[-1]["text"] == "想"
    assert lines[-1]["cum"] == 7 and lines[-1]["session_id"] == "s-think"


# ---------------------------------------------------------------- 宿主归属
async def test_engine_consume_subagent_thinking():
    from loadn_webui.claude_runner import StopHandle
    from loadn_webui.engine import ENGINE, ActiveTurn
    sid, tid = "think-fwd-sid", 99930
    q: asyncio.Queue = asyncio.Queue()
    ENGINE._subs[sid] = {q}
    at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(),
                    started_at=asyncio.get_event_loop().time())
    at.agent_names["sub_1"] = "马洛"    # Task 开始卡先到的学习表
    try:
        await ENGINE._consume(sid, tid, at, {
            "type": "subagent_thinking", "n": 1, "text": "我在想", "cum": 42})
        eid, typ, data = q.get_nowait()
        assert typ == "thinking"
        assert data["text"] == "我在想" and data["delta"] is False
        assert data["agent_id"] == "sub_1" and data["agent_n"] == 1
        assert data["agent_name"] == "马洛" and data["cum"] == 42
        assert at.blocks[-1] == {"type": "thinking", "text": "我在想",
                                 "agent_id": "sub_1", "agent_n": 1,
                                 "agent_name": "马洛"}
        # 学习表无人名：照发（agent_name 缺席，不炸）
        await ENGINE._consume(sid, tid, at, {
            "type": "subagent_thinking", "n": 3, "text": "想", "cum": 1})
        _, _, data2 = q.get_nowait()
        assert data2["agent_n"] == 3 and "agent_name" not in data2
        # 坏 n / 空文本：静默跳过
        await ENGINE._consume(sid, tid, at, {
            "type": "subagent_thinking", "n": "x", "text": "?", "cum": 1})
        await ENGINE._consume(sid, tid, at, {
            "type": "subagent_thinking", "n": 4, "text": "  ", "cum": 1})
        assert q.empty()
    finally:
        ENGINE._subs.pop(sid, None)
