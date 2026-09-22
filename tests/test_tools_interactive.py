"""InteractiveShell 测试（真 pty、零 token）：脚本化交互/超时语义/会话治理。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from loadn.supervisor.process import ProcessSupervisor
from loadn.tools.base import ToolContext, ToolError
from loadn.tools.interactive import InteractiveShellTool


@pytest.fixture
async def env(tmp_path: Path):
    sup = ProcessSupervisor()
    ctx = ToolContext(cwd=tmp_path, supervisor=sup)
    yield tool_ctx(sup, ctx)
    for sh in list(ctx.extras.get("shells", {}).values()):
        try:
            from loadn.tools.interactive import _kill_shell
            _kill_shell(sh, ctx)
        except Exception:
            pass
    ctx.extras.get("shells", {}).clear()
    await sup.shutdown()


class tool_ctx:
    def __init__(self, sup, ctx):
        self.sup = sup
        self.ctx = ctx


async def _wait_pid_gone(pid: int, timeout_s: float = 3.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        await asyncio.sleep(0.05)
    return not Path(f"/proc/{pid}").exists()


async def test_create_and_expect_match(env):
    tool = InteractiveShellTool()
    out = await tool.execute({"command": "bash"}, env.ctx)
    assert "[sh1]" in out and "pid=" in out
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "echo marker-$((6*7))", "expect": "marker-42"},
    ]}, env.ctx)
    assert "marker-42" in out
    assert "expect 超时" not in out


async def test_multi_steps_single_call(env):
    """一次调用多轮交互（play-zork 死法的解药）：全部 transcript 带回。"""
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "echo r1", "expect": "r1"},
        {"send": "echo r2", "expect": "r2"},
        {"send": "echo r3", "expect": "r3"},
    ]}, env.ctx)
    for r in ("r1", "r2", "r3"):
        assert r in out
    assert "仍存活" in out


async def test_expect_timeout_not_error_session_survives(env):
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "sleep 5", "expect": "NEVER", "timeout_s": 0.8},
    ]}, env.ctx)
    assert "expect 超时" in out and "会话保留" in out
    # 会话确实可续：下一步正常命中
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "echo alive", "expect": "alive"},
    ]}, env.ctx)
    assert "alive" in out


async def test_no_expect_fixed_wait(env):
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "echo plain"},
    ]}, env.ctx)
    assert "plain" in out and "expect 超时" not in out


async def test_ctrl_c_sends_raw(env):
    """裸 \x03 字节不补换行：cat 会话被 Ctrl-C 打断后 EOF。"""
    tool = InteractiveShellTool()
    await tool.execute({"command": "cat"}, env.ctx)
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "hello-line", "expect": "hello-line"},
        {"send": "\x03", "expect": "NEVER", "timeout_s": 1.5},   # 真控制字节
    ]}, env.ctx)
    assert "hello-line" in out
    assert "会话已结束" in out   # cat 被 SIGINT 终止 → EOF 路径覆盖


async def test_capture_only(env):
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    await tool.execute({"session": "sh1", "steps": [
        {"send": "echo cap-me", "expect": "cap-me"}]}, env.ctx)
    out = await tool.execute({"session": "sh1", "capture_only": True}, env.ctx)
    assert "cap-me" in out and "当前输出尾部" in out


async def test_kill_removes_session_and_process(env):
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    pid = next(iter(env.ctx.extras["shells"].values())).popen.pid
    out = await tool.execute({"session": "sh1", "kill": True}, env.ctx)
    assert "已终止" in out
    assert env.ctx.extras["shells"] == {}
    assert await _wait_pid_gone(pid)


async def test_session_limit(env):
    tool = InteractiveShellTool()
    for _ in range(4):
        await tool.execute({"command": "bash"}, env.ctx)
    with pytest.raises(ToolError) as ei:
        await tool.execute({"command": "bash"}, env.ctx)
    assert "上限" in str(ei.value)


async def test_steps_limit(env):
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    with pytest.raises(ToolError) as ei:
        await tool.execute({"session": "sh1", "steps": [
            {"send": "true"} for _ in range(41)]}, env.ctx)
    assert "超上限" in str(ei.value)


async def test_short_program_eof_marked(env):
    """短命程序退出 → eof + supervisor 落态（track 无 reader 的出口）。"""
    tool = InteractiveShellTool()
    await tool.execute({"command": "echo bye"}, env.ctx)
    sh = env.ctx.extras["shells"]["sh1"]
    # 初始排空即应看到 bye；随后 poll 到退出
    for _ in range(40):
        if sh.eof:
            break
        await asyncio.sleep(0.1)
    assert sh.eof
    assert "bye" in sh.buffer.decode("utf-8", errors="replace")
    if sh.task_id:
        assert env.sup.poll(sh.task_id)["status"] == "done"


async def test_big_output_clipped_still_expect_tail(env, monkeypatch):
    """64k 滚动裁剪后仍能 expect 到尾部行（重叠窗防跨点漏配）。"""
    from loadn.constants import SHELL_BUFFER_MAX
    tool = InteractiveShellTool()
    await tool.execute({"command": "bash"}, env.ctx)
    out = await tool.execute({"session": "sh1", "steps": [
        {"send": "seq 1 30000", "expect": "29999"},
    ]}, env.ctx)
    assert "29999" in out
    sh = env.ctx.extras["shells"]["sh1"]
    assert len(sh.buffer) <= SHELL_BUFFER_MAX + 65536   # 滚动裁剪生效


async def test_unknown_session_rejected(env):
    tool = InteractiveShellTool()
    with pytest.raises(ToolError) as ei:
        await tool.execute({"session": "nope", "steps": [{"send": "x"}]}, env.ctx)
    assert "会话不存在" in str(ei.value)
