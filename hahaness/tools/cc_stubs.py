"""CC 形态 stub 工具（伪装模式的工具面整形，仅 stealth 时注册）。

目的：工具集合形状对齐 Claude Code——CC 名单内但 hahaness 无实现的工具
注册为无害 stub（模型见过这些名字，缺失本身就是形状差异）；BashOutput/
KillShell 映射到 hahaness 真实的后台任务治理（真功能，非摆设）。

AUTO_REGISTER=False：由 build_agent 在伪装模式下手动注册（非伪装通道
零影响）。hahaness 特有工具（InteractiveShell）在伪装通道反向隐藏。
"""
from __future__ import annotations

from hahaness.tools.base import Tool, ToolContext, ToolError

AUTO_REGISTER = False


class AskUserQuestionStub(Tool):
    """CC 的用户提问工具——headless 下不可交互，回执引导自主决策。"""

    name = "AskUserQuestion"
    description = "Ask the user a question. In headless mode the user cannot answer."
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        return ("Headless 环境：用户不在线，无法回答提问。请自主决策选最合理"
                "的选项继续执行，并在最终结果中说明你做了什么假设。")


class EnterPlanModeStub(Tool):
    name = "EnterPlanMode"
    description = "Enter plan mode. Headless: informational only."
    input_schema: dict = {"type": "object", "properties": {}, "required": []}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        return "已记录（headless：计划模式仅标记，无交互确认）。"


class ExitPlanModeStub(Tool):
    name = "ExitPlanMode"
    description = "Exit plan mode with a plan. Headless: auto-approved."
    input_schema: dict = {"type": "object",
                          "properties": {"plan": {"type": "string"}},
                          "required": ["plan"]}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        plan = str(args.get("plan") or "")[:400]
        return f"计划已记录（headless 自动批准，直接执行）：\n{plan}"


class BashOutputTool(Tool):
    """CC 的后台任务输出查询——映射 hahaness ProcessSupervisor 真实任务。"""

    name = "BashOutput"
    description = ("Read output from a background shell task "
                   "(task_id from Bash run_in_background).")
    input_schema: dict = {"type": "object",
                          "properties": {"task_id": {"type": "string"}},
                          "required": ["task_id"]}
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        sup = ctx.supervisor
        tid = str(args.get("task_id") or "")
        if sup is None:
            raise ToolError("无 ProcessSupervisor，没有后台任务")
        try:
            info = sup.poll(tid)
        except KeyError as e:
            raise ToolError(str(e)) from None
        return (f"task_id={info['id']} status={info['status']} "
                f"pid={info['pid']}\n{info['output_tail']}")


class KillShellTool(Tool):
    """CC 的后台任务终止——映射 hahaness ProcessSupervisor.kill。"""

    name = "KillShell"
    description = "Kill a background shell task by task_id."
    input_schema: dict = {"type": "object",
                          "properties": {"task_id": {"type": "string"}},
                          "required": ["task_id"]}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        sup = ctx.supervisor
        tid = str(args.get("task_id") or "")
        if sup is None:
            raise ToolError("无 ProcessSupervisor，没有后台任务")
        try:
            out = await sup.kill(tid)
        except KeyError as e:
            raise ToolError(str(e)) from None
        return f"已终止 task_id={tid}：{out['status']}"


def cc_stub_tools() -> list[Tool]:
    """伪装模式注册的 CC 形态工具全集。"""
    return [AskUserQuestionStub(), EnterPlanModeStub(), ExitPlanModeStub(),
            BashOutputTool(), KillShellTool()]
