"""MultiEdit 工具——单文件原子多编辑（对位 claude CLI 的 MultiEdit）。

顺序语义：edits 按序逐个应用，edit N 的 old_string 匹配的是前 N-1 个
编辑作用后的文本（后一个编辑可以改前一个编辑产生的文本）。全部编辑在
内存工作缓冲上模拟应用 + 校验通过后才一次落盘——任何一步失败零写入，
错误文案带 1-based 序号。守卫（读后写 + mtime + 写前复查）复用
edit.py 的共享内核。
"""
from __future__ import annotations

from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.edit import _apply_one, _load_guarded, _unified_diff, _write_guarded


class MultiEditTool(Tool):
    """单文件多处原子编辑（一失败全不落盘）。"""

    name = "MultiEdit"
    description = (
        "对同一文件按序应用多处精确替换（原子：任一编辑失败则全部不"
        "落盘）。edits 顺序应用——后一个编辑匹配的是前一个编辑作用后的"
        "文本。必须先 Read 目标文件；比多次 Edit 少几个来回，适合成组"
        "小改动。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "edits": {
                "type": "array",
                "description": "按序应用的编辑列表（old_string 精确匹配含缩进）",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string",
                                       "description": "要替换的原文（精确匹配）"},
                        "new_string": {"type": "string", "description": "替换后的文本"},
                        "replace_all": {"type": "boolean", "default": False,
                                        "description": "true 时替换该步全部命中"},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["file_path", "edits"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        raw = args.get("file_path")
        edits = args.get("edits")
        if not raw or not isinstance(raw, str):
            raise ToolError("缺少必填参数 file_path")
        if not isinstance(edits, list) or not edits:
            raise ToolError("edits 需为非空数组 [{old_string, new_string, replace_all?}]")

        path, key, text = _load_guarded(raw, ctx)
        work = text
        for i, e in enumerate(edits, 1):
            if not isinstance(e, dict):
                raise ToolError(f"edits[{i}] 需为对象（收到 {type(e).__name__}）")
            old, new = e.get("old_string"), e.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                raise ToolError(f"edits[{i}] 的 old_string/new_string 需为字符串")
            work = _apply_one(work, old, new, bool(e.get("replace_all", False)),
                              label=f"edits[{i}]",
                              allow_fuzzy=path.suffix != ".ipynb")

        diff = _unified_diff(path, text, work)
        _write_guarded(path, key, work, ctx)
        return (f"{''.join(diff)}"
                f"已原子应用 {len(edits)} 处编辑到 {path}（建议用 Read 回读确认）")


tool = MultiEditTool()        # ToolRegistry.default() 收集的模块级实例
