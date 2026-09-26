"""M6d：tools 存活变异对赌（grep/read 首轮 64% 的可杀位）。

grep 参数守卫（or→and=非串 pattern 炸进匹配层）、glob 子路径 include
（or→and=「sub/*」式模式恒不中）、read 二进制/缺参守卫。
"""
from __future__ import annotations

import pytest

from loadn.tools.base import ToolContext, ToolError
from loadn.tools.grep import GrepTool
from loadn.tools.read import ReadTool


async def test_grep_pattern_guard_rejects_nonstring(tmp_path):
    """pattern 缺失/非串 → 可读 ToolError（不是 AttributeError 炸穿）。"""
    ctx = ToolContext(cwd=tmp_path)
    for bad in (None, 123):
        with pytest.raises(ToolError, match="pattern"):
            await GrepTool().execute({"pattern": bad}, ctx)


async def test_grep_include_matches_subdir(tmp_path):
    """include glob 按相对路径命中子目录文件（name 与 rel 取或）。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "x.py").write_text("needle\n", encoding="utf-8")
    (tmp_path / "y.txt").write_text("needle\n", encoding="utf-8")
    out = await GrepTool().execute(
        {"pattern": "needle", "path": str(tmp_path),
         "glob": "sub/*", "output_mode": "files_with_matches"},
        ToolContext(cwd=tmp_path))
    assert "x.py" in out and "y.txt" not in out


async def test_read_guards_binary_and_dir(tmp_path):
    ctx = ToolContext(cwd=tmp_path)
    for bad in (None, 123):                      # None 与真值非串双形态
        with pytest.raises(ToolError, match="file_path"):
            await ReadTool().execute({"file_path": bad}, ctx)
    with pytest.raises(ToolError, match="目录"):
        await ReadTool().execute({"file_path": str(tmp_path)}, ctx)
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02" * 100)
    out = await ReadTool().execute({"file_path": str(binary)}, ctx)
    assert isinstance(out, str) and out.startswith("     1")   # 原文读出不炸
