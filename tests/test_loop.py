"""LoopController 全分支（零 token，ScriptedProvider 驱动）。"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("engine.compact")]

import json

import tests.helpers as H
from loadn.core.loop import AgentCore, LoopSettings, StopFlag
from loadn.core.session import SessionManager
from loadn.providers import Chunk
from loadn.types import ToolResultBlock


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


async def test_stream_interrupted_retries(tmp_path, monkeypatch):
    """断流（已产出内容后连接断开）→ loop 层整轮重试，最终 success。"""
    monkeypatch.setattr("loadn.core.loop.backoff_delay", lambda n: 0.0)
    prov = H.FlakyStreamProvider(fail_n=1)
    core, session = await _core(tmp_path, [], provider=prov)
    summary = await core.run_turn("断流场景")
    assert summary.subtype == "success"
    assert summary.text == "重试后的完整回复"
    assert prov.attempts == 2
    # 半成品没有入上下文/transcript：恰一条 assistant
    assistants = [e for e in session.transcript.read_events()
                  if e["type"] == "assistant"]
    assert len(assistants) == 1


async def test_stream_interrupted_exhausts_to_error(tmp_path, monkeypatch):
    """重试耗尽 → error_during_execution（不再落假 success / 带 traceback 崩）。"""
    monkeypatch.setattr("loadn.core.loop.backoff_delay", lambda n: 0.0)
    prov = H.FlakyStreamProvider(fail_n=99)
    core, session = await _core(tmp_path, [], provider=prov)
    summary = await core.run_turn("一直断流")
    assert summary.subtype == "error_during_execution"
    assert "stream interrupted" in (summary.error or "")
    last = session.transcript.read_events()[-1]
    assert last["type"] == "result"
    assert last["payload"]["subtype"] == "error_during_execution"
    assert prov.attempts == 3   # 1 次原始 + STREAM_RETRY_MAX=2 次重试


async def test_retriable_provider_error_retried(tmp_path, monkeypatch):
    """retriable error chunk（429/529/5xx 形态）→ loop 层重试后恢复。"""
    monkeypatch.setattr("loadn.core.loop.backoff_delay", lambda n: 0.0)
    prov = H.RetriableErrorProvider(fail_n=1)
    core, _ = await _core(tmp_path, [], provider=prov)
    summary = await core.run_turn("限流场景")
    assert summary.subtype == "success"
    assert summary.text == "retriable 恢复后的回复"
    assert prov.attempts == 2


async def test_unexpected_exception_turn_error(tmp_path):
    """provider 意外异常 → catch-all 转 turn error（run_turn 正常返回）。"""
    core, session = await _core(tmp_path, [], provider=H.ExplodingProvider())
    summary = await core.run_turn("内部炸了")
    assert summary.subtype == "error_during_execution"
    assert "internal error" in (summary.error or "")
    last = session.transcript.read_events()[-1]
    assert last["type"] == "result"
    assert last["payload"]["subtype"] == "error_during_execution"


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


async def test_loop_guard_name_streak_wiring(tmp_path):
    """同名连败接线对赌（2026-10-09 生产实证形态）：参数逐次搅动的同名
    失败（指纹守卫全程重置免疫）在 LOOP_NAME_FAIL_LIMIT 处触发硬打断
    nudge。走完整 _exec_tool 链——is_error 接线变异（恒 False）在此被杀。
    """
    rounds = [H.tool_round(f"tu_{i}", "Boom", {"x": i}) for i in range(8)]
    rounds.append(H.text_round("已汇报阻塞"))
    core, _ = await _core(tmp_path, rounds, tools={"Boom": H.BoomTool()})
    summary = await core.run_turn("死工具搅动")
    assert summary.subtype == "success"
    assert core.loop_guard.nudges >= 1
    prov = core.provider
    assert any("Boom" in (m.text_parts() or "") and "连续" in (m.text_parts() or "")
               for chat in prov.calls for m in chat)


async def test_permission_denied(tmp_path):
    from loadn.core.permissions import PermissionEngine
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


async def test_truncation_nudge_recovers_empty_turn(tmp_path):
    """aimo 案回归：stop_reason=max_tokens 且零文本零工具 → 注入收敛续轮一次。"""
    from loadn.providers import Chunk
    rounds = [
        # 第一轮：只有 thinking，撞满 max_tokens（复刻实测事故形态）
        [Chunk(kind="thinking_delta", text="让我分析这道竞赛题" * 500),
         Chunk(kind="usage", usage={"input_tokens": 100}, model="fake"),
         Chunk(kind="stop", usage={"input_tokens": 100, "output_tokens": 16384},
               stop_reason="max_tokens")],
        # 续轮：收敛出最终答案
        H.text_round("最终答案：d=49"),
    ]
    core, session = await _core(tmp_path, rounds)
    summary = await core.run_turn("解这道数学题")
    assert summary.subtype == "success"
    assert summary.text == "最终答案：d=49"
    assert summary.num_turns == 2
    # 续轮的收敛提示进了上下文（第二次 chat 的 messages 里有）
    assert any("被输出长度上限截断" in (m.text_parts() or "")
               for m in core.provider.calls[1])


async def test_truncation_nudge_only_once(tmp_path):
    """连续两次截断空转：只续一次，第二次按空文本终结（不死循环）。"""
    from loadn.providers import Chunk

    def trunc():
        return [Chunk(kind="thinking_delta", text="继续想" * 10),
                Chunk(kind="stop", usage={"output_tokens": 32000},
                      stop_reason="max_tokens")]

    core, _ = await _core(tmp_path, [trunc(), trunc()])
    summary = await core.run_turn("x")
    assert summary.subtype == "success"  # 第二次截断按空文本终结，不再续
    assert core.provider.i == 2   # 恰好两次 chat：截断→续（仍截断）→终结
    assert summary.text == ""


# ---------------------------------------------------------------- grace call
async def test_max_turns_grace_call_writes_conclusion(tmp_path):
    """轮次耗尽 → 一次无工具收尾调用：text 进 result、usage 并入。"""
    rounds = [H.tool_round(f"tu_{i}", "Echo", {"msg": str(i)}) for i in range(3)]
    grace = H.text_round("收尾结论：已完成 2/3")
    core, session = await _core(tmp_path, rounds + [grace],
                                settings=LoopSettings(max_turns=3))
    summary = await core.run_turn("干活")
    assert summary.subtype == "error_max_turns"
    assert summary.text == "收尾结论：已完成 2/3"
    # grace 的 usage 并入了记账（4 次调用：3 工具轮 + 1 收尾）
    assert core.provider.i == 4
    # 收尾 assistant 事件也落了 transcript
    texts = [e for e in session.transcript.read_events()
             if e["type"] == "assistant"]
    assert any("收尾结论" in json.dumps(e["payload"], ensure_ascii=False)
               for e in texts)


async def test_max_turns_grace_call_failure_tolerated(tmp_path):
    """grace 调用失败（provider error chunk）→ 沿用旧 text，subtype 不被改写。"""
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "x"}),
              H.tool_round("tu_2", "Echo", {"msg": "y"})]
    grace_fail = [Chunk(kind="error", error="grace down", retriable=False)]
    core, _ = await _core(tmp_path, rounds + grace_fail,
                          settings=LoopSettings(max_turns=2))
    summary = await core.run_turn("干活")
    assert summary.subtype == "error_max_turns"      # 不被 catch-all 改写
    assert summary.error == "达到轮次上限 2"


async def test_max_turns_grace_skipped_when_stopped(tmp_path):
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "x"})]
    core, _ = await _core(tmp_path, rounds, settings=LoopSettings(max_turns=1))
    stop = StopFlag()
    stop.requested = True
    summary = await core.run_turn("干活", stop=stop)
    assert summary.subtype == "error_stopped"
    assert core.provider.i == 0                      # 首轮即停，无任何调用


# ---------------------------------------------------------------- 两段提醒
async def test_loop_guard_remind_then_break(tmp_path):
    """第 2 次同指纹同结果轻提醒，第 3 次硬打断（nudges 只在 break 计）。"""
    rounds = [H.tool_round(f"tu_{i}", "Echo", {"msg": "same"}) for i in range(3)]
    rounds.append(H.text_round("已换策略"))
    core, _ = await _core(tmp_path, rounds)
    summary = await core.run_turn("重复狂魔")
    assert summary.subtype == "success"
    prov = core.provider
    # 第 2 次 Echo 后注入轻提醒 → 第 3 次 chat（calls[2]）可见；
    # 第 3 次后硬打断 → 第 4 次 chat（calls[3]）可见
    assert any("同结果无进展" in (m.text_parts() or "") for m in prov.calls[2])
    assert any("重复同一操作" in (m.text_parts() or "") for m in prov.calls[3])
    assert core.loop_guard.nudges == 1


# ---------------------------------------------------------------- 残缺拒绝
async def test_truncated_tool_call_refused(tmp_path):
    """max_tokens 截断的半截 JSON 参数 → 不执行，回填 is_error 让模型重发。"""
    import json as _j
    raw_prefix = _j.dumps({"command": "echo hello"})[:10]   # 半截
    rounds = [
        [Chunk(kind="input_json_delta", tool_use_id="tu_bad", tool_name="Echo",
               partial_json=raw_prefix),
         Chunk(kind="stop", usage={"output_tokens": 32000},
               stop_reason="max_tokens")],
        H.text_round("已重发完成"),
    ]
    calls = []
    tool = H.EchoTool()
    orig = tool.execute

    async def spy(args, ctx):
        calls.append(args)
        return await orig(args, ctx)

    tool.execute = spy
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(rounds),
                     tools={"Echo": tool}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5))
    events = []
    summary = await core.run_turn("截断场景", emit=events.append)
    assert summary.subtype == "success"
    assert calls == []                                # 半截参数未被执行
    tr = next(e for e in events if e["type"] == "tool_result")
    assert tr["block"].is_error and "截断不完整" in tr["block"].content
    assert raw_prefix[:200] in tr["block"].content


async def test_bad_json_non_max_tokens_still_executes(tmp_path):
    """非截断的坏 JSON（stop_reason=tool_use）→ 走 _raw 回显老路（不回退）。"""
    rounds = [
        [Chunk(kind="input_json_delta", tool_use_id="tu_x", tool_name="Echo",
               partial_json='{"broken'),
         Chunk(kind="stop", usage={"output_tokens": 30}, stop_reason="tool_use")],
        H.text_round("自救完成"),
    ]
    calls = []

    class _Spy(H.EchoTool):
        async def execute(self, args, ctx):
            calls.append(args)
            return await H.EchoTool.execute(self, args, ctx)

    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(rounds),
                     tools={"Echo": _Spy()}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5))
    await core.run_turn("坏 JSON")
    assert len(calls) == 1                            # 执行了（_raw 自救路径）
    assert "_raw" in calls[0]


# ---------------------------------------------------------------- FailoverReason
def test_classify_error_table():
    from loadn.providers.retry import classify_error
    assert classify_error("Prompt is too long: 210000 > 200000")["reason"] \
        == "context_overflow"
    assert classify_error("400: context_length_exceeded")["compress"] is True
    assert classify_error("请求过长，超出上下文长度")["compress"] is True
    assert classify_error("Rate limit exceeded")["retry"] is True
    assert classify_error("overloaded_error")["retry"] is True
    assert classify_error("some unknown failure")["reason"] == "unknown"


async def test_overflow_triggers_compact_and_resend(tmp_path, monkeypatch):
    """溢出 error chunk → 强制压缩 → 重发成功（不计流重试额度）。"""
    from loadn.core.compactor import Compactor
    monkeypatch.setattr("loadn.core.compactor.COMPACT_KEEP_TOKENS", 10)
    overflow = [Chunk(kind="error",
                      error="400: prompt is too long (210000 tokens)",
                      retriable=False)]
    # 先攒两轮工具轮（压缩器要有轮可丢），再溢出，再成功
    prov = H.ScriptedProvider([
        H.tool_round("tu_1", "Echo", {"msg": "a"}),
        H.tool_round("tu_2", "Echo", {"msg": "b"}),
        overflow,
        H.text_round("压缩后成功")])
    session = SessionManager.create(tmp_path)
    comp = Compactor(H.ScriptedProvider([H.text_round("交接摘要")]))
    core = AgentCore(provider=prov,
                     tools={"Echo": H.EchoTool()}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5), compactor=comp)
    summary = await core.run_turn("溢出场景")
    assert summary.subtype == "success"
    assert summary.text == "压缩后成功"
    # 主 provider 4 次 chat（2 工具轮 + 溢出 + 压缩后重发），摘要 provider 一次
    assert prov.i == 4
    # compact 事件落了 transcript
    assert any(e["type"] == "compact" for e in session.transcript.read_events())


async def test_overflow_second_time_reports_error(tmp_path, monkeypatch):
    """压缩自救后再溢出（compress_used 已置位）→ 如实 error_during_execution。"""
    from loadn.core.compactor import Compactor
    monkeypatch.setattr("loadn.core.compactor.COMPACT_KEEP_TOKENS", 10)
    overflow = [Chunk(kind="error",
                      error="400: context_length_exceeded", retriable=False)]

    class _OverflowAfterTools:
        model_name = "ovf"

        def __init__(self):
            self.calls = []
            self._rounds = [H.tool_round("tu_1", "Echo", {"msg": "a"}),
                            H.tool_round("tu_2", "Echo", {"msg": "b"})]

        async def chat(self, messages, tools, system, *, stream=True,
                       model=None, use_cache=True):
            self.calls.append(list(messages))
            if self._rounds:
                for c in self._rounds.pop(0):
                    yield c
                return
            yield overflow[0]

    prov = _OverflowAfterTools()
    session = SessionManager.create(tmp_path)
    comp = Compactor(H.ScriptedProvider([H.text_round("摘要")]))
    core = AgentCore(provider=prov,
                     tools={"Echo": H.EchoTool()}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5), compactor=comp)
    summary = await core.run_turn("持续溢出")
    assert summary.subtype == "error_during_execution"
    assert "context_length_exceeded" in (summary.error or "")
    assert len(prov.calls) == 4          # 2 工具轮→溢出→压缩→重发→仍溢出→止


async def test_overflow_without_compactor_direct_error(tmp_path):
    """子代理（无 compactor）/no_compact → 直 error，不回退不炸。"""
    overflow = [Chunk(kind="error", error="400: prompt is too long",
                      retriable=False)]
    core, _ = await _core(tmp_path, [overflow])
    summary = await core.run_turn("无压缩器")
    assert summary.subtype == "error_during_execution"


# ---------------------------------------------------------------- 随时插话
async def test_steer_injected_between_rounds(tmp_path, monkeypatch):
    """运行中插话：工具轮间隙写入 steer 文件 → 下一轮 LLM 调用前注入上下文。

    文件在 turn 开始时不存在（宿主收到第一条插话才创建——回归：曾因
    「首次成功 open 才定 EOF 界」把启动后写入的插话当历史跳过）。
    """
    import os
    steer = tmp_path / "steer.jsonl"
    monkeypatch.setattr("loadn.core.loop.os.environ",
                        {**os.environ, "LOADN_STEER_FILE": str(steer)})
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "a"}),
              H.tool_round("tu_2", "Echo", {"msg": "b"}),
              H.text_round("收到转向指令，已放弃 kaggle")]
    core, session = await _core(tmp_path, rounds,
                                settings=LoopSettings(max_turns=6, no_plan=True))
    # 工具执行期间用户插话：第一次工具执行后创建文件并追加
    orig_exec = core._exec_tool

    async def slow_exec(tu, emit):
        if tu.id == "tu_1":
            steer.write_text(
                json.dumps({"ts": 1, "text": "别打 kaggle 了，改打 game jam"}) + "\n")
        return await orig_exec(tu, emit)

    core._exec_tool = slow_exec
    events = []
    summary = await core.run_turn("调研比赛", emit=events.append)
    assert summary.subtype == "success"
    # 第 2 次 chat 的 messages 已含插话（工具轮间隙注入生效）
    prov = core.provider
    assert any("别打 kaggle" in (m.text_parts() or "") and "用户插话" in m.text_parts()
               for m in prov.calls[1])
    # transcript 落了插话 user 事件
    assert any("用户插话" in json.dumps(e.get("payload") or {}, ensure_ascii=False)
               for e in session.transcript.read_events())
    # 消费回执：steer 事件带原文（宿主据此摘除待回队列条目）
    steers = [e for e in events if e["type"] == "steer"]
    assert len(steers) == 1 and "别打 kaggle" in steers[0]["text"]


async def test_steer_history_not_replayed(tmp_path, monkeypatch):
    """启动前已有的历史插话不重放（在当时的上下文里已消化）。"""
    import os
    steer = tmp_path / "steer.jsonl"
    steer.write_text(json.dumps({"ts": 0, "text": "上周的旧插话"}) + "\n")
    monkeypatch.setattr("loadn.core.loop.os.environ",
                        {**os.environ, "LOADN_STEER_FILE": str(steer)})
    rounds = [H.tool_round("tu_1", "Echo", {"msg": "a"}),
              H.text_round("done")]
    core, _ = await _core(tmp_path, rounds,
                          settings=LoopSettings(max_turns=4, no_plan=True))
    orig_exec = core._exec_tool

    async def write_exec(tu, emit):
        if tu.id == "tu_1":
            steer.write_text(steer.read_text()
                             + json.dumps({"ts": 1, "text": "新插话"}) + "\n")
        return await orig_exec(tu, emit)

    core._exec_tool = write_exec
    summary = await core.run_turn("x")
    assert summary.subtype == "success"
    texts = [m.text_parts() for m in core.provider.calls[1]]
    assert any("新插话" in t for t in texts)
    assert not any("上周的旧插话" in t for t in texts)


async def test_steer_absent_file_silent(tmp_path, monkeypatch):
    """无 steer 文件/无 env：零影响（插话是增强不是依赖）。"""
    monkeypatch.delenv("LOADN_STEER_FILE", raising=False)
    core, _ = await _core(tmp_path, [H.text_round("ok")])
    summary = await core.run_turn("x")
    assert summary.subtype == "success"
