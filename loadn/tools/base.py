"""工具协议与执行上下文——所有工具（tools/*.py）实现的契约。

错误语义（工程详设 §4.2）：工具错误是特性不是故障——execute 抛 ToolError
→ loop 回填 is_error=True 的 tool_result 让模型自救；未捕获异常同样回填
（带 repr），永不炸穿循环。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loadn.types import ToolDef

if TYPE_CHECKING:
    from loadn.supervisor.process import ProcessSupervisor


class ToolError(Exception):
    """工具级可读错误（回填给模型的提示语）。"""


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
