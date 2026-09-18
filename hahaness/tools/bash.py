"""Bash 工具——bash -c 执行、流式输出累积、超时杀进程组、后台任务托管。

前台 60s 自动转后台（Terminal-Bench 死法①的根治）：前台命令跑满
BASH_AUTO_BG_S 仍未结束时不再干等——ProcessSupervisor.adopt 收养进程
（已捕获输出作为 prelude 落盘、reader 续读），立即返回 task_id 让 agent
继续干别的、稍后轮询输出文件收结果。显式 timeout_s ≤ BASH_AUTO_BG_S 的
短超时保持原杀进程语义（快速失败）；无 supervisor（子代理场景）降级为
原超时行为。

纪律（constants.py）：输出 ≥BASH_OUTPUT_MAX 截中间保首尾；真超时（降级
路径）killpg SIGTERM→宽限→SIGKILL 后抛 ToolError（带已捕获输出前 2000
字符），由 loop 回填 is_error 让模型自救。

心跳钩子：ctx.extras["on_output"]（可 None）在每段输出到达时被同步回调
（参数为累积字节数），供上层判活/转发 heartbeat——工具只管执行，发心跳
是 loop 层的事。后台路径经 ProcessSupervisor 登记，输出 tee 到
logs/task_<id>.out，立即返回 task_id。
"""
from __future__ import annotations

import asyncio
import itertools
import os
import time
from pathlib import Path
from typing import NoReturn

from hahaness.constants import (
    BASH_AUTO_BG_S,
    BASH_OUTPUT_MAX,
    BASH_TIMEOUT_DEFAULT_S,
    STREAM_LINE_MAX,
)
from hahaness.supervisor.process import ProcessSupervisor, kill_process_group
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.truncate import Truncator


def _format_output(raw: bytes, rc: int, scratch_dir: str | None = None) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > BASH_OUTPUT_MAX:
        # 截断即行动（pi 纪律）：全文落盘给路径（session scratch 目录，
        # 不落 cwd——cwd 落盘会改 git status 打掉 system 缓存断点）
        text = Truncator.clip_middle(text, BASH_OUTPUT_MAX)
        if scratch_dir:
            try:
                Path(scratch_dir).mkdir(parents=True, exist_ok=True)
                full_path = Path(scratch_dir) / f"bash_full_{next(_FULL_SEQ)}.out"
                full_path.write_bytes(raw)
                text += f"\n[完整输出已存 {full_path}，可用 Read 或 tail 查看]"
            except OSError:
                pass                  # 落盘失败保留截断标注，不报错
    if rc != 0:
        text = f"{text}\nExit code {rc}" if text else f"Exit code {rc}"
    return text


_FULL_SEQ = itertools.count(1)


