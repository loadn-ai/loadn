"""NotebookEdit 工具——Jupyter .ipynb 的 cell 级编辑（对位 claude CLI）。

零依赖手搓 nbformat 4 子集：json.load → 按 cell_id 定位（cell dict 的
"id" 键）→ edit_mode ∈ replace|insert|delete → 落盘。insert 语义：插到
cell_id 指定的 cell 之前（省略 cell_id = append 到尾部）。新 cell 统一
造 {cell_type, id, source(行列表), metadata:{}}，code 额外带 outputs:[]/
execution_count:None。守卫（读后写 + mtime + 写前复查）复用 edit.py 内核。
"""
from __future__ import annotations

import json
import uuid

from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.edit import _load_guarded, _write_guarded

_CELL_TYPES = ("code", "markdown", "raw")
_MODES = ("replace", "insert", "delete")


class NotebookEditTool(Tool):
    """.ipynb cell 编辑（replace/insert/delete，按 cell_id 定位）。"""

    name = "NotebookEdit"
    description = (
        "编辑 Jupyter notebook 的 cell（.ipynb）。edit_mode=replace 替换"
        " cell_id 的 source；insert 在 cell_id 之前插入新 cell（省略 "
        "cell_id 则追加到尾部）；delete 删除 cell_id。必须先 Read 该文件。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "notebook_path": {"type": "string", "description": "目标 .ipynb 路径"},
            "cell_id": {"type": "string",
                        "description": "目标 cell 的 id（nbformat 4；insert 省略=尾部追加）"},
            "new_source": {"type": "string", "description": "新 cell 内容（replace/insert）"},
            "cell_type": {"type": "string", "enum": list(_CELL_TYPES),
                          "description": "新 cell 类型（insert 必填）"},
            "edit_mode": {"type": "string", "enum": list(_MODES),
                          "default": "replace"},
        },
        "required": ["notebook_path", "new_source"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        raw = args.get("notebook_path")
        if not raw or not isinstance(raw, str):
            raise ToolError("缺少必填参数 notebook_path")
        mode = args.get("edit_mode") or "replace"
        if mode not in _MODES:
            raise ToolError(f"edit_mode 非法：{mode!r}（合法：{'|'.join(_MODES)}）")
        source = args.get("new_source")
        if not isinstance(source, str):
            raise ToolError("new_source 需为字符串")
        if mode != "delete" and not source.strip():
            raise ToolError("new_source 不能为空（delete 模式可省略，但仍需给空串）")

        path, key, text = _load_guarded(raw, ctx)
        try:
            nb = json.loads(text)
        except json.JSONDecodeError as e:
            raise ToolError(f"notebook 不是合法 JSON：{e}") from None
        cells = nb.get("cells")
        if not isinstance(nb, dict) or not isinstance(cells, list):
            raise ToolError(f"{path} 不是 notebook（缺顶层 cells 数组）")

        cell_id = args.get("cell_id")
        index = _find(cells, cell_id) if cell_id else None
        if cell_id and index is None:
            raise ToolError(f"cell_id 不存在：{cell_id}"
                            f"（现有：{[c.get('id') for c in cells][:20]}）")

        if mode == "replace":
            if index is None:
                raise ToolError("replace 需要 cell_id")
            cells[index]["source"] = _split(source)
            note = f"已替换 cell {cell_id} 的 source"
        elif mode == "delete":
            if index is None:
                raise ToolError("delete 需要 cell_id")
            cells.pop(index)
            note = f"已删除 cell {cell_id}"
        else:   # insert
            ctype = args.get("cell_type") or "code"
            if ctype not in _CELL_TYPES:
                raise ToolError(f"cell_type 非法：{ctype!r}（合法：{'|'.join(_CELL_TYPES)}）")
            cell = {"cell_type": ctype, "id": uuid.uuid4().hex[:12],
                    "source": _split(source), "metadata": {}}
            if ctype == "code":
                cell["outputs"] = []
                cell["execution_count"] = None
            cells.insert(index if index is not None else len(cells), cell)
            where = f"（位于 {cell_id} 之前）" if index is not None else "（尾部追加）"
            note = f"已插入 {ctype} cell {cell['id']}{where}"

        _write_guarded(path, key, json.dumps(nb, ensure_ascii=False, indent=1) + "\n", ctx)
        return f"{note}；现有 {len(cells)} 个 cell（建议用 Read 回读确认）"


def _split(source: str) -> list[str]:
    """source 统一存行列表形（nbformat 惯例，保行尾）。"""
    return source.splitlines(keepends=True)


def _find(cells: list[dict], cell_id: str) -> int | None:
    for i, c in enumerate(cells):
        if isinstance(c, dict) and c.get("id") == cell_id:
            return i
    return None


tool = NotebookEditTool()      # ToolRegistry.default() 收集的模块级实例
