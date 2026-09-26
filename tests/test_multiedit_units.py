"""M2b：MultiEdit 直测（突变扫描 7/7 全活——参数校验/原子应用从未正测）。

M2 扫描暴露：multiedit 0% 杀伤——file_mutex 只测锁互斥（mock 掉
_write_guarded），evals 场景走 ScriptedProvider 全链但断言只在文件终态。
本文件直打 execute：
- 参数校验三门：缺 file_path/非字符串；edits 非数组或空；元素非对象/
  old/new 非字符串——**校验反转（not isinstance → isinstance）全存活**
- 原子性：第 N 步失配 → 全不落盘（1-based 序号文案）
- 顺序语义：edit N 匹配前 N-1 作用后的文本（链式替换）
- replace_all 透传 + 成功文案计数
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loadn.tools.base import ToolContext, ToolError
from loadn.tools.multiedit import MultiEditTool
from loadn.tools.read import ReadTool


async def _ready(tmp_path: Path, name: str, body: str) -> tuple[Path, ToolContext]:
    f = tmp_path / name
    f.write_text(body, encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, ctx)
    return f, ctx


# ---------------------------------------------------------------- 参数校验
async def test_missing_file_path_rejected(tmp_path):
    ctx = ToolContext(cwd=tmp_path)
    with pytest.raises(ToolError, match="file_path"):
        await MultiEditTool().execute(
            {"edits": [{"old_string": "a", "new_string": "b"}]}, ctx)
    with pytest.raises(ToolError, match="file_path"):   # 非字符串同拒
        await MultiEditTool().execute(
            {"file_path": 123, "edits": [{"old_string": "a",
                                           "new_string": "b"}]}, ctx)


async def test_bad_edits_list_rejected(tmp_path):
    ctx = ToolContext(cwd=tmp_path)
    with pytest.raises(ToolError, match="非空数组"):
        await MultiEditTool().execute({"file_path": "/tmp/x",
                                       "edits": "not-a-list"}, ctx)
    with pytest.raises(ToolError, match="非空数组"):
        await MultiEditTool().execute({"file_path": "/tmp/x",
                                       "edits": []}, ctx)


async def test_bad_edit_element_rejected(tmp_path):
    f, ctx = await _ready(tmp_path, "e.txt", "hello\n")
    with pytest.raises(ToolError, match=r"edits\[1\] 需为对象"):
        await MultiEditTool().execute(
            {"file_path": str(f), "edits": ["string-not-dict"]}, ctx)
    with pytest.raises(ToolError, match=r"edits\[2\]"):
        await MultiEditTool().execute(
            {"file_path": str(f),
             "edits": [{"old_string": "hello", "new_string": "hi"},
                       {"old_string": 42, "new_string": "x"}]}, ctx)
    assert f.read_text() == "hello\n"          # 校验失败零写入


# ---------------------------------------------------------------- 原子性
async def test_second_edit_failure_no_write(tmp_path):
    """第 2 步失配 → 全不落盘（原子）+ 1-based 序号文案。"""
    f, ctx = await _ready(tmp_path, "doc.md", "alpha\nbeta\n")
    with pytest.raises(ToolError, match=r"edits\[2\]"):
        await MultiEditTool().execute(
            {"file_path": str(f),
             "edits": [
                 {"old_string": "alpha", "new_string": "ALPHA"},   # 会成功
                 {"old_string": "不存在的串", "new_string": "X"},  # 失配
             ]}, ctx)
    assert f.read_text() == "alpha\nbeta\n"    # 成功的第 1 步也不落盘


# ---------------------------------------------------------------- 顺序语义
async def test_sequential_edits_chain(tmp_path):
    """edit N 匹配前 N-1 作用后的文本（链式：第 2 步改第 1 步的产物）。"""
    f, ctx = await _ready(tmp_path, "chain.txt", "aaa\n")
    out = await MultiEditTool().execute(
        {"file_path": str(f),
         "edits": [
             {"old_string": "aaa", "new_string": "bbb"},
             {"old_string": "bbb", "new_string": "ccc"},   # 匹配第 1 步产物
         ]}, ctx)
    assert "已原子应用 2 处编辑" in out
    assert f.read_text() == "ccc\n"


async def test_replace_all_passthrough(tmp_path):
    f, ctx = await _ready(tmp_path, "rep.txt", "x-x-x\n")
    out = await MultiEditTool().execute(
        {"file_path": str(f),
         "edits": [{"old_string": "x", "new_string": "y",
                    "replace_all": True}]}, ctx)
    assert f.read_text() == "y-y-y\n"


async def test_requires_prior_read(tmp_path):
    """未 Read 的文件 → 读后写守卫拒（守卫链复用 edit 内核）。"""
    f = tmp_path / "unread.txt"
    f.write_text("data\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    with pytest.raises(ToolError, match="Read"):
        await MultiEditTool().execute(
            {"file_path": str(f),
             "edits": [{"old_string": "data", "new_string": "x"}]}, ctx)


async def test_replace_all_default_false_on_multi_hits(tmp_path):
    """缺省 replace_all=False：多命中拒（L66 运行时默认对赌——不是 schema 文档位）。"""
    f, ctx = await _ready(tmp_path, "multi.txt", "dup\ndup\n")
    with pytest.raises(ToolError, match="2 次"):
        await MultiEditTool().execute(
            {"file_path": str(f),
             "edits": [{"old_string": "dup", "new_string": "uniq"}]},
            ctx)                     # 不传 replace_all——缺省 False 不全量替换
    assert f.read_text() == "dup\ndup\n"
