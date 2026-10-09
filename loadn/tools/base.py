"""工具协议与执行上下文——所有工具（tools/*.py）实现的契约。

错误语义（工程详设 §4.2）：工具错误是特性不是故障——execute 抛 ToolError
→ loop 回填 is_error=True 的 tool_result 让模型自救；未捕获异常同样回填
（带 repr），永不炸穿循环。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from loadn.types import ToolDef

if TYPE_CHECKING:
    from loadn.supervisor.process import ProcessSupervisor


class ToolError(Exception):
    """工具级可读错误（回填给模型的提示语）。"""


# ---------------------------------------------------------------- 执行域改道路标
# 第 4 起生产实证（2026-10-10 会话 6f12）：off 档 deny 推回内建 Bash 的设计
# 意图没兑现——裸拒绝不带「该用什么」，冷启动模型原地重试 3 次后误诊
# 「内建工具未注入」。拒绝文案必须点名改道目标；「已在工具面」按活面断言
# （内建也全被禁时不虚报）。
_BUILTIN_REDIRECTS = ("Bash", "Read", "Write", "Edit", "Grep", "Glob")


def mcp_redirect_note(tools_dict: dict | None = None) -> str:
    """MCP 工具被策略禁用时的改道提示（面感知：只点名真实在场的内建工具）。"""
    if tools_dict:
        have = [n for n in _BUILTIN_REDIRECTS if n in tools_dict]
        if have:
            return ("本机命令/文件操作请改用内建 " + "/".join(have[:3])
                    + " 等——已在工具面，不受此限。")
    return "请改用工具面中已有的内建工具完成同类操作。"


# ---------------------------------------------------------------- 同文件写互斥
# P0-1（pi file-mutation-queue 同构）：并行子代理/并行 plan 下同文件写
# 的 lost-update 防护。刻意**模块级**注册表而非挂 ToolContext——子代理各持
# 独立 ToolContext（subagent.py 新建，files_touched 本就不共享），ctx 级
# 锁无法跨子代理互斥；同一 asyncio 事件循环内进程级 dict 即全部写入方。
# 键=resolve 规范路径（与 read.file_key 同语义，符号链接归一）；条目不
# 主动回收（每进程编辑过的文件数级，可忽略）。
_FILE_LOCKS: dict[str, asyncio.Lock] = {}
FILE_LOCK_TIMEOUT_S = 30.0     # 取锁超时：报错释放，防并行任务互等死锁

_T = TypeVar("_T")


async def with_file_lock(raw: str | Path, fn: Callable[[], Awaitable[_T]],
                         *, timeout_s: float | None = None) -> _T:
    """按文件串行执行 fn（异文件天然并行）。

    必须包住**读后写守卫之前**的整段（守卫→读→算→写）：锁在守卫后取会让
    排队方看到写前 mtime 而被守卫误杀。超时抛 ToolError（is_error 回填，
    fn 不执行），已持有的锁总在 finally 释放。
    """
    key = str(Path(raw).resolve())
    lock = _FILE_LOCKS.setdefault(key, asyncio.Lock())
    timeout = FILE_LOCK_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout)
    except asyncio.TimeoutError:      # py3.10 尚未与内建 TimeoutError 合并
        raise ToolError(
            f"文件正被另一并行任务编辑，等待 {timeout:.0f}s 超时未获得写入权："
            f"{raw}（重试，或等该任务完成）") from None
    try:
        return await fn()
    finally:
        lock.release()


@dataclass
class ToolContext:
    """一次会话（跨 turn）共享的工具执行上下文。"""
    cwd: Path
    workspace: Path | None = None          # cwd 别名语义（宿主场景相同）
    supervisor: ProcessSupervisor | None = None   # Bash 后台任务/进程组治理
    files_touched: dict[str, float] = field(default_factory=dict)   # Edit 读后写守卫
    # 只读工具集（Task 子代理隔离用：Explore 型禁写）由 registry 决定，不在此处
    extras: dict[str, Any] = field(default_factory=dict)


class Tool:
    """工具基类：子类声明 name/description/input_schema 并实现 execute。

    execute 返回 str（普通文本结果）或 block 列表（Read 图片 → base64
    vision block：[{"type":"image","source":{"type":"base64","media_type":…,
    "data":…}}]，由 loop 原样放进 tool_result.content）。
    """

    name: str = ""
    description: str = ""
    input_schema: dict = {}
    timeout_s: int | None = None           # None = constants 默认（Bash 120s）
    # 只读工具（plan 模式/Explore 子代理放行集合的判定依据）
    read_only: bool = False

    async def execute(self, args: dict, ctx: ToolContext) -> str | list[dict]:
        raise NotImplementedError

    def def_(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description,
                       input_schema=self.input_schema, timeout_s=self.timeout_s)
