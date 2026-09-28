"""TodoWrite 工具——全量覆盖写会话 todo 清单。

todo 状态放在 ctx.extras["state"]（loadn.types.SessionState），每次
提交完整清单（不是增量）：按提交顺序重建 state.todos，id 用 t1..tN 稳定
序号——UI/压缩摘要按 id 对齐，不用 uuid 防闪跳。
"""
from __future__ import annotations

from loadn.tools.base import Tool, ToolContext, ToolError
from loadn.types import SessionState, Todo

_VALID_STATUS = ("pending", "in_progress", "completed")
_MARK = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}


class TodoWriteTool(Tool):
    """todo 全量覆盖写（计划管理，模型自组织长任务用）。"""

    name = "TodoWrite"
    description = (
        "全量覆盖写入当前会话的 todo 清单（每次提交完整列表，不是增量"
        "修改）。status：pending|in_progress|completed；activeForm 是进行时"
        "表述（如「正在编译内核」），供 UI 展示。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "完整 todo 清单（覆盖式；空数组=清空）",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "事项内容"},
                        "status": {"type": "string", "enum": list(_VALID_STATUS)},
                        "activeForm": {"type": "string",
                                       "description": "进行时表述（可选）"},
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        state = ctx.extras.get("state")
        if not isinstance(state, SessionState):
            raise ToolError("ctx.extras['state'] 缺少 SessionState，无法写 todo")
        todos = args.get("todos")
        if not isinstance(todos, list):
            raise ToolError("todos 需为数组 [{content, status, activeForm?}]")
        rebuilt: list[Todo] = []
        for i, item in enumerate(todos):
            if not isinstance(item, dict):
                raise ToolError(f"todos[{i}] 需为对象，收到 {type(item).__name__}")
            content = item.get("content")
            if not content or not isinstance(content, str):
                raise ToolError(f"todos[{i}].content 缺失（非空字符串）")
            status = item.get("status") or "pending"
            if status not in _VALID_STATUS:
                raise ToolError(
                    f"todos[{i}].status 非法：{status!r}"
                    f"（合法：{'|'.join(_VALID_STATUS)}）")
            rebuilt.append(Todo(id=f"t{i + 1}", subject=content, status=status,
                                activeForm=item.get("activeForm") or ""))
        state.todos = rebuilt
        if not rebuilt:
            return "已覆盖写入 0 项 todo（清单已清空）"
        lines = [f"已覆盖写入 {len(rebuilt)} 项 todo："]
        for t in rebuilt:
            extra = f"（进行时：{t.activeForm}）" if t.activeForm else ""
            lines.append(f"- {_MARK[t.status]} {t.id} {t.subject} "
                         f"[{t.status}]{extra}")
        return "\n".join(lines)


tool = TodoWriteTool()        # ToolRegistry.default() 收集的模块级实例
