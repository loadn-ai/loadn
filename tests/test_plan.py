"""TaskPlanner（v0.2 并行拆分调度）单测 + loop 集成。"""
from __future__ import annotations

import json

import tests.helpers as H
from hahaness.core.loop import AgentCore, LoopSettings
from hahaness.core.plan import TaskPlanner, _extract_json
from hahaness.core.session import SessionManager
from hahaness.core.subagent import SubagentManager
from hahaness.providers import Chunk


def _plan_round(obj: dict) -> list[Chunk]:
    """planner 调用的回放轮：text 输出 JSON。"""
    return [Chunk(kind="text_delta", text=json.dumps(obj, ensure_ascii=False)),
            Chunk(kind="stop", usage={"output_tokens": 50})]


# ---------------------------------------------------------------- 解析与护栏
async def test_plan_parses_parallelizable():
    prov = H.ScriptedProvider([_plan_round({
        "parallelizable": True,
        "subtasks": [{"prompt": "查 A", "type": "explore"},
                     {"prompt": "查 B", "type": "general"}],
        "reason": "两个独立调研"})])
    plan = await TaskPlanner(prov).plan("同时调研 A 和 B")
    assert plan.parallelizable and len(plan.subtasks) == 2
    assert plan.subtasks[0].subagent_type == "explore"
    assert plan.subtasks[1].prompt == "查 B"


async def test_plan_serial_fallbacks():
    # 模型说不可拆 / 输出垃圾 JSON / 子任务不足 —— 全部降级 serial
    p1 = await TaskPlanner(H.ScriptedProvider(
        [_plan_round({"parallelizable": False, "reason": "单链流程"})])).plan("编译内核")
    assert not p1.parallelizable
    p2 = await TaskPlanner(H.ScriptedProvider(
        [[Chunk(kind="text_delta", text="我觉得不用拆"),
          Chunk(kind="stop")]])).plan("x")
    assert not p2.parallelizable
    p3 = await TaskPlanner(H.ScriptedProvider(
        [_plan_round({"parallelizable": True,
                      "subtasks": [{"prompt": "唯一子任务"}]})])).plan("x")
    assert not p3.parallelizable   # <2 个子任务无并行意义


async def test_plan_caps_subtasks_and_types():
    prov = H.ScriptedProvider([_plan_round({
        "parallelizable": True,
        "subtasks": [{"prompt": f"任务{i}", "type": "bogus"} for i in range(6)],
    })])
    plan = await TaskPlanner(prov, max_subtasks=3).plan("x")
    assert len(plan.subtasks) == 3                 # 上限截断
    assert all(st.subagent_type == "general" for st in plan.subtasks)  # 未知型归 general


async def test_plan_provider_error_degrades():
    prov = H.ScriptedProvider([[Chunk(kind="error", error="down")]])
    plan = await TaskPlanner(prov).plan("x")
    assert not plan.parallelizable


def test_extract_json_tolerates_fences():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json('前置说明 {"a": {"b": 2}} 后缀') == {"a": {"b": 2}}
    assert _extract_json("完全不是 JSON") is None


