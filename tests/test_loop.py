"""LoopController 全分支（零 token，ScriptedProvider 驱动）。"""
from __future__ import annotations

import json

import tests.helpers as H
from hahaness.core.loop import AgentCore, LoopSettings, StopFlag
from hahaness.core.session import SessionManager
from hahaness.providers import Chunk
from hahaness.types import ToolResultBlock


async def _core(tmp_path, rounds, tools=None, settings=None, provider=None):
    session = SessionManager.create(tmp_path)
    core = AgentCore(
        provider=provider or H.ScriptedProvider(rounds),
        tools=tools if tools is not None else {"Echo": H.EchoTool(), "Boom": H.BoomTool()},
        session=session, cwd=tmp_path,
        settings=settings or LoopSettings(max_turns=10))
    return core, session


async def test_pure_text_terminates(tmp_path):
    core, session = await _core(tmp_path, [H.text_round("你好，完成")])
    events = []
    summary = await core.run_turn("做个自我介绍", emit=events.append)
    assert summary.subtype == "success" and summary.num_turns == 1
    assert summary.text == "你好，完成"
    assert summary.usage["input_tokens"] == 100
    assert summary.usage["cache_creation_input_tokens"] == 5
    assert summary.model_usage["fake"]["outputTokens"] == 20
    # 事件序列：assistant → turn
    assert [e["type"] for e in events] == ["assistant", "turn"]
    # transcript：init/user/assistant/result
    types = [e["type"] for e in session.transcript.read_events()]
    assert types == ["init", "user", "assistant", "result"]


async def test_tool_roundtrip(tmp_path):
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "hi"}),
              H.text_round("干完了")]
    core, session = await _core(tmp_path, rounds)
    events = []
    summary = await core.run_turn("调 Echo", emit=events.append)
    assert summary.subtype == "success"
    kinds = [e["type"] for e in events]
    assert kinds == ["assistant", "tool_result", "assistant", "turn"]
    tr = events[1]
    assert tr["name"] == "Echo" and not tr["block"].is_error
    assert tr["block"].content == "echo: hi"
    # transcript 有 tool_result 事件；replay 归并成 user 批消息
    types = [e["type"] for e in session.transcript.read_events()]
    assert "tool_result" in types
    msgs = session.transcript.replay_messages()
    assert msgs[0].role == "user" and msgs[0].text_parts() == "调 Echo"
    assert any(isinstance(b, ToolResultBlock) and b.tool_use_id == "tu_1"
               for m in msgs for b in m.content)


async def test_tool_error_backfilled(tmp_path):
    rounds = [H.tool_round("tu_1", "Boom", {}),
              H.text_round("已自救")]
    core, _ = await _core(tmp_path, rounds)
    events = []
    summary = await core.run_turn("调 Boom", emit=events.append)
    assert summary.subtype == "success"   # 工具错误不终结 turn
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["block"].is_error and "炸了" in tr["block"].content


async def test_unknown_tool(tmp_path):
    rounds = [H.tool_round("tu_1", "Nope", {}),
              H.text_round("换路子了")]
    core, _ = await _core(tmp_path, rounds)
    events = []
    await core.run_turn("调不存在", emit=events.append)
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["block"].is_error and "未知工具" in tr["block"].content


async def test_provider_error_turn_error(tmp_path):
    rounds = [[Chunk(kind="error", error="boom", retriable=False)]]
    core, _ = await _core(tmp_path, rounds)
    summary = await core.run_turn("跑挂")
    assert summary.subtype == "error_during_execution"
    assert "provider error" in (summary.error or "")


async def test_max_turns_gate(tmp_path):
    rounds = [H.tool_round(f"tu_{i}", "Echo", {"msg": str(i)}) for i in range(5)]
    core, _ = await _core(tmp_path, rounds,
                          settings=LoopSettings(max_turns=2))
    summary = await core.run_turn("停不下来")
    assert summary.subtype == "error_max_turns"
    assert summary.num_turns == 2


async def test_stop_flag_graceful(tmp_path):
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "x"}),
              H.text_round("ok")]
    core, _ = await _core(tmp_path, rounds)
    stop = StopFlag()
    stop.requested = True
    summary = await core.run_turn("被打断", stop=stop)
    assert summary.subtype == "error_stopped" and summary.stopped
    # result 事件仍落盘（优雅收尾）
    last = core.session.transcript.read_events()[-1]
    assert last["type"] == "result" and last["payload"]["subtype"] == "error_stopped"


async def test_loop_guard_nudge(tmp_path):
    # 同参数同结果连续 3 次 → 第 3 次后注入打断 user 消息
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "same"}),
              H.tool_round("tu_2", "Echo", {"msg": "same"}),
              H.tool_round("tu_3", "Echo", {"msg": "same"}),
              H.text_round("已汇报阻塞")]
    core, _ = await _core(tmp_path, rounds)
    summary = await core.run_turn("重复狂魔")
    assert summary.subtype == "success"
    assert core.loop_guard.nudges >= 1
    # nudge 进了 provider 收到的上下文（第 4 次 chat 的 messages 里有）
    prov = core.provider
    assert any("重复同一操作" in (m.text_parts() or "")
               for m in prov.calls[3])


async def test_permission_denied(tmp_path):
    from hahaness.core.permissions import PermissionEngine
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "x"}),
              H.text_round("改道")]
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(rounds),
                     tools={"Echo": H.EchoTool()}, session=session, cwd=tmp_path,
                     permissions=PermissionEngine(mode="default",
                                                  deny=["Echo"]),
                     settings=LoopSettings(max_turns=5))
    events = []
    await core.run_turn("调 Echo", emit=events.append)
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["block"].is_error and "权限拒绝" in tr["block"].content


async def test_result_event_usage_contract(tmp_path):
    """result 事件字段与 claude stream-json 契约对齐（usage snake/modelUsage camel）。"""
    core, session = await _core(tmp_path, [H.text_round("done")])
    await core.run_turn("x")
    res = [e for e in session.transcript.read_events() if e["type"] == "result"][0]
    p = res["payload"]
    assert set(p["usage"]) >= {"input_tokens", "output_tokens",
                               "cache_read_input_tokens",
                               "cache_creation_input_tokens"}
    mu = p["modelUsage"]["fake"]
    assert {"inputTokens", "outputTokens", "cacheReadInputTokens",
            "cacheCreationInputTokens"} <= set(mu)
    assert p["num_turns"] == 1 and "duration_ms" in p
    json.dumps(p)   # 可序列化
