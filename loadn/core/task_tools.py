"""TaskList / TaskCancel 引擎工具（P2-4）：注册表对 agent 与宿主的枚举面。"""
from __future__ import annotations

from loadn.core.tasks import registry_of
from loadn.tools.base import Tool, ToolContext, ToolError


class TaskListTool(Tool):
    name = "TaskList"
    description = ("列出本会话在跑的长任务（后台命令/子代理/定时器），含 id/"
                   "类型/描述/已运行秒数。取消用 TaskCancel。")
    input_schema: dict = {"type": "object", "properties": {}}
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        reg = registry_of(ctx)
        if reg is None:
            return "（无注册表）"
        rows = reg.list_alive()
        if not rows:
            return "（无在跑任务）"
        return "\n".join(f"[{r['id']}] {r['kind']:8s} {r['age_s']:>6}s  "
                         f"{r['desc'][:60]}" for r in rows)


class TaskCancelTool(Tool):
    name = "TaskCancel"
    description = ("取消一个长任务（TaskList 的 id）：后台命令杀进程组、"
                   "子代理/定时器取消 asyncio 任务。")
    input_schema: dict = {
        "type": "object",
        "properties": {"task_id": {"type": "string",
                                   "description": "TaskList 里的任务 id"}},
        "required": ["task_id"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        reg = registry_of(ctx)
        if reg is None:
            raise ToolError("无注册表")
        tid = str(args.get("task_id") or "")
        if not tid:
            raise ToolError("缺少 task_id")
        out = await reg.cancel(tid)
        if not out.get("ok"):
            raise ToolError(out.get("error") or "取消失败")
        return f"已取消 {tid}（{out.get('way')}）"


tool = None      # 注册走 build（需 ctx extras 就绪后按会话实例化）
