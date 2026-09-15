"""BashTool 行为测试：exit code / 输出截断 / 超时收割 / 后台任务 / 回调钩子。

超时用真实 SIGTERM 收割（命令写 pidfile，断言 /proc 下进程组确已消失）；
后台任务经 ProcessSupervisor 登记、输出 tee、poll/kill 全链路。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from hahaness.constants import BASH_OUTPUT_MAX
from hahaness.supervisor.process import ProcessSupervisor
from hahaness.tools.base import ToolContext, ToolError
from hahaness.tools.bash import BashTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


@pytest.fixture
async def sup():
    s = ProcessSupervisor()
    yield s
    await s.shutdown()


async def test_exit_code_nonzero_reported(ctx: ToolContext):
    out = await BashTool().execute({"command": "echo out; exit 3"}, ctx)
    assert "out" in out
    assert "Exit code 3" in out


async def test_exit_code_zero_no_suffix(ctx: ToolContext):
    out = await BashTool().execute({"command": "echo ok"}, ctx)
    assert out.strip() == "ok"
    assert "Exit code" not in out


async def test_stderr_merged_into_stdout(ctx: ToolContext):
    out = await BashTool().execute({"command": "echo to-stderr 1>&2"}, ctx)
    assert "to-stderr" in out


async def test_cwd_is_ctx_cwd(ctx: ToolContext, tmp_path: Path):
    out = await BashTool().execute({"command": "pwd"}, ctx)
    assert str(tmp_path) in out


async def test_env_extra_injected(ctx: ToolContext):
    ctx.extras["env_extra"] = {"HAHANESS_PROBE_VAR": "42"}
    out = await BashTool().execute({"command": "echo $HAHANESS_PROBE_VAR"}, ctx)
    assert out.strip() == "42"


async def test_output_30k_clip_middle(ctx: ToolContext):
    out = await BashTool().execute(
        {"command": "python3 -c \"print('a' * 40000)\""}, ctx)
    assert len(out) <= BASH_OUTPUT_MAX + 8          # 截断标注自身占的少量额度
    assert "…[截断" in out and "字符]…" in out
    assert out.startswith("aaa")                    # 首部保留
    assert "Exit code" not in out
    m = re.search(r"…\[截断 (\d+) 字符\]…", out)
    assert m and int(m.group(1)) >= 40000 - BASH_OUTPUT_MAX


async def test_timeout_kills_process_group(ctx: ToolContext, tmp_path: Path):
    pidfile = tmp_path / "pid"
    with pytest.raises(ToolError) as ei:
        await BashTool().execute(
            {"command": f"echo $$ > {pidfile}; sleep 30",
             "timeout_s": 1}, ctx)
    msg = str(ei.value)
    assert "超时" in msg and "SIGTERM" in msg
    pid = int(pidfile.read_text().strip())
    assert not Path(f"/proc/{pid}").exists()        # 进程组确已收割


async def test_timeout_captures_partial_output(ctx: ToolContext):
    with pytest.raises(ToolError) as ei:
        await BashTool().execute(
            {"command": "echo before-hang; sleep 30", "timeout_s": 1}, ctx)
    assert "before-hang" in str(ei.value)


async def test_on_output_progress_callback(ctx: ToolContext):
    seen: list[int] = []
    ctx.extras["on_output"] = seen.append
    await BashTool().execute({"command": "echo hello"}, ctx)
    assert seen and seen[-1] >= len("hello\n")      # 累积字节数单调、末值=总量


async def test_missing_command_rejected(ctx: ToolContext):
    with pytest.raises(ToolError):
        await BashTool().execute({}, ctx)


async def test_background_returns_task_id_and_tees_output(
        ctx: ToolContext, sup: ProcessSupervisor, tmp_path: Path):
    ctx.supervisor = sup
    out = await BashTool().execute(
        {"command": "echo bg-line; sleep 0.2; echo bg-done",
         "run_in_background": True}, ctx)
    assert out.startswith("后台任务已启动 task_id=")
    assert "查看输出文件" in out
    task_id = re.search(r"task_id=(\w+)", out).group(1)
    for _ in range(40):                             # 最多 2s 等任务收尾
        if sup.poll(task_id)["status"] != "running":
            break
        await asyncio.sleep(0.05)
    info = sup.poll(task_id)
    assert info["status"] == "done"
    assert "bg-line" in info["output_tail"]
    assert "bg-done" in info["output_tail"]
    out_file = Path(info["output_path"])
    assert out_file == tmp_path / "logs" / f"task_{task_id}.out"
    assert out_file.exists()


async def test_background_without_supervisor_rejected(ctx: ToolContext):
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": "sleep 1",
                                  "run_in_background": True}, ctx)
    assert "ProcessSupervisor" in str(ei.value)


async def test_background_kill(ctx: ToolContext, sup: ProcessSupervisor):
    ctx.supervisor = sup
    out = await BashTool().execute({"command": "sleep 30",
                                    "run_in_background": True}, ctx)
    task_id = re.search(r"task_id=(\w+)", out).group(1)
    await asyncio.sleep(0.15)                       # 等 spawn 落稳
    assert sup.poll(task_id)["status"] == "running"
    result = await sup.kill(task_id)
    assert result["status"] == "killed"
    pid = sup.poll(task_id)["pid"]
    assert not Path(f"/proc/{pid}").exists()
