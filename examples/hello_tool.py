"""例 1/10：注册一个全新工具（非替换）——最小可运行扩展。

用法：放 $LOADN_HOME/extensions/ 或 .loadn/extensions/（信任门内），
会话里模型即可调用 Hello。
"""
from __future__ import annotations

from loadn.tools.base import Tool, ToolContext


class HelloTool(Tool):
    name = "Hello"
    description = "打招呼（loadn.ext 示例工具）"
    input_schema: dict = {"type": "object",
                          "properties": {"who": {"type": "string"}}}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        return f"你好，{args.get('who') or '世界'}！"


def load(ext) -> None:
    ext.register_tool(HelloTool())
