"""v0.7 死磕三件套单测：完工自检关卡 / 策略轮换注入 / 反思检查点。"""
from __future__ import annotations

import tests.helpers as H
from hahaness.core.loop import AgentCore, LoopSettings
from hahaness.core.session import SessionManager


def _core(tmp_path, rounds, **settings_kw):
    provider = H.ScriptedProvider(rounds)
    session = SessionManager.create(tmp_path, home=tmp_path)
    core = AgentCore(provider=provider, tools={"Echo": H.EchoTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=30, **settings_kw))
    return core, session


def _events(session, needle: str) -> int:
    n = 0
    for ev in session.transcript.read_events():
        content = str(ev.get("content") or "")
        if needle in content or needle in str(ev):
            n += 1
    return n


async def test_grind_gate_nudges_on_missing_artifact(tmp_path):
    """死磕模式：宣称完成但产物缺失 → 注入续战提示，最多 GRIND_MAX_NUDGES 次。"""
    claim = "完成：已生成 /app/out.step（8 轮工具铺垫后宣称）"
    rounds = [H.tool_round(f"tu_{i}", "Echo", {"msg": str(i)}) for i in range(7)]
    rounds += [H.text_round(claim)] * 5      # 每次自检不过 → 再宣称
    core, session = _core(tmp_path, rounds, grind=True)
    summary = await core.run_turn("生成 /app/out.step 后结束")
    assert summary.subtype == "success"
    # v0.7.1：8 次续战注入（上限 8）
    assert _events(session, "完工自检") == 8  # v0.7.1：上限 3→8


async def test_grind_off_by_default(tmp_path):
    """默认不开 grind：纯文本直接收工，零自检事件（聊天体验不受影响）。"""
    rounds = [H.tool_round("tu_1", "Echo", {}), H.text_round("答完了 /app/x.py")]
    core, session = _core(tmp_path, rounds)
    summary = await core.run_turn("做 /app/x.py")
    assert summary.subtype == "success"
    assert _events(session, "完工自检") == 0


async def test_grind_gate_passes_when_artifact_exists(tmp_path):
    """产物真实存在：走「逐条自查」软关卡（仍注入一次自检确认）。"""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.py").write_text("print(1)")
    rounds = [H.tool_round(f"tu_{i}", "Echo", {}) for i in range(7)]
    rounds += [H.text_round("done，产出 /app/x.py")] * 4
    core, session = _core(tmp_path, rounds, grind=True)
    summary = await core.run_turn("写 /app/x.py")
    # 产物真实存在（cwd/app/x.py 命中相对映射）→ 只走软自查，绝不误报缺失
    assert summary.subtype == "success"
    assert _events(session, "完工自检未通过") == 0
    assert _events(session, "完工自检") >= 1


async def test_reflection_checkpoint_fires(tmp_path):
    """反思检查点：每 reflect_every 轮注入总结提示。"""
    rounds = [H.tool_round(f"tu_{i}", "Echo", {}) for i in range(5)]
    rounds += [H.text_round("ok")]
    core, session = _core(tmp_path, rounds, reflect_every=2)
    await core.run_turn("干活")
    assert _events(session, "反思检查点") >= 2   # 第 2、4 轮各一次


async def test_strategy_rotation_on_loop_break(tmp_path):
    """LoopGuard 硬打断 → 注入换思路清单。"""
    same = H.tool_round("tu_x", "Echo", {"msg": "same"})
    rounds = [same] * 8 + [H.text_round("fin")]
    core, session = _core(tmp_path, rounds)
    await core.run_turn("循环任务")
    assert _events(session, "换思路清单") >= 1
