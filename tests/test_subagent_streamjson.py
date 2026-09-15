"""SubagentManager 与 stream-json 输出层单测。"""
from __future__ import annotations

import json

import tests.helpers as H
from hahaness.core.loop import AgentCore, LoopSettings
from hahaness.core.session import SessionManager
from hahaness.core.subagent import SUBAGENT_TYPES, SubagentManager, TaskTool


# ---------------------------------------------------------------- 子代理
class _Registry:
    """SubagentManager 需要的最小 registry 面。"""

    def __init__(self, tools):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)


async def test_task_returns_final_text_only(tmp_path):
    session = SessionManager.create(tmp_path, home=tmp_path)
    mgr = SubagentManager(
        registry=_Registry({"Echo": H.EchoTool(), "Task": "placeholder"}),
        provider_factory=lambda: H.ScriptedProvider([H.text_round("子代理结论：找到了 3 处")]),
        cwd=tmp_path, session_id=session.session_id, home=tmp_path)
    out = await mgr.task("去找东西", subagent_type="explore")
    assert out.startswith("子代理结论")
    assert "工具" not in out[:5]   # 只回传 final text，无过程日志


async def test_task_output_truncated_30k(tmp_path):
    from hahaness.constants import TASK_OUTPUT_MAX_CHARS
    session = SessionManager.create(tmp_path, home=tmp_path)
    big = "结" * (TASK_OUTPUT_MAX_CHARS + 500)
    mgr = SubagentManager(
        registry=_Registry({}),
        provider_factory=lambda: H.ScriptedProvider([H.text_round(big)]),
        cwd=tmp_path, session_id=session.session_id, home=tmp_path)
    out = await mgr.task("长输出")
    assert len(out) == TASK_OUTPUT_MAX_CHARS + len("…[截断]")
    assert out.endswith("…[截断]")


async def test_subagent_tools_have_no_task():
    """子代理类型定义的工具面一律不含 Task（禁递归）。"""
    for conf in SUBAGENT_TYPES.values():
        assert "Task" not in conf["tools"]


async def test_subagent_independent_transcript(tmp_path):
    session = SessionManager.create(tmp_path, home=tmp_path)
    mgr = SubagentManager(
        registry=_Registry({}),
        provider_factory=lambda: H.ScriptedProvider([H.text_round("ok")]),
        cwd=tmp_path, session_id=session.session_id, home=tmp_path)
    await mgr.task("子任务1")
    await mgr.task("子任务2")
    # 独立 transcript（sub1/sub2），且都在 parent 会话目录旁
    base = tmp_path / "sessions"
    sids = [p.name for p in base.iterdir() if p.is_dir()]
    assert any("sub1" in s for s in sids) and any("sub2" in s for s in sids)


async def test_task_tool_wires_manager(tmp_path):
    session = SessionManager.create(tmp_path, home=tmp_path)
    mgr = SubagentManager(
        registry=_Registry({}),
        provider_factory=lambda: H.ScriptedProvider([H.text_round("子结果")]),
        cwd=tmp_path, session_id=session.session_id, home=tmp_path)
    tool = TaskTool(mgr)
    assert tool.name == "Task"
    out = await tool.execute({"prompt": "做事", "subagent_type": "general"}, None)
    assert out == "子结果"
    import pytest

    from hahaness.tools.base import ToolError
    with pytest.raises(ToolError):
        await tool.execute({"prompt": "  "}, None)


# ---------------------------------------------------------------- stream-json
async def test_stream_json_event_contract(tmp_path, capsys):
    from hahaness.cli.stream_json import StreamJsonEmitter
    session = SessionManager.create(tmp_path, home=tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(
        [H.tool_round("tu_1", "Echo", {"msg": "hi"}), H.text_round("完成")]),
        tools={"Echo": H.EchoTool()}, session=session, cwd=tmp_path,
        settings=LoopSettings(max_turns=5))
    emitter = StreamJsonEmitter(model="fake", tools=["Echo"])
    emitter.session_id = session.session_id
    emitter.send_init(session.session_id)
    await core.run_turn("调工具", emit=emitter)
    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    types = [ln["type"] for ln in lines]
    assert types[0] == "system" and lines[0]["subtype"] == "init"
    assert lines[0]["session_id"] == session.session_id
    assert lines[0]["model"] == "fake" and "Echo" in lines[0]["tools"]
    assert types == ["system", "assistant", "user", "assistant", "result"]
    # assistant 消息形状（blocks 完整）
    a1 = lines[1]["message"]
    assert a1["role"] == "assistant" and a1["id"].startswith("msg_")
    assert any(b["type"] == "tool_use" and b["id"] == "tu_1"
               and b["input"] == {"msg": "hi"} for b in a1["content"])
    # tool_result 回填形状
    u = lines[2]["message"]["content"][0]
    assert u["type"] == "tool_result" and u["tool_use_id"] == "tu_1" \
        and u["content"] == "echo: hi" and not u["is_error"]
    # result 记账字段（fake_claude 契约）
    r = lines[-1]
    assert r["subtype"] == "success" and r["session_id"] == session.session_id
    assert set(r["usage"]) >= {"input_tokens", "output_tokens",
                               "cache_read_input_tokens",
                               "cache_creation_input_tokens"}
    assert r["modelUsage"]["fake"]["inputTokens"] == 200   # 两轮各 100
    assert r["num_turns"] == 2 and "duration_ms" in r


def test_price_table_cost():
    from hahaness.constants import cost_usd, price_key
    assert price_key("glm-5.3[1m]") == "glm-5.3"
    assert price_key("GLM-5.3-FLASH") == "glm-5.3-flash"
    assert price_key("unknown-model") == "glm-5.3"
    c = cost_usd("glm-5.3", input_t=1_000_000, output_t=1_000_000)
    assert abs(c - (1.4 + 4.4)) < 1e-9
