"""TUI（textual）单测：渲染助手纯函数 + App pilot 全链（fake provider 零 token）。

textual 属可选 extra——测试文件在 import textual 缺席时整组 skip
（pyproject dev extras 不含 tui，dev 环境须 pip install textual 才跑此处）。
"""
from __future__ import annotations

import asyncio

import pytest

from loadn.tui import result_excerpt, tool_line, usage_line

textual = pytest.importorskip("textual")  # noqa: F841 —— extras 缺席整组跳过


# ---------------- 渲染助手（含否定/边界） ----------------

def test_tool_line_probe_and_truncation():
    assert tool_line("Bash", {"command": "ls -la"}) == "⏺ Bash(ls -la)"
    assert tool_line("Bash", {"command": "x" * 300}).count("x") == 100  # 截 100
    assert tool_line("WebSearch", {}) == "⏺ WebSearch()"  # 无参不崩
    assert tool_line("Task", {"n": 0}) == "⏺ Task()"       # 0 值不算 probe


def test_result_excerpt_shapes():
    assert result_excerpt("a\nb\n  c", False) == "  ⠿ a b c"   # 压空白
    assert result_excerpt("boom", True).startswith("  ✗")     # 错误形态可区分
    assert result_excerpt("", False) == "  ⠿（空）"
    assert result_excerpt("y" * 500, False).endswith(" …")    # 截断标记
    assert len(result_excerpt("z" * 500, False)) < 400        # 真截了


def test_usage_line_missing_and_present():
    assert usage_line(None, 42) == "累计 42 tok"
    line = usage_line({"input_tokens": 10, "output_tokens": 5,
                       "cache_read_input_tokens": 7}, 22)
    assert "in 10" in line and "out 5" in line and "cache 7" in line


# ---------------- App pilot（fake provider 全链） ----------------

async def test_tui_slash_battery_and_guards(tmp_path, monkeypatch):
    """>>> 守卫对赌：指令分发逐个走通/未知拒绝/无 sid 的 /resume 不崩/
    中断只对在跑轮生效/失败轮渲染错误行（杀 cmd==/中断守卫/失败分支变异）。"""
    ws = tmp_path / "ws"
    (ws / ".fake").mkdir(parents=True)
    (ws / ".fake" / "reply").write_text("ok")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOADN_FAKE_DIR", str(ws / ".fake"))
    from loadn.core.build import build_agent
    from loadn.tui import LoadnTUI

    bundle = await build_agent(ws, cfg={"provider": "fake"})

    class _Req:
        requested = False

    app = LoadnTUI(bundle, _Req())
    recorded: list[str] = []
    async with app.run_test(size=(100, 40)) as pilot:
        log = app.query_one("#log")
        orig = log.write

        def rec(x, **kw):
            recorded.append(str(x))
            orig(x, **kw)

        log.write = rec  # type: ignore[method-assign]
        await app._slash("/help")
        assert any("/resume" in r for r in recorded)          # /help 真分发
        await app._slash("/todos")                             # 空 todos 不崩
        recorded.clear()
        await app._slash("/resume")                            # 无 sid：守卫不崩
        await app._slash("/nope")
        assert any("未知指令" in r for r in recorded)          # 未知拒绝有回显
        # 中断守卫：在跑 → requested 置位；无轮 → 不置位
        busy = asyncio.create_task(asyncio.sleep(60))
        app._turn = busy
        app.action_interrupt()
        assert app.stop.requested is True
        busy.cancel()
        app._turn = None
        app.stop.requested = False
        app.action_interrupt()
        assert app.stop.requested is False                     # 无轮不误置
        # 失败轮渲染：fail 控制文件 → summary 非 success → 错误行入流水
        (ws / ".fake" / "fail").write_text("injected-fail")
        recorded.clear()
        await app._run_turn("触发失败")
        await pilot.pause(0.2)
        for _ in range(100):
            if app._turn is not None and app._turn.done():
                break
            await pilot.pause(0.05)
        assert app._turn.done()
        assert any("（" in r for r in recorded)                # 错误形态行渲染
        assert "（轮异常" not in "".join(recorded)              # 是失败不是崩溃

async def test_tui_turn_roundtrip(tmp_path, monkeypatch):
    """mount → 提交一条消息 → run_turn 全链（fake reply）→ 状态条累计。
    渲染面经 _render_event 录制断言（RichLog 条带内容不可移植）。"""
    ws = tmp_path / "ws"
    (ws / ".fake").mkdir(parents=True)
    (ws / ".fake" / "reply").write_text("TUI 回复内容")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOADN_FAKE_DIR", str(ws / ".fake"))
    from loadn.core.build import build_agent
    from loadn.tui import LoadnTUI

    bundle = await build_agent(ws, cfg={"provider": "fake"})

    class _Req:
        requested = False

    app = LoadnTUI(bundle, _Req())
    seen: list[str] = []
    orig = app._render_event

    def spy(ev: dict) -> None:
        seen.append(str(ev.get("type")))
        orig(ev)

    app._render_event = spy  # type: ignore[method-assign]
    async with app.run_test(size=(100, 40)) as pilot:
        inp = app.query_one("#prompt")
        inp.focus()
        inp.value = "你好"
        await pilot.press("enter")
        for _ in range(200):                 # 10s 预算等轮完成
            if app._turn is not None and app._turn.done():
                break
            await pilot.pause(0.05)
        assert app._turn is not None and app._turn.done()
        summary = app._turn.result()
        assert summary.subtype == "success"
        assert "assistant" in seen           # emit 回调真被路由到渲染
        assert app._total_tokens > 0          # 状态条累计真动了


async def test_tui_busy_guard_and_slash(tmp_path, monkeypatch):
    """>>> 守卫对赌：轮在跑时再提交被拒（不发第二次 run_turn）；未知指令有回显。"""
    ws = tmp_path / "ws"
    (ws / ".fake").mkdir(parents=True)
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    from loadn.core.build import build_agent
    from loadn.tui import LoadnTUI

    bundle = await build_agent(ws, cfg={"provider": "fake"})

    class _Req:
        requested = False

    app = LoadnTUI(bundle, _Req())
    async with app.run_test(size=(100, 40)) as pilot:
        busy = asyncio.create_task(asyncio.sleep(60))        # 假装在跑
        app._turn = busy
        await app._run_turn("第二条")
        assert app._turn is busy               # 被拒：未创建新任务
        assert not busy.done()
        await app._slash("/nope")
        log = app.query_one("#log")
        assert log.lines                       # 未知指令有回显
        busy.cancel()
