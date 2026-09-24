"""例 2/10：同名整体替换内置 Grep（pi registerTool 同名替换语义）。

不改编擎代码改变内置工具行为：这里把 Grep 换成「带审计留痕的 Grep」
——参数与语义原样转发内置实现，仅在 .loadn/ext-grep.log 记一行调用。
替换行为在 build_agent 有 log.info 留痕。
"""
from __future__ import annotations

from pathlib import Path

from loadn.tools.base import ToolContext
from loadn.tools.grep import GrepTool

_inner = GrepTool()


class AuditedGrep(GrepTool):
    name = "Grep"

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        out = await _inner.execute(args, ctx)
        try:
            p = Path(ctx.cwd) / ".loadn" / "ext-grep.log"
            p.parent.mkdir(exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(f"grep pattern={args.get('pattern')!r}\n")
        except OSError:
            pass
        return out


def load(ext) -> None:
    ext.register_tool(AuditedGrep())
