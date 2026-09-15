"""Bash 工具——bash -c 执行、流式输出累积、超时杀进程组、后台任务托管。

纪律（constants.py）：输出 ≥BASH_OUTPUT_MAX 截中间保首尾；超时（参数覆盖
或 BASH_TIMEOUT_DEFAULT_S）killpg SIGTERM→宽限→SIGKILL 后抛 ToolError
（带已捕获输出前 2000 字符），由 loop 回填 is_error 让模型自救。

心跳钩子：ctx.extras["on_output"]（可 None）在每段输出到达时被同步回调
（参数为累积字节数），供上层判活/转发 heartbeat——工具只管执行，发心跳
是 loop 层的事。后台路径经 ProcessSupervisor 登记，输出 tee 到
logs/task_<id>.out，立即返回 task_id。
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import NoReturn

from hahaness.constants import BASH_OUTPUT_MAX, BASH_TIMEOUT_DEFAULT_S, STREAM_LINE_MAX
from hahaness.supervisor.process import ProcessSupervisor, kill_process_group
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.truncate import Truncator


class BashTool(Tool):
    """shell 命令执行（前台阻塞或后台托管）。"""

    name = "Bash"
    description = (
        "在会话工作区执行 bash 命令（bash -c，stderr 合并进 stdout）。"
        f"默认超时 {BASH_TIMEOUT_DEFAULT_S}s（timeout_s 覆盖）；长任务用 "
        "run_in_background=true 后台执行（立即返回 task_id，输出落 "
        "logs/task_<id>.out）。优先用 Grep/Glob/Read 处理文件检索。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout_s": {"type": "number",
                          "description": f"超时秒数（默认 {BASH_TIMEOUT_DEFAULT_S}）"},
            "run_in_background": {"type": "boolean",
                                  "description": "后台运行，立即返回 task_id"},
            "description": {"type": "string",
                            "description": "命令用途简述（5-12 词，日志展示用）"},
        },
        "required": ["command"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        command = args.get("command")
        if not command or not isinstance(command, str):
            raise ToolError("缺少必填参数 command（字符串）")
        env = {**os.environ, **(ctx.extras.get("env_extra") or {})}
        if args.get("run_in_background"):
            return await self._background(command, ctx, env)
        timeout_s = args.get("timeout_s") or BASH_TIMEOUT_DEFAULT_S
        try:
            timeout_s = float(timeout_s)
        except (TypeError, ValueError):
            raise ToolError(f"timeout_s 需为数字（收到 {args.get('timeout_s')!r}）") from None
        if timeout_s <= 0:
            raise ToolError(f"timeout_s 需为正数（收到 {timeout_s}）")
        return await self._foreground(command, ctx, env, timeout_s)

    # ---------------------------------------------------------------- 前台
    async def _foreground(self, command: str, ctx: ToolContext, env: dict,
                           timeout_s: float) -> str:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ctx.cwd), env=env, start_new_session=True,
            limit=STREAM_LINE_MAX)
        assert proc.stdout is not None
        started = time.monotonic()
        chunks: list[bytes] = []
        total = 0
        on_output = ctx.extras.get("on_output")
        while True:
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                await self._timeout(proc, chunks, timeout_s)
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(65536),
                                               timeout=remaining)
            except asyncio.TimeoutError:
                await self._timeout(proc, chunks, timeout_s)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if on_output is not None:
                try:
                    on_output(total)     # 同步回调：累积字节数（判活/心跳）
                except Exception:
                    pass                  # 回调故障不拖垮工具执行
        rc = await proc.wait()
        text = b"".join(chunks).decode("utf-8", errors="replace")
        text = Truncator.clip_middle(text, BASH_OUTPUT_MAX)
        if rc != 0:
            text = f"{text}\nExit code {rc}" if text else f"Exit code {rc}"
        return text

    async def _timeout(self, proc: asyncio.subprocess.Process,
                       chunks: list[bytes], timeout_s: float) -> NoReturn:
        """超时收割：杀进程组后抛 ToolError（带已捕获输出前 2000 字符）。"""
        captured = b"".join(chunks).decode("utf-8", errors="replace")[:2000]
        await kill_process_group(proc)
        raise ToolError(
            f"Bash 命令超时（>{timeout_s:g}s），进程组已 SIGTERM/SIGKILL 终止。"
            f"已捕获输出（前 2000 字符）：\n{captured}")

    # ---------------------------------------------------------------- 后台
    async def _background(self, command: str, ctx: ToolContext,
                          env: dict) -> str:
        sup: ProcessSupervisor | None = ctx.supervisor
        if sup is None:
            raise ToolError("run_in_background 需要 ProcessSupervisor"
                            "（ctx.supervisor 未配置）")
        info = await sup.spawn_bg(["bash", "-c", command], cwd=ctx.cwd, env=env)
        return (f"后台任务已启动 task_id={info.id}"
                f"（轮询：用 Bash 查看输出文件 {info.output_path}）")


tool = BashTool()             # ToolRegistry.default() 收集的模块级实例
