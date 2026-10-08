"""M6b：loop.py 直测簇——51% 首轮存活里的真盲区（便宜直杀组）。

LoopGuard 两段式防循环（核心防护从未按阈值逐位对赌）、TurnSummary.ok、
repomap 刷新节拍、并行拆分 ≥2 门。grind 递进/保温钩子/LSP 回注门/
steer 守卫四簇场景较重，记 M6b 待续。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("engine.compact")]

import tests.helpers as H
from loadn.constants import LOOP_REMIND_AT, LOOP_REPEAT_LIMIT
from loadn.core.loop import AgentCore, LoopGuard, LoopSettings, TurnSummary
from loadn.core.session import SessionManager


# ---------------------------------------------------------------- LoopGuard
def test_loop_guard_two_stage_thresholds():
    """同指纹同结果：REMIND_AT 前静默→轻提醒→LIMIT 硬打断→重置再来。"""
    g = LoopGuard()
    levels = [g.record("Echo", {"msg": "x"}, "同结果") for _ in range(8)]
    cycle = [None] * (LOOP_REMIND_AT - 1) + ["remind"] + ["break"]
    assert [lv[0] if lv else None for lv in levels] \
        == cycle * 2 + [None, "remind"]    # 8 次覆盖两轮半周期
    # break 文案必含策略指引；remind 文案含次数
    assert "改变策略" in levels[LOOP_REPEAT_LIMIT - 1][1]
    assert f"连续 {LOOP_REMIND_AT} 次" in levels[LOOP_REMIND_AT - 1][1]


def test_loop_guard_resets_on_change():
    """同工具不同结果 / 换工具都不累计（or 反转=误伤正常重试）。"""
    g = LoopGuard()
    for i in range(10):
        lv = g.record("Echo", {"msg": "x"}, f"结果每次都不同 {i}")
        assert lv is None
    for i in range(10):
        lv = g.record(f"Tool{i}", {"msg": "x"}, "同结果")
        assert lv is None


def test_loop_guard_name_fail_streak_breaks_arg_churning():
    """同名连败×参数搅动（2026-10-09 生产实证：browser_click 逐像素递增
    50 连败，指纹每轮都变=指纹段全程重置）。同名第 LOOP_NAME_FAIL_LIMIT
    次失败硬打断，文案带工具名/最近错误/环境缺失指引；此前静默。"""
    from loadn.constants import LOOP_NAME_FAIL_LIMIT
    g = LoopGuard()
    out = [g.record("mcp__browser__browser_click", {"x": i, "y": i},
                    "not_installed: 平台 venv 缺 playwright", is_error=True)
           for i in range(LOOP_NAME_FAIL_LIMIT)]
    assert all(lv is None for lv in out[:-1])
    brk = out[-1]
    assert brk[0] == "break"
    assert "browser_click" in brk[1] and "not_installed" in brk[1]
    # 打断即重置：紧接的下一败重新计数（不再连环打断）
    assert g.record("mcp__browser__browser_click", {"x": 99, "y": 99},
                    "not_installed", is_error=True) is None


def test_loop_guard_name_streak_interleave_and_success_reset():
    """连败段两否定路径：①交错免疫——其他工具的成败不清本名计数（or 反转
    =交织重试的死人工具永远打不断）；②本名成功一次即清零（or 反转=误伤
    偶发失败后恢复的工具）。"""
    from loadn.constants import LOOP_NAME_FAIL_LIMIT
    g = LoopGuard()
    for i in range(LOOP_NAME_FAIL_LIMIT - 1):
        assert g.record("Bad", {"i": i}, "err", is_error=True) is None
        assert g.record("Other", {"i": i}, "ok") is None   # 他人成功不清
    assert g.record("Bad", {"i": 99}, "err", is_error=True)[0] == "break"

    g2 = LoopGuard()
    for i in range(LOOP_NAME_FAIL_LIMIT - 2):
        g2.record("Bad", {"i": i}, "err", is_error=True)
    g2.record("Bad", {"i": 98}, "成功")                    # 本名成功清零
    assert g2.record("Bad", {"i": 99}, "err", is_error=True) is None


def test_turn_summary_ok_gate():
    assert TurnSummary(subtype="success").ok is True
    assert TurnSummary(subtype="error_during_execution").ok is False


# ---------------------------------------------------------------- repomap
async def test_repomap_due_cadence(tmp_path, monkeypatch):
    """首 3 轮刷新仓库地图，之后停（预算>0 且轮次<3 双门）。"""
    from loadn.core import repomap as rm
    monkeypatch.setattr(rm, "budget_tokens", lambda: 100)
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider([]), tools={},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5))
    dues = [core._repomap_due() is True for _ in range(3)]   # 纯查询：不自动计数
    assert dues == [True, True, True]
    core._repomap_turns = 3                                    # run_turn 推进后
    assert core._repomap_due() is False
    monkeypatch.setattr(rm, "budget_tokens", lambda: 0)
    core2 = AgentCore(provider=H.ScriptedProvider([]), tools={},
                      session=session, cwd=tmp_path,
                      settings=LoopSettings(max_turns=5))
    assert core2._repomap_due() is False          # 大仓库预算耗尽即停


# ---------------------------------------------------------------- 并行拆分门
class _Plan:
    def __init__(self, n):
        from types import SimpleNamespace
        self.plan_out = SimpleNamespace(
            parallelizable=True, reason="",
            subtasks=[SimpleNamespace(prompt=f"子任务 {i}") for i in range(n)])

    async def plan(self, _msg):
        return self.plan_out


class _Subs:
    def __init__(self):
        self.calls = 0

    async def gather(self, subtasks, emit=None):
        self.calls += 1
        return ["r"] * len(subtasks)


async def test_parallel_gate_needs_two_subtasks(tmp_path):
    """可拆但只 1 个子任务 → 不扇出（or 反转/阈值+1=单任务也开子代理）。"""
    from tests.helpers import text_round
    rounds = [text_round("整合完成")] * 3
    session = SessionManager.create(tmp_path)
    subs = _Subs()
    core = AgentCore(provider=H.ScriptedProvider(rounds),
                     tools={"Echo": H.EchoTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=3),
                     planner=_Plan(1), subagents=subs)
    await core.run_turn("做一件事")
    assert subs.calls == 0                        # 单子任务不开扇出
    core2 = AgentCore(provider=H.ScriptedProvider(
        [text_round("a"), text_round("整合完成")]),
        tools={"Echo": H.EchoTool()},
        session=SessionManager.create(tmp_path), cwd=tmp_path,
        settings=LoopSettings(max_turns=3),
        planner=_Plan(2), subagents=subs)
    await core2.run_turn("做两件事")
    assert subs.calls == 1                        # 恰好 2 个才扇出


# ---------------------------------------------------------------- grind 递进
def _grind_core(tmp_path):
    session = SessionManager.create(tmp_path)
    return AgentCore(provider=H.ScriptedProvider([]), tools={},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5, grind=True,
                                           budget_s=600))


def _gate_summary():
    from loadn.core.loop import TurnSummary as TS
    s = TS()
    s.num_turns, s.duration_s = 8, 120.0
    return s


def test_grind_gate_escalation_and_verbal_intercept(tmp_path):
    """递进强度（1 自查/≥2 动手/≥4 证据）+ 口头交付拦截 + 缺产物拦截。"""
    core = _grind_core(tmp_path)
    core._tool_execs, core._gate_tool_execs = 1, 0     # 有新工具执行
    base = core._completion_gate("整理这份调研并汇报", ["已完成汇报"],
                                 _gate_summary())
    assert base and "逐条自查" in base                  # 第 1 次自查式
    assert core._grind_nudges == 1

    core2 = _grind_core(tmp_path)
    core2._tool_execs = core2._gate_tool_execs = 2      # 零工具=纯改口
    verbal = core2._completion_gate("任务", ["又完成一遍"], _gate_summary())
    assert verbal and "口头交付拦截" in verbal

    core3 = _grind_core(tmp_path)
    core3._grind_nudges, core3._tool_execs, core3._gate_tool_execs = 1, 3, 0
    assert "动手验证" in core3._completion_gate("t", ["d"], _gate_summary())

    core4 = _grind_core(tmp_path)
    core4._grind_nudges, core4._tool_execs, core4._gate_tool_execs = 3, 4, 0
    assert "证据清单" in core4._completion_gate("t", ["d"], _gate_summary())

    core5 = _grind_core(tmp_path)
    core5._tool_execs, core5._gate_tool_execs = 1, 0
    miss = core5._completion_gate("请写 src/不存在的交付.py",
                                  ["完成了 src/不存在的交付.py"], _gate_summary())
    assert "未通过" in miss and "src/不存在的交付.py" in miss


# ---------------------------------------------------------------- steer 守卫
async def test_poll_steer_partial_and_malformed(tmp_path):
    """半行不越过（offset 不前进）；坏 JSON/非 dict 行免疫不炸。"""
    from tests.helpers import text_round
    f = tmp_path / ".steer.jsonl"
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider([text_round("ok")]),
                     tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=3))
    core._steer_path, core._steer_offset = f, 0
    f.write_bytes(b'{"ts": 1, "text":')                 # 尾部半行
    assert core._poll_steer() == []
    assert core._steer_offset == 0                      # 未消费，下次续读
    with open(f, "ab") as fh:
        fh.write(' "转向甲"}\n[1, 2]\nnot-json\n\n'
                 '{"ts": 2, "text": "补充乙"}\n'.encode())
    assert core._poll_steer() == ["转向甲", "补充乙"]     # 坏行/非 dict 跳过
    assert core._poll_steer() == []                     # offset 已到尾


# ---------------------------------------------------------------- warmer 门
async def test_warmer_schedule_gates(tmp_path, monkeypatch):
    from loadn.core import cache_warmer as cw
    monkeypatch.setattr(cw, "warm_enabled", lambda: True)

    async def _replay(_tokens):
        return None

    def mk():
        s = SessionManager.create(tmp_path)
        return AgentCore(provider=H.ScriptedProvider([]), tools={},
                         session=s, cwd=tmp_path,
                         settings=LoopSettings(max_turns=3))
    core = mk()
    core._last_replay, core._last_input_tokens = _replay, 500
    core._warmer_schedule()
    assert core._warmer is not None
    assert core._warmer.model == "scripted"             # 真名非空串回退
    core._warmer_task.cancel()

    core2 = mk()
    core2._last_replay, core2._last_input_tokens = _replay, 0
    core2._warmer_schedule()
    assert core2._warmer is None                        # 零输入不保温

    core3 = mk()
    monkeypatch.setattr(type(core3.provider), "replayable_for_cache",
                        False, raising=False)
    core3._last_replay, core3._last_input_tokens = _replay, 500
    core3._warmer_schedule()
    assert core3._warmer is None                        # 重放键变模型不保温


def test_record_warm_usage_models_json(tmp_path):
    """保温用量按 provider 真模型名入账（or→and=models_json 键成空串）。"""
    import json as _json

    from loadn.persistence import db as db_mod
    s = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider([]), tools={},
                     session=s, cwd=tmp_path, settings=LoopSettings())
    core._record_warm_usage({"input_tokens": 7})
    with db_mod.conn(s.transcript.dir.parent.parent) as c:
        row = c.execute("SELECT models_json FROM sessions WHERE id=?",
                        (s.session_id,)).fetchone()
    assert "scripted" in _json.loads(row["models_json"])


# ---------------------------------------------------------------- titlegen 门
class _TitleProv:
    model_name = "scripted"
    replayable_for_cache = True

    def __init__(self):
        self.calls = 0

    def chat(self, _m, _t, _s, *, model=None, use_cache=False):
        self.calls += 1

        async def gen():
            from loadn.providers import Chunk
            yield Chunk(kind="text_delta", text="自动标题")

        return gen()


async def test_titlegen_gate_and_existing_title(tmp_path, monkeypatch):
    """≥6 字才生成；空标题不算已有（可生成）；短文本不触发。"""
    import asyncio

    from loadn.persistence import db as db_mod
    monkeypatch.delenv("LOADN_SESSION_ID", raising=False)
    set_calls: list[str] = []
    monkeypatch.setattr(db_mod, "set_title",
                        lambda c, sid, t: set_calls.append(t))

    def mk():
        s = SessionManager.create(tmp_path, title="")
        return AgentCore(provider=_TitleProv(), tools={}, session=s,
                         cwd=tmp_path, settings=LoopSettings()), s

    core, _ = mk()
    core._titlegen("abc")                    # 短于 6 字：不生成
    await asyncio.sleep(0.1)
    assert set_calls == []

    core2, _ = mk()
    core2._titlegen("六字标题测试")                      # 恰好 6 字（边界含）
    await asyncio.sleep(0.1)
    assert set_calls and set_calls[0] == "自动标题"


# ---------------------------------------------------------------- LSP 回注门
class _Diag(H.EchoTool):
    name = "mcp__lsp__diagnostics"
    description = "diag"

    def __init__(self):
        self.calls: list[dict] = []

    async def execute(self, args, ctx):
        self.calls.append(dict(args))
        return "1 warning"


class _WriteTool(H.EchoTool):
    name = "Write"
    description = "写"


async def test_lsp_diag_injection_gates(tmp_path):
    """写工具成功→查诊断并回注尾巴；错误结果/无 file_path/非写工具不查。"""
    fp = str(tmp_path / "x.py")
    diag = _Diag()
    rounds = [H.tool_round("t1", "Write", {"file_path": fp}),
              H.text_round("完成")]
    session = SessionManager.create(tmp_path)
    core = AgentCore(provider=H.ScriptedProvider(rounds),
                     tools={"Write": _WriteTool(), "Boom": H.BoomTool(),
                            "mcp__lsp__diagnostics": diag},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=3))
    events: list = []
    await core.run_turn("写文件", emit=events.append)
    assert diag.calls == [{"file_path": fp}]        # 成功写路径精确查询
    assert "[lsp diagnostics]" in session.transcript.path.read_text()

    # 错误结果（Boom 抛 ToolError）不得触发诊断查询
    rounds2 = [H.tool_round("t2", "Boom", {}),
               H.text_round("带病收尾")]
    core2 = AgentCore(provider=H.ScriptedProvider(rounds2),
                      tools={"Write": _WriteTool(), "Boom": H.BoomTool(),
                             "mcp__lsp__diagnostics": diag},
                      session=SessionManager.create(tmp_path), cwd=tmp_path,
                      settings=LoopSettings(max_turns=3))
    await core2.run_turn("炸一下", emit=events.append)
    assert len(diag.calls) == 1                      # 错误不查

    # 写工具**失败**（带 file_path）也不查——错误结果的诊断是噪声
    class _FailWrite(H.BoomTool):
        name = "Write"

    rounds_f = [H.tool_round("tf", "Write", {"file_path": fp}),
                H.text_round("失败收尾")]
    coref = AgentCore(provider=H.ScriptedProvider(rounds_f),
                      tools={"Write": _FailWrite(),
                             "mcp__lsp__diagnostics": diag},
                      session=SessionManager.create(tmp_path), cwd=tmp_path,
                      settings=LoopSettings(max_turns=3))
    await coref.run_turn("失败写", emit=events.append)
    assert len(diag.calls) == 1                      # 错误不查（同上）

    # 写工具成功但无 file_path（fp 空）也不查
    rounds3 = [H.tool_round("t3", "Write", {"notebook_path": fp}),
               H.text_round("完成")]
    core3 = AgentCore(provider=H.ScriptedProvider(rounds3),
                      tools={"Write": _WriteTool(),
                             "mcp__lsp__diagnostics": diag},
                      session=SessionManager.create(tmp_path), cwd=tmp_path,
                      settings=LoopSettings(max_turns=3))
    await core3.run_turn("无路径写", emit=events.append)
    assert len(diag.calls) == 1                      # fp 空不查