class BashTool(Tool):
    """shell 命令执行（前台阻塞或后台托管）。"""

    name = "Bash"
    description = (
        "在会话工作区执行 bash 命令（bash -c，stderr 合并进 stdout）。"
        f"前台跑满 {BASH_AUTO_BG_S}s 自动转后台（返回 task_id 与输出文件，"
        "稍后 tail 收结果）；预计更久的直接 run_in_background=true。可选 "
        "cwd（限工作区子树内）与 env（本次调用附加环境变量）。编译/make/"
        "安装必带 -j$(nproc)。优先用 Grep/Glob/Read 处理文件检索。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout_s": {"type": "number",
                          "description": f"超时秒数（默认 {BASH_TIMEOUT_DEFAULT_S}）"},
            "run_in_background": {"type": "boolean",
                                  "description": "后台运行，立即返回 task_id"},
            "cwd": {"type": "string",
                    "description": "命令工作目录（相对 cwd 解析；必须在会话工作区子树内）"},
            "env": {"type": "object",
                    "description": "本次调用附加的环境变量 {KEY: value}"},
            "description": {"type": "string",
                            "description": "命令用途简述（5-12 词，日志展示用）"},
        },
        "required": ["command"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        command = args.get("command")
        if not command or not isinstance(command, str):
            raise ToolError("缺少必填参数 command（字符串）")
        run_cwd = self._resolve_cwd(args.get("cwd"), ctx)
        env = self._merge_env(args.get("env"), ctx)
        if args.get("run_in_background"):
            return await self._background(command, ctx, env, run_cwd)
        timeout_s = args.get("timeout_s") or BASH_TIMEOUT_DEFAULT_S
        try:
            timeout_s = float(timeout_s)
        except (TypeError, ValueError):
            raise ToolError(f"timeout_s 需为数字（收到 {args.get('timeout_s')!r}）") from None
        if timeout_s <= 0:
            raise ToolError(f"timeout_s 需为正数（收到 {timeout_s}）")
        return await self._foreground(command, ctx, env, timeout_s, run_cwd)

    # ---------------------------------------------------------------- 参数
    @staticmethod
    def _resolve_cwd(arg, ctx: ToolContext) -> Path:
        """每调用 cwd：相对 ctx.cwd 解析，必须在工作区子树内（符号链解开后判）。"""
        if not arg:
            return Path(ctx.cwd)
        if not isinstance(arg, str):
            raise ToolError(f"cwd 需为字符串（收到 {type(arg).__name__}）")
        p = Path(arg)
        if not p.is_absolute():
            p = Path(ctx.cwd) / p
        rp = p.resolve()
        root = Path(ctx.cwd).resolve()
        if rp != root and root not in rp.parents:
            raise ToolError(f"cwd 越界：{arg}（解析为 {rp}，不在工作区 {root} 子树内）")
        if not rp.is_dir():
            raise ToolError(f"cwd 不存在或不是目录：{rp}")
        return rp

    @staticmethod
    def _merge_env(args_env, ctx: ToolContext) -> dict:
        """合并序：os.environ < 会话级 env_extra < 本次调用 env。"""
        env = {**os.environ, **(ctx.extras.get("env_extra") or {})}
        if not args_env:
            return env
        if not isinstance(args_env, dict):
            raise ToolError(f"env 需为对象 {{KEY: value}}（收到 {type(args_env).__name__}）")
        env.update({str(k): str(v) for k, v in args_env.items()})
        return env

    # ---------------------------------------------------------------- 前台
    async def _foreground(self, command: str, ctx: ToolContext, env: dict,
                           timeout_s: float, run_cwd: Path) -> str:
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(run_cwd), env=env, start_new_session=True,
            limit=STREAM_LINE_MAX)
        assert proc.stdout is not None
        # auto-bg 生效条件：有 supervisor 且显式超时比阈值长（短超时保持
        # 快速失败语义；子代理无 supervisor → 降级原超时行为）
        sup = ctx.supervisor
        auto_bg = sup is not None and timeout_s > BASH_AUTO_BG_S
        line_s = BASH_AUTO_BG_S if auto_bg else timeout_s
        started = time.monotonic()
        started_wall = time.time()
        chunks: list[bytes] = []
        total = 0
        on_output = ctx.extras.get("on_output")
        while True:
            remaining = line_s - (time.monotonic() - started)
            if remaining <= 0:
                if auto_bg:
                    return await self._convert_or_finish(proc, sup, command,
                                                         run_cwd, chunks,
                                                         started_wall, timeout_s,
                                                         ctx)
                await self._timeout(proc, chunks, timeout_s)
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(65536),
                                               timeout=remaining)
            except asyncio.TimeoutError:
                # 60s 线（或降级路径的 timeout 线）到：转后台收养或超时杀。
                # 此后本函数绝不再碰 proc.stdout——读权整体移交收养 reader
                # （wait_for 取消 read 后数据留在流缓冲，不丢）。
                if auto_bg:
                    return await self._convert_or_finish(proc, sup, command,
                                                         run_cwd, chunks,
                                                         started_wall, timeout_s,
                                                         ctx)
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
        return _format_output(b"".join(chunks), rc,
                              ctx.extras.get("scratch_dir"))

    async def _convert_or_finish(self, proc: asyncio.subprocess.Process,
                                 sup, command: str, run_cwd: Path,
                                 chunks: list[bytes], started_wall: float,
                                 timeout_s: float, ctx: ToolContext = None) -> str:
        """auto-bg 线到点：进程仍活 → 收养转后台；恰好已退出 → 排空收尾。"""
        if proc.returncode is None:
            info = await sup.adopt(proc, ["bash", "-c", command], run_cwd,
                                   b"".join(chunks), started_at=started_wall)
            captured = b"".join(chunks).decode("utf-8", errors="replace")[:2000]
            return (f"命令前台执行超过 {BASH_AUTO_BG_S:g}s 仍在运行，已自动转后台"
                    f" task_id={info.id} pid={info.pid}"
                    f"（终止：kill -TERM -{info.pgid}）。\n"
                    f"输出文件：{info.output_path}"
                    f"（轮询：Bash 执行 `tail -n 40 {info.output_path}`，"
                    "间隔别太密；期间先继续干下一步能做的事）。\n"
                    f"已捕获输出（前 2000 字符）：\n{captured}")
        # 阈值线上恰好退出：排空残留输出按前台语义收尾（不走收养）
        return await self._drain_and_finish(proc, chunks, ctx)

    async def _drain_and_finish(self, proc: asyncio.subprocess.Process,
                                chunks: list[bytes],
                                ctx: ToolContext = None) -> str:
        """有界排空（≤1s）+ wait + 前台格式化。"""
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(65536),
                                               timeout=max(
                                                   0.05, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
        rc = await proc.wait()
        return _format_output(b"".join(chunks), rc,
                              (ctx.extras if ctx else {}).get("scratch_dir"))

    async def _timeout(self, proc: asyncio.subprocess.Process,
                       chunks: list[bytes], timeout_s: float) -> NoReturn:
        """超时收割：杀进程组后抛 ToolError（带已捕获输出前 2000 字符）。"""
        captured = b"".join(chunks).decode("utf-8", errors="replace")[:2000]
        await kill_process_group(proc)
        raise ToolError(
            f"Bash 命令超时（>{timeout_s:g}s），进程组已 SIGTERM/SIGKILL 终止。"
            f"已捕获输出（前 2000 字符）：\n{captured}")

    # ---------------------------------------------------------------- 后台
    async def _background(self, command: str, ctx: ToolContext, env: dict,
                          run_cwd: Path) -> str:
        sup: ProcessSupervisor | None = ctx.supervisor
        if sup is None:
            raise ToolError("run_in_background 需要 ProcessSupervisor"
                            "（ctx.supervisor 未配置）")
        info = await sup.spawn_bg(["bash", "-c", command], cwd=run_cwd, env=env)
        return (f"后台任务已启动 task_id={info.id}"
                f"（轮询：用 Bash 查看输出文件 {info.output_path}）")


tool = BashTool()             # ToolRegistry.default() 收集的模块级实例
