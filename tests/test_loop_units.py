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
