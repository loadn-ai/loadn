"""P2-4 Runtime Task 注册表 + 通知策略验收（零 token）。

- 注册表：三类任务（bg 进程/subagent asyncio/timer）枚举含 kind+desc+age；
  已死任务惰性收割；取消传播（bg=进程组真死；asyncio=CancelledError）
- 工具：TaskList/TaskCancel 走 ctx.extras 注册表；无注册表优雅退化
- build 接线：AgentCore 建注册表→supervisor/manager 注入；TaskList 进
  工具面（fake provider E2E：agent 调 TaskList 看到后台 sleep）
- 通知策略：三档分支；免打扰窗（on_confirm 永不静默）；i18n zh/en
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loadn.core.tasks import TaskRegistry, notify_policy, render_notice, should_notify
from loadn.tools.base import ToolContext


# ---------------------------------------------------------------- 注册表
async def test_registry_three_kinds_and_cancel():
    reg = TaskRegistry()
    # bg：真子进程（sleep）——取消=进程组收割
    proc = await asyncio.create_subprocess_exec(
        "sleep", "30", start_new_session=True)
    bg_id = reg.register("bg", "sleep 30", proc=proc)
    # subagent/timer：asyncio task
    async def hang():
        await asyncio.sleep(30)
    t1, t2 = asyncio.create_task(hang()), asyncio.create_task(hang())
    sa_id = reg.register("subagent", "调研子代理", cancel_handle=t1)
    tm_id = reg.register("timer", "30 分钟后提醒", cancel_handle=t2)

    alive = reg.list_alive()
    kinds = {r["kind"] for r in alive}
    assert kinds == {"bg", "subagent", "timer"}
    assert any("调研" in r["desc"] for r in alive)

    # 取消传播
    out = await reg.cancel(bg_id)
    assert out["ok"] and out["way"] == "process-group"
    await asyncio.sleep(0.2)
    assert proc.returncode is not None            # 进程真死
    await reg.cancel(sa_id)
    await asyncio.sleep(0.05)
    assert t1.cancelled()                          # asyncio 取消传播
    # 已取消的不再枚举（惰性收割）
    ids = {r["id"] for r in reg.list_alive()}
    assert bg_id not in ids and sa_id not in ids and tm_id in ids
    t2.cancel()


async def test_registry_lazy_reap_finished():
    reg = TaskRegistry()
    async def quick():
        return 1
    t = asyncio.create_task(quick())
    tid = reg.register("timer", "秒完", cancel_handle=t)
    await t
    assert all(r["id"] != tid for r in reg.list_alive())   # 完成即收


async def test_task_tools_surface():
    from loadn.core.task_tools import TaskListTool
    ctx = ToolContext(cwd=Path("."))
    ctx.extras["task_registry"] = TaskRegistry()
    out = await TaskListTool().execute({}, ctx)
    assert "无在跑任务" in out


# ---------------------------------------------------------------- 通知策略
def test_notify_policy_branches():
    p = notify_policy()
    assert p == {"on_confirm": "immediate", "on_done": "summary",
                 "on_error": "immediate"}
    assert should_notify("on_confirm", p)
    assert not should_notify("on_done", p)        # summary=平台汇总不逐条
    assert should_notify("on_error", p)
    # 覆盖与 silent
    p2 = notify_policy({"on_error": "silent"})
    assert not should_notify("on_error", p2)
    # 非法覆盖被忽略（fail 到默认）
    assert notify_policy({"on_confirm": "whenever"})["on_confirm"] == "immediate"


def test_dnd_window_confirm_never_silent():
    import time
    p = notify_policy()
    now = time.time()
    # 免打扰窗内：error 静默、confirm 仍然即时（人审阻塞不可静默）
    assert not should_notify("on_error", p, dnd_until=now + 60, now=now)
    assert should_notify("on_confirm", p, dnd_until=now + 60, now=now)
    # 窗外恢复
    assert should_notify("on_error", p, dnd_until=now - 1, now=now)


def test_i18n_copy_layer(monkeypatch):
    assert "需要你的确认" in render_notice("on_confirm", desc="x")
    monkeypatch.setenv("LOADN_LANG", "en")
    assert "Needs your confirmation" in render_notice("on_confirm", desc="x")


# ---------------------------------------------------------------- E2E
async def test_build_wires_tools_and_bg_registration(tmp_path, monkeypatch):
    """fake provider：agent 发 Bash 后台 sleep → TaskList 枚举到它。"""
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOADN_PROVIDER", "fake")
    monkeypatch.setenv("LOADN_FAKE_DIR", str(tmp_path / ".fake"))
    (tmp_path / ".fake").mkdir()
    # 轮 1：工具调用 Bash(run_in_background)；轮 2：TaskList；轮 3 收尾
    (tmp_path / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash",
         "input": {"command": "sleep 30", "run_in_background": True}}))
    import tests.helpers as H
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    # ScriptedProvider 逐轮：tool_use(Bash bg) → tool_use(TaskList) → text
    provider = H.ScriptedProvider([
        H.tool_round("tu1", "Bash",
                     {"command": "sleep 30", "run_in_background": True}),
        H.tool_round("tu2", "TaskList", {}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 30, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    from loadn.core.task_tools import TaskListTool
    from loadn.supervisor.process import ProcessSupervisor
    from loadn.tools.bash import tool as bash_tool
    sup = ProcessSupervisor()
    core = AgentCore(provider=provider,
                     tools={"Bash": bash_tool, "TaskList": TaskListTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=6),
                     ctx=ToolContext(cwd=tmp_path, supervisor=sup))
    await core.run_turn("跑个后台任务再列出任务")
    txt = json.dumps([b.to_dict() for b in session.messages_for_turn()],
                     ensure_ascii=False)
    assert "bg" in txt and "sleep 30" in txt         # TaskList 结果里有登记
    # 收尾：清掉后台 sleep
    for t in core.task_registry.list_alive():
        await core.task_registry.cancel(t["id"])
