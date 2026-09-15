"""Edit 工具——精确字符串替换，读后写守卫 + mtime 外部变更守卫。

三重前置：①文件必须在本会话 Read 过（files_touched 有键）；②mtime 与
登记一致（期间被外部改过 → 拒绝，重读再改）；③old_string 精确匹配
（含缩进）。0 命中给 fuzzy 提示（difflib.get_close_matches + 目标行
±3 上下文）；多命中未开 replace_all 列出全部行号；单次 diff 超
EDIT_DIFF_MAX_LINES 拒绝并建议分步——大改用 Write 或多次小步。
"""
from __future__ import annotations

import difflib
from pathlib import Path

from hahaness.constants import EDIT_DIFF_MAX_LINES
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.tools.read import file_key


class EditTool(Tool):
    """文件内精确替换（Edit 对位基础是 Read 的 `行号\\t内容` 输出）。"""

    name = "Edit"
    description = (
        "对文件做精确字符串替换（old_string 含缩进精确匹配）。必须先 "
        "Read 目标文件；文件被外部变更过会拒绝并要求重读。old_string "
        "需唯一（多处命中时扩大上下文或 replace_all=true）。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "old_string": {"type": "string", "description": "要替换的原文（精确匹配含缩进）"},
            "new_string": {"type": "string", "description": "替换后的文本"},
            "replace_all": {"type": "boolean", "default": False,
                            "description": "true 时替换全部命中（默认 false 需唯一）"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        raw = args.get("file_path")
        old = args.get("old_string")
        new = args.get("new_string")
        if not raw or not isinstance(raw, str):
            raise ToolError("缺少必填参数 file_path")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("old_string/new_string 需为字符串")
        replace_all = bool(args.get("replace_all", False))
        if not old:
            raise ToolError("old_string 不能为空（空串必多命中且易误伤）")
        if old == new:
            raise ToolError("old_string 与 new_string 相同，无需编辑")
        key = file_key(raw)
        if key not in ctx.files_touched:
            raise ToolError(f"本会话尚未 Read 过 {raw}，先 Read 再 Edit（读后写守卫）")
        path = Path(raw)
        if not path.exists():
            raise ToolError(f"文件不存在：{path}（已被外部删除？）")
        st = path.stat()
        if st.st_mtime != ctx.files_touched[key]:
            raise ToolError(f"文件已外部变更，请重读后再编辑：{path}")
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{path} 不是 UTF-8 文本文件，Edit 只支持文本") from None

        positions: list[int] = []
        start = 0
        while True:
            i = text.find(old, start)
            if i < 0:
                break
            positions.append(i)
            start = i + len(old)          # 非重叠命中
        if not positions:
            raise ToolError(self._no_hit(text, old))
        if len(positions) > 1 and not replace_all:
            line_nos = [text.count("\n", 0, p) + 1 for p in positions]
            raise ToolError(
                f"old_string 出现 {len(positions)} 次（行 "
                f"{', '.join(str(n) for n in line_nos)}），不唯一；"
                "扩大上下文使其唯一，或 replace_all=true 全量替换")

        new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        diff = list(difflib.unified_diff(
            text.splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=f"{path}（旧）", tofile=f"{path}（新）"))
        if len(diff) > EDIT_DIFF_MAX_LINES:
            raise ToolError(
                f"本次编辑 diff {len(diff)} 行超过上限 {EDIT_DIFF_MAX_LINES} 行，"
                "拒绝执行；请拆成多次小步编辑，或整文件重写用 Write")

        path.write_bytes(new_text.encode("utf-8"))
        ctx.files_touched[key] = path.stat().st_mtime
        return f"{''.join(diff)}已编辑 {path}（建议用 Read 回读确认）"

    # ---------------------------------------------------------------- 0 命中
    @staticmethod
    def _no_hit(text: str, old: str) -> str:
        """0 命中提示：fuzzy 相近行 ±3 上下文；无相近则给文件头。"""
        lines = text.splitlines()
        uniq = list(dict.fromkeys(lines))
        close = difflib.get_close_matches(old, uniq, n=3, cutoff=0.6)
        if not close:
            stripped = list(dict.fromkeys(ln.strip() for ln in lines if ln.strip()))
            close = [c for c in (difflib.get_close_matches(
                old.strip(), stripped, n=3, cutoff=0.6))][:3]
        parts = [f"未找到 old_string（精确匹配，含缩进）：{old[:120]!r}"]
        if close:
            parts.append("最接近的行（缩进/空白可能不同，行号±3 上下文）：")
            for target in close:
                for idx, line in enumerate(lines):
                    if line == target or line.strip() == target:
                        lo, hi = max(0, idx - 3), min(len(lines), idx + 4)
                        ctx_lines = "\n".join(
                            f"{lo + j + 1:6}\t{lines[lo + j]}"
                            for j in range(hi - lo))
                        parts.append(ctx_lines)
                        break
        else:
            head = "\n".join(f"{i + 1:6}\t{ln}"
                             for i, ln in enumerate(lines[:15]))
            parts.append(f"无相近行；文件前 {min(15, len(lines))} 行如下（核对后重试）：\n{head}")
        return "\n".join(parts)


tool = EditTool()             # ToolRegistry.default() 收集的模块级实例
