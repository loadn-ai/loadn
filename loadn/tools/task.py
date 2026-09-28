"""Task 子代理工具占位——只锁 name/schema，core 阶段接线后手动注册。

子代理执行牵扯 core/subagent.py 的并发信号量（SUBAGENT_CONCURRENCY）、
独立循环与 read_only 工具集装配，属于 core 层职责，本模块不实现执行。
ToolRegistry.default() 不自动收集本模块（AUTO_REGISTER = False），
core 集成阶段自行实例化注册。
"""
from __future__ import annotations

from typing import NoReturn

from loadn.tools.base import Tool, ToolContext

AUTO_REGISTER = False          # registry 自动装配跳过（core 阶段手动注册）


class TaskTool(Tool):
    """启动子代理执行子任务（骨架占位，未接线）。"""

    name = "Task"
    description = (
        "启动一个子代理独立执行子任务（只读探索或通用调研），拿回其最终"
        "报告。子代理不共享本会话上下文，prompt 必须自包含。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string",
                       "description": "子代理的完整任务提示词（自包含）"},
            "subagent_type": {"type": "string",
                              "description": "子代理类型（general-purpose/explore…）"},
            "description": {"type": "string",
                            "description": "任务简述（3-6 词，进度展示用，可选）"},
        },
        "required": ["prompt", "subagent_type"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> NoReturn:
        raise NotImplementedError("Task 工具由 core/subagent.py 在集成阶段接线")
