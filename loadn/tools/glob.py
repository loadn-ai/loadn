"""Glob 工具——文件名模式匹配，按 mtime 倒序（新改的在前）。

pathlib/os.walk 递归匹配（fnmatch 语义，`**/*.py` 与 `*.py` 都命中子
目录），排除 .git/node_modules；超 GLOB_MAX_HITS 截断提示收窄。
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from loadn.constants import GLOB_MAX_HITS
from loadn.tools.base import Tool, ToolContext, ToolError


class GlobTool(Tool):
    """按文件名模式找文件（内容检索用 Grep）。"""

    name = "Glob"
    description = (
        "按 glob 模式匹配文件路径（如 \"**/*.py\"、\"src/**/*.ts\"），"
        "按修改时间倒序返回（最近改动的在前）。只找文件名不知道内容时"
        "用本工具；要搜内容用 Grep。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式（如 **/*.py）"},
            "path": {"type": "string", "description": "检索根目录（默认 cwd）"},
        },
        "required": ["pattern"],
    }
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        pattern = args.get("pattern")
        if not pattern or not isinstance(pattern, str):
            raise ToolError("缺少必填参数 pattern")
        root = Path(args.get("path") or str(ctx.cwd))
        if not root.is_dir():
            raise ToolError(f"path 不存在或不是目录：{root}")
        pats = [pattern]
        if pattern.startswith("**/"):
            pats.append(pattern[3:])          # **/*.py 也要命中根级 a.py
        else:
            pats.append("**/" + pattern)      # *.py 也命中任意深度
        matches: list[tuple[float, Path]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d != "node_modules"]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    rel = fp.relative_to(root).as_posix()
                except ValueError:
                    rel = fp.as_posix()
                if not any(fnmatch.fnmatch(fn, p) or fnmatch.fnmatch(rel, p)
                           for p in pats):
                    continue
                try:
                    matches.append((fp.stat().st_mtime, fp))
                except OSError:
                    continue
        if not matches:
            return "（无匹配文件）"
        matches.sort(key=lambda t: -t[0])     # mtime 倒序：新改的在前
        out = [str(fp) for _, fp in matches]
        note = ""
        if len(out) > GLOB_MAX_HITS:
            note = f"\n…还有 {len(out) - GLOB_MAX_HITS} 个文件未列出，建议收窄 pattern 或 path"
            out = out[:GLOB_MAX_HITS]
        return "\n".join(out) + note


tool = GlobTool()             # ToolRegistry.default() 收集的模块级实例