# ---------------------------------------------------------------- loop 集成
class _Recorder:
    """记录 gather 调用的假 SubagentManager。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    async def gather(self, subtasks):
        self.calls.append([st.prompt for st in subtasks])
        return list(self.results)


async def test_loop_parallel_fanout_then_converge(tmp_path):
    """planner 判可拆 → gather 扇出 → 子结果注入主循环收敛出最终答案。"""
    planner_prov = H.ScriptedProvider([_plan_round({
        "parallelizable": True,
        "subtasks": [{"prompt": "子任务甲"}, {"prompt": "子任务乙"}]})])
    # 主循环 provider：第一轮（收敛轮）直接给最终答案
    main_prov = H.ScriptedProvider([H.text_round("整合完成：A=1，B=2")])
    session = SessionManager.create(tmp_path, home=tmp_path)
    rec = _Recorder(["甲结果：A=1", "乙结果：B=2"])
    core = AgentCore(provider=main_prov, tools={"Echo": H.EchoTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5),
                     subagents=rec,
                     planner=TaskPlanner(planner_prov))
    events = []
    summary = await core.run_turn("查 A 和 B", emit=events.append)
    assert summary.subtype == "success" and "整合完成" in summary.text
    # gather 收到两个子任务
    assert rec.calls == [["子任务甲", "子任务乙"]]
    # 收敛上下文里有子结果
    assert any("甲结果：A=1" in (m.text_parts() or "") for m in main_prov.calls[0])
    # plan 事件对外发射（SSE 可见拆分决策）
    assert any(e.get("type") == "plan" for e in events)


async def test_loop_serial_when_planner_declines(tmp_path):
    """planner 判不可拆 → 完全走原有循环，gather 不被调用。"""
    planner_prov = H.ScriptedProvider(
        [_plan_round({"parallelizable": False, "reason": "单链"})])
    main_prov = H.ScriptedProvider([H.text_round("直跑完成")])
    session = SessionManager.create(tmp_path, home=tmp_path)
    rec = _Recorder(["不该被调用"])
    core = AgentCore(provider=main_prov, tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5), subagents=rec,
                     planner=TaskPlanner(planner_prov))
    summary = await core.run_turn("单一任务")
    assert summary.subtype == "success" and rec.calls == []


async def test_loop_no_plan_flag_disables(tmp_path):
    """settings.no_plan=True → planner 存在也不评估（对照实验开关）。"""
    planner_prov = H.ScriptedProvider([])   # 不该被消费
    main_prov = H.ScriptedProvider([H.text_round("ok")])
    session = SessionManager.create(tmp_path, home=tmp_path)
    rec = _Recorder(["x"])
    core = AgentCore(provider=main_prov, tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5, no_plan=True),
                     subagents=rec, planner=TaskPlanner(planner_prov))
    summary = await core.run_turn("随便")
    assert summary.subtype == "success"
    assert planner_prov.i == 0 and rec.calls == []


async def test_gather_real_subagents(tmp_path):
    """SubagentManager.gather 真扇出：两个子代理各自产出。"""
    # planner 需要真 SubagentManager（registry 面）
    class _R:
        def get(self, name):
            return None

    def factory():
        return H.ScriptedProvider([H.text_round("子代理产出")])
    mgr = SubagentManager(registry=_R(), provider_factory=factory,
                          cwd=tmp_path, session_id="gather-test", home=tmp_path)
    from hahaness.core.plan import SubTask
    out = await mgr.gather([SubTask(prompt="子1"), SubTask(prompt="子2")])
    assert out == ["子代理产出", "子代理产出"]


def test_prompt_has_background_discipline():
    """v0.2 后台纪律进了 system 提示。"""
    from hahaness.core.context import CORE_PROMPT, TOOL_NOTES
    assert "长命令必后台" in CORE_PROMPT
    assert "run_in_background" in TOOL_NOTES["Bash"]


async def test_plan_decision_persisted(tmp_path):
    """判定可观测：拆与不拆都落 transcript（system/plan 事件）+ plan 对外事件。"""
    planner_prov = H.ScriptedProvider(
        [_plan_round({"parallelizable": False, "reason": "单链"})])
    main_prov = H.ScriptedProvider([H.text_round("ok")])
    session = SessionManager.create(tmp_path, home=tmp_path)
    core = AgentCore(provider=main_prov, tools={}, session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5), subagents=_Recorder([]),
                     planner=TaskPlanner(planner_prov))
    events = []
    await core.run_turn("x", emit=events.append)
    # plan 事件发射（serial 也发）
    pe = [e for e in events if e.get("type") == "plan"]
    assert pe and pe[0]["plan"]["parallelizable"] is False
    # transcript 落了 system/plan 事件
    import json as _json
    types = [e["type"] for e in session.transcript.read_events()]
    assert "system" in types


def test_planner_provider_temperature():
    """build 的 planner 专用 provider 注入 temperature=0。"""
    import inspect
    from hahaness.core import build
    src = inspect.getsource(build)
    assert '"temperature": 0' in src
