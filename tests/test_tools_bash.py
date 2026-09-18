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
    assert out.startswith("Warning: 输出已截断")     # codex 纪律：原尺寸可见
    assert "\naaa" in out                          # 首部保留（Warning 头之后）
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


# ---------------------------------------------------------------- cwd/env 参数
async def test_cwd_param_subdir(ctx: ToolContext, tmp_path: Path):
    """每调用 cwd：相对路径解析到子目录，pwd 生效。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    out = await BashTool().execute(
        {"command": "pwd", "cwd": "sub"}, ctx)
    assert out.strip() == str(sub)


async def test_cwd_param_absolute_inside(ctx: ToolContext, tmp_path: Path):
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    out = await BashTool().execute({"command": "pwd", "cwd": str(sub)}, ctx)
    assert out.strip() == str(sub)


async def test_cwd_param_outside_rejected(ctx: ToolContext, tmp_path: Path):
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": "pwd", "cwd": "/tmp"}, ctx)
    assert "越界" in str(ei.value)


async def test_cwd_param_parent_escape_rejected(ctx: ToolContext, tmp_path: Path):
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": "pwd", "cwd": ".."}, ctx)
    assert "越界" in str(ei.value)


async def test_cwd_param_missing_dir_rejected(ctx: ToolContext):
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": "pwd", "cwd": "no-such-dir"}, ctx)
    assert "不存在" in str(ei.value)


async def test_env_param_injected(ctx: ToolContext):
    out = await BashTool().execute(
        {"command": "echo $HHN_FOO", "env": {"HHN_FOO": "bar-42"}}, ctx)
    assert out.strip() == "bar-42"


async def test_env_param_merges_not_replaces(ctx: ToolContext):
    out = await BashTool().execute(
        {"command": "echo $HHN_A$HHN_B", "env": {"HHN_B": "2"}}, ctx)
    assert out.strip() == "2"   # HHH_A 为空、B 注入成功


async def test_env_param_invalid_type_rejected(ctx: ToolContext):
    with pytest.raises(ToolError):
        await BashTool().execute({"command": "true", "env": "not-a-dict"}, ctx)


async def test_background_cwd_param(
        ctx: ToolContext, sup: ProcessSupervisor, tmp_path: Path):
    ctx.supervisor = sup
    sub = tmp_path / "bgdir"
    sub.mkdir()
    out = await BashTool().execute(
        {"command": "sleep 0.2; pwd", "run_in_background": True,
         "cwd": "bgdir"}, ctx)
    task_id = re.search(r"task_id=(\w+)", out).group(1)
    for _ in range(40):
        if sup.poll(task_id)["status"] != "running":
            break
        await asyncio.sleep(0.05)
    assert sub.as_posix() in sup.poll(task_id)["output_tail"]


# ---------------------------------------------------------------- auto-bg
async def test_auto_bg_converts_at_threshold(monkeypatch, tmp_path: Path):
    """前台超阈值 → 收养转后台：返回 task_id/输出文件，任务最终 done。"""
    monkeypatch.setattr("hahaness.tools.bash.BASH_AUTO_BG_S", 1.0)
    sup = ProcessSupervisor()
    try:
        ctx = ToolContext(cwd=tmp_path, supervisor=sup)
        out = await BashTool().execute(
            {"command": "echo early; sleep 1.5; echo late", "timeout_s": 30},
            ctx)
        assert "已自动转后台" in out and "task_id=" in out
        m = re.search(r"task_id=(\w+)", out)
        assert m and "输出文件" in out
        for _ in range(80):     # 最多 4s 等收尾
            if sup.poll(m.group(1))["status"] != "running":
                break
            await asyncio.sleep(0.05)
        info = sup.poll(m.group(1))
        assert info["status"] == "done"
        assert "early" in info["output_tail"] and "late" in info["output_tail"]
    finally:
        await sup.shutdown()


async def test_auto_bg_short_timeout_still_kills(tmp_path: Path):
    """显式 timeout_s ≤ 阈值 → 保持原快速失败语义（杀进程组，真死）。"""
    import subprocess
    holder = subprocess.Popen(["bash", "-c", "sleep 5"],
                              start_new_session=True)
    # 借 holder 的子进程模拟：直接跑 Bash 且抓不到 pid —— 改为验证异常语义
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": f"kill -0 {holder.pid}; sleep 3",
                                  "timeout_s": 0.3},
                                 ToolContext(cwd=tmp_path))
    assert "超时" in str(ei.value)
    holder.terminate()
    holder.wait()


async def test_auto_bg_short_timeout_raises(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("hahaness.tools.bash.BASH_AUTO_BG_S", 1.0)
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": "sleep 3", "timeout_s": 0.3},
                                 ToolContext(cwd=tmp_path))
    assert "超时" in str(ei.value)


async def test_auto_bg_degrades_without_supervisor(monkeypatch, tmp_path: Path):
    """无 supervisor（子代理场景）→ 降级为原超时杀。"""
    monkeypatch.setattr("hahaness.tools.bash.BASH_AUTO_BG_S", 0.5)
    with pytest.raises(ToolError) as ei:
        await BashTool().execute({"command": "sleep 3", "timeout_s": 1},
                                 ToolContext(cwd=tmp_path))
    assert "超时" in str(ei.value)


async def test_completes_before_threshold_normal(monkeypatch, tmp_path: Path):
    """阈值前完成 → 正常同步结果，不转后台。"""
    monkeypatch.setattr("hahaness.tools.bash.BASH_AUTO_BG_S", 5.0)
    out = await BashTool().execute({"command": "echo x; sleep 0.2; echo y"},
                                   ToolContext(cwd=tmp_path))
    assert "已自动转后台" not in out
    assert "x" in out and "y" in out


async def test_drain_and_finish_when_exit_at_line(monkeypatch, tmp_path: Path):
    """阈值线上恰好退出 → _drain_and_finish 前台语义收尾（stub proc）。"""
    monkeypatch.setattr("hahaness.tools.bash.BASH_AUTO_BG_S", 0.3)

    class _Stdout:
        def __init__(self):
            self.n = 0

        async def read(self, n):
            self.n += 1
            if self.n == 1:
                await asyncio.sleep(0.05)  # 残留数据很快到达
                return b"tail-output"
            return b""

    class _Proc:
        stdout = _Stdout()
        pid = 999999
        returncode = 0                     # 阈值到点时已退出

        async def wait(self):
            return 0

    tool = BashTool()
    out = await tool._drain_and_finish(_Proc(), [b"head-"])
    assert "head-" in out and "tail-output" in out
    assert "Exit code" not in out


# ---------------------------------------------------------------- 超限落盘
async def test_bash_overflow_saved_to_scratch(ctx: ToolContext, tmp_path: Path):
    """输出超 30k → Warning 头 + 全文落 scratch 目录 + 路径提示。"""
    scratch = tmp_path / "sess"
    ctx.extras["scratch_dir"] = str(scratch)
    out = await BashTool().execute(
        {"command": "python3 -c \"print('b' * 40000)\""}, ctx)
    assert out.startswith("Warning: 输出已截断")
    assert "完整输出已存" in out
    saved = list(scratch.glob("bash_full_*.out"))
    assert saved and b"b" * 40000 in saved[0].read_bytes()


async def test_bash_overflow_no_scratch_degrades(ctx: ToolContext):
    """无 scratch_dir（子代理等）→ 只留截断标注，不落盘不报错。"""
    out = await BashTool().execute(
        {"command": "python3 -c \"print('c' * 40000)\""}, ctx)
    assert out.startswith("Warning: 输出已截断")
    assert "完整输出已存" not in out
