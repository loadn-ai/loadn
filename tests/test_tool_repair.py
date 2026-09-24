"""P1-2 tool-call-repair 验收。

- 纯函数：fenced/行内双语法、standalone 语义（混排不修）、未知工具不修、
  非 JSON/非对象不修、多块序列、strip 残文
- loop 集成（fake provider 纯文本工具调用）：修复后工具真的执行；
  修复出的调用**照样走权限引擎**（deny 拒绝路径）；代码块内同形文本
  不误修复；transcript 记 tool_call_repaired；env 关闭生效
"""
from __future__ import annotations

import json

import pytest

import tests.helpers as H
from loadn.core import tool_repair as tr
from loadn.core.loop import AgentCore, LoopSettings
from loadn.core.session import SessionManager
from loadn.providers import Chunk
from loadn.providers.fake import _fake_model, _stop_chunk

pytestmark = [pytest.mark.coverage("engine.repair")]


# ---------------------------------------------------------------- 纯函数
def test_fenced_and_inline_syntaxes():
    f = tr.parse_standalone_blocks('```tool Echo\n{"msg": "hi"}\n```')
    assert f and f[0].name == "Echo" and f[0].args == {"msg": "hi"}
    assert f[0].syntax == "fenced"
    i = tr.parse_standalone_blocks('`Echo{"msg": 1}`')
    assert i and i[0].syntax == "inline" and i[0].args == {"msg": 1}
    # 冒号变体 + 多块序列
    m = tr.parse_standalone_blocks(
        '```tool: A\n{"x": 1}\n```\n\n`B{"y": 2}`')
    assert m and [c.name for c in m] == ["A", "B"]


def test_standalone_semantics_protection():
    """混排正文/普通代码块/引用 → 不修（protection 由语义承载）。"""
    assert tr.parse_standalone_blocks("看这个：`Echo{}`") is None
    assert tr.parse_standalone_blocks("```python\nEcho{}\n```") is None
    assert tr.parse_standalone_blocks("> `Echo{}`") is None
    assert tr.parse_standalone_blocks("") is None
    assert tr.parse_standalone_blocks("普通正文没有调用") is None


def test_known_tools_gate_and_bad_json():
    assert tr.parse_standalone_blocks('`Nope{}`', {"Echo"}) is None
    assert tr.parse_standalone_blocks('`Echo{}`', {"Echo"}) is not None
    assert tr.parse_standalone_blocks('`Echo{bad}`') is None
    # 行内语法体不含嵌套大括号（保守——嵌套场景走 fenced）
    assert tr.parse_standalone_blocks('`Echo{"x": "v"}`') is not None
    assert tr.parse_standalone_blocks('`Echo[1,2]`') is None   # 非对象


def test_strip_residual():
    blocks = tr.parse_standalone_blocks(
        '```tool A\n{"x": 1}\n```\n\n`B{"y": 2}`')
    assert tr.strip_blocks('```tool A\n{"x": 1}\n```\n\n`B{"y": 2}`',
                           blocks) == ""


# ---------------------------------------------------------------- loop 集成
def _repair_round(text: str) -> list[Chunk]:
    """纯文本工具调用形态的 assistant 轮（廉价网关故障形态）。"""
    return [Chunk(kind="text_delta", text=text),
            _stop_chunk({"input_tokens": 100, "output_tokens": 30},
                        "end_turn", _fake_model())]


def _core(tmp_path, rounds, deny: list[str] | None = None):
    from loadn.core.permissions import PermissionEngine
    provider = H.ScriptedProvider(rounds)
    session = SessionManager.create(tmp_path, home=tmp_path)
    core = AgentCore(provider=provider, tools={"Echo": H.EchoTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=10),
                     permissions=PermissionEngine(mode="default", deny=deny or []))
    return core, session


async def test_loop_repairs_and_executes(tmp_path):
    core, session = _core(tmp_path, [
        _repair_round('```tool Echo\n{"msg": "修好了"}\n```'),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 50, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    summary = await core.run_turn("干活")
    # 工具真的执行了（EchoTool 回写 msg 进工具结果；修复轮后还有收尾轮）
    txt = json.dumps([b.to_dict() for b in session.messages_for_turn()],
                     ensure_ascii=False)
    assert "修好了" in txt
    # transcript 记修复事件
    evs = [e for e in session.transcript.read_events()
           if e.get("type") == "tool_call_repaired"]
    assert evs
    assert evs[0]["payload"]["calls"][0]["name"] == "Echo"


async def test_repaired_calls_go_through_permissions(tmp_path):
    """修复出的调用照样走权限引擎：deny 规则拒绝它（不得绕过）。"""
    core, session = _core(tmp_path, [
        _repair_round('`Echo{"msg": "不应执行"}`'),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 50, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ], deny=["Echo"])
    await core.run_turn("干活")
    txt = json.dumps([b.to_dict() for b in session.messages_for_turn()],
                     ensure_ascii=False)
    # 权限引擎拦下提升出的调用：结果块=拒绝+is_error，工具本体未执行
    # （args 出现在 tool_use 块属正常——调用被提出后被拒）
    assert "权限拒绝" in txt and '"is_error": true' in txt
    assert "echo:" not in txt                    # EchoTool 的输出前缀未出现


async def test_code_fence_same_shape_not_repaired(tmp_path):
    """代码块内的同形文本不误修复（普通 fenced 代码 → 不识别为调用）。"""
    core, session = _core(tmp_path, [
        _repair_round("```python\nEcho({\"msg\": 1})\n```"),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 50, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    await core.run_turn("干活")
    evs = [e for e in session.transcript.read_events()
           if e.get("type") == "tool_call_repaired"]
    assert not evs


async def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_TOOL_REPAIR", "0")
    core, session = _core(tmp_path, [
        _repair_round('`Echo{"msg": "x"}`'),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 50, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    await core.run_turn("干活")
    evs = [e for e in session.transcript.read_events()
           if e.get("type") == "tool_call_repaired"]
    assert not evs


async def test_real_tool_use_not_touched(tmp_path):
    """已有结构化 tool_use 的轮不进修复路径（对照）。"""
    core, session = _core(tmp_path, [
        H.tool_round("tu_1", "Echo", {"msg": "原生"}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 50, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    await core.run_turn("干活")
    evs = [e for e in session.transcript.read_events()
           if e.get("type") == "tool_call_repaired"]
    assert not evs
    txt = json.dumps([b.to_dict() for b in session.messages_for_turn()],
                     ensure_ascii=False)
    assert "原生" in txt
