"""Read/Write/Edit 三件套测试：行号格式 / 截断 / 守卫 / fuzzy 提示 / diff。

读后写守卫（files_touched）与 mtime 外部变更守卫是宪法级路径，全分支
覆盖：先读后写、未读拒写、外部变更拒编辑、replace_all、diff 上限。
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from hahaness.constants import (
    EDIT_DIFF_MAX_LINES,
    READ_FILE_MAX_BYTES,
    READ_LINE_CHARS_MAX,
    WRITE_MAX_BYTES,
)
from hahaness.tools.base import ToolContext, ToolError
from hahaness.tools.edit import EditTool
from hahaness.tools.multiedit import MultiEditTool
from hahaness.tools.read import ReadTool, file_key
from hahaness.tools.write import WriteTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


# ---------------------------------------------------------------- Read
async def test_read_cat_n_format(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    out = await ReadTool().execute({"file_path": str(f)}, ctx)
    assert out.splitlines() == ["     1\talpha", "     2\tbeta", "     3\tgamma"]


async def test_read_offset_limit(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "n.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    out = await ReadTool().execute({"file_path": str(f), "offset": 3,
                                    "limit": 4}, ctx)
    lines = out.splitlines()
    assert lines[:4] == [f"{n:6}\tline{n}" for n in (3, 4, 5, 6)]
    # 截断即行动：还有更多行时 footer 给续读 offset（pi 纪律）
    assert lines[4] == "[文件共 10 行，已显示 3-6；继续读用 offset=7]"


async def test_read_offset_out_of_range(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "small.txt"
    f.write_text("one\n")
    with pytest.raises(ToolError) as ei:
        await ReadTool().execute({"file_path": str(f), "offset": 99}, ctx)
    assert "超出范围" in str(ei.value)


async def test_read_single_line_truncated(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "long.txt"
    f.write_text("x" * (READ_LINE_CHARS_MAX + 500) + "\nshort\n")
    out = await ReadTool().execute({"file_path": str(f)}, ctx)
    first = out.splitlines()[0]
    assert len(first) <= READ_LINE_CHARS_MAX + 40
    assert "…[截断" in first
    assert out.splitlines()[1].endswith("short")


async def test_read_oversize_rejected_sparse(ctx: ToolContext, tmp_path: Path):
    big = tmp_path / "big.bin"
    with open(big, "wb") as fh:                     # sparse 文件，瞬间造 26MB
        fh.truncate(READ_FILE_MAX_BYTES + 1024 * 1024)
    with pytest.raises(ToolError) as ei:
        await ReadTool().execute({"file_path": str(big)}, ctx)
    assert "拒读" in str(ei.value)


async def test_read_image_returns_base64_block(ctx: ToolContext,
                                               tmp_path: Path):
    data = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    img = tmp_path / "pic.png"
    img.write_bytes(data)
    blocks = await ReadTool().execute({"file_path": str(img)}, ctx)
    assert isinstance(blocks, list) and len(blocks) == 1
    b = blocks[0]
    assert b["type"] == "image"
    assert b["source"]["type"] == "base64"
    assert b["source"]["media_type"] == "image/png"
    assert b["source"]["data"] == base64.b64encode(data).decode()
    assert str(img) not in ctx.files_touched       # 图片不进读后写守卫


async def test_read_missing_lists_siblings(ctx: ToolContext, tmp_path: Path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    with pytest.raises(ToolError) as ei:
        await ReadTool().execute({"file_path": str(tmp_path / "c.txt")}, ctx)
    msg = str(ei.value)
    assert "文件不存在" in msg and "a.txt" in msg and "b.txt" in msg


async def test_read_registers_files_touched(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "reg.txt"
    f.write_text("hello\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    key = file_key(f)
    assert key in ctx.files_touched
    assert ctx.files_touched[key] == f.stat().st_mtime


# ---------------------------------------------------------------- Write
async def test_write_new_file_and_parents(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "deep" / "sub" / "new.txt"
    out = await WriteTool().execute({"file_path": str(f),
                                     "content": "a\nb\nc"}, ctx)
    assert "已写入" in out and "3 行" in out
    assert f.read_text() == "a\nb\nc"
    assert file_key(f) in ctx.files_touched


async def test_write_existing_requires_prior_read(ctx: ToolContext,
                                                  tmp_path: Path):
    f = tmp_path / "keep.txt"
    f.write_text("原有内容\n")
    with pytest.raises(ToolError) as ei:
        await WriteTool().execute({"file_path": str(f), "content": "覆盖"}, ctx)
    assert "文件已存在但本会话未读取过" in str(ei.value)
    assert f.read_text() == "原有内容\n"          # 未被覆盖


async def test_write_after_read_allowed(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "rw.txt"
    f.write_text("旧\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    out = await WriteTool().execute({"file_path": str(f), "content": "新\n"}, ctx)
    assert "已写入" in out
    assert f.read_text() == "新\n"


async def test_write_max_bytes_guard(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "big.txt"
    with pytest.raises(ToolError) as ei:
        await WriteTool().execute({"file_path": str(f),
                                   "content": "x" * (WRITE_MAX_BYTES + 1)},
                                  ctx)
    assert "上限" in str(ei.value)
    # 恰好 1MB 边界放行
    out = await WriteTool().execute({"file_path": str(f),
                                     "content": "y" * WRITE_MAX_BYTES}, ctx)
    assert "已写入" in out


# ---------------------------------------------------------------- Edit
async def test_edit_unique_match_returns_diff(ctx: ToolContext,
                                              tmp_path: Path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    return 1\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    out = await EditTool().execute(
        {"file_path": str(f), "old_string": "return 1",
         "new_string": "return 42"}, ctx)
    assert "-    return 1" in out and "+    return 42" in out
    assert "已编辑" in out and "Read" in out          # 回读提示
    assert f.read_text() == "def foo():\n    return 42\n"
    # 编辑后 files_touched 已刷新：无需重读可连续编辑
    out2 = await EditTool().execute(
        {"file_path": str(f), "old_string": "def foo()",
         "new_string": "def bar()"}, ctx)
    assert "已编辑" in out2


async def test_edit_requires_prior_read(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "guard.txt"
    f.write_text("content\n")
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f),
                                  "old_string": "content",
                                  "new_string": "changed"}, ctx)
    assert "Read" in str(ei.value) and "守卫" in str(ei.value)


async def test_edit_external_mtime_change_rejected(ctx: ToolContext,
                                                   tmp_path: Path):
    f = tmp_path / "touched.txt"
    f.write_text("base\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    os.utime(f, (1000000, 1000000))                 # 模拟外部编辑（确定性 mtime）
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f), "old_string": "base",
                                  "new_string": "new"}, ctx)
    assert "文件已外部变更" in str(ei.value)


async def test_edit_multiple_hits_lists_line_numbers(ctx: ToolContext,
                                                     tmp_path: Path):
    f = tmp_path / "dup.txt"
    f.write_text("a\ntarget\nb\ntarget\nc\ntarget\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f),
                                  "old_string": "target",
                                  "new_string": "hit"}, ctx)
    msg = str(ei.value)
    assert "3 次" in msg and "2" in msg and "4" in msg and "6" in msg


async def test_edit_zero_hit_fuzzy_hint(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "fuzzy.py"
    f.write_text("import os\n\n\ndef hello_wrold():\n    pass\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f),
                                  "old_string": "def hello_world():",
                                  "new_string": "def hi():"}, ctx)
    msg = str(ei.value)
    assert "未找到 old_string" in msg
    assert "hello_wrold" in msg                    # fuzzy 找到相近行
    assert f"{4:6}\tdef hello_wrold():" in msg     # ±3 上下文带行号


async def test_edit_replace_all(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "many.txt"
    f.write_text("x\ny\nx\n")
    await ReadTool().execute({"file_path": str(f)}, ctx)
    out = await EditTool().execute({"file_path": str(f), "old_string": "x",
                                    "new_string": "z", "replace_all": True},
                                   ctx)
    assert f.read_text() == "z\ny\nz\n"
    assert out.count("+z") == 2


async def test_edit_diff_limit_rejected(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "huge.txt"
    f.write_text("old\n" * (EDIT_DIFF_MAX_LINES + 50))
    await ReadTool().execute({"file_path": str(f)}, ctx)
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f), "old_string": "old",
                                  "new_string": "new",
                                  "replace_all": True}, ctx)
    assert "分步" in str(ei.value) or "拆" in str(ei.value)
    assert f.read_text() == "old\n" * (EDIT_DIFF_MAX_LINES + 50)   # 未动


async def test_edit_write_then_edit_no_reread(ctx: ToolContext,
                                              tmp_path: Path):
    f = tmp_path / "flow.txt"
    await WriteTool().execute({"file_path": str(f), "content": "v1\n"}, ctx)
    out = await EditTool().execute({"file_path": str(f), "old_string": "v1",
                                    "new_string": "v2"}, ctx)
    assert "已编辑" in out
    assert f.read_text() == "v2\n"


# ---------------------------------------------------------------- MultiEdit
async def _read_for_edit(ctx: ToolContext, path: Path) -> None:
    """满足读后写守卫：Read 一次登记 mtime。"""
    await ReadTool().execute({"file_path": str(path)}, ctx)


async def test_multiedit_sequential_semantics(ctx: ToolContext, tmp_path: Path):
    """顺序应用：后一个编辑能匹配前一个编辑产生的文本。"""
    f = tmp_path / "seq.txt"
    f.write_text("alpha\nbeta\n")
    await _read_for_edit(ctx, f)
    out = await MultiEditTool().execute({"file_path": str(f), "edits": [
        {"old_string": "alpha", "new_string": "alpha2"},
        {"old_string": "alpha2\nbeta", "new_string": " ALPHA2+Beta "},
    ]}, ctx)
    assert "2 处编辑" in out
    assert f.read_text() == " ALPHA2+Beta \n"


async def test_multiedit_atomic_on_failure(ctx: ToolContext, tmp_path: Path):
    """第 2 个编辑失败 → 零写入（byte 级不变）+ 错误带序号。"""
    f = tmp_path / "atom.txt"
    f.write_text("one\ntwo\n")
    before = f.read_bytes()
    await _read_for_edit(ctx, f)
    with pytest.raises(ToolError) as ei:
        await MultiEditTool().execute({"file_path": str(f), "edits": [
            {"old_string": "one", "new_string": "ONE"},
            {"old_string": "nope-no-such", "new_string": "x"},
        ]}, ctx)
    assert "edits[2]" in str(ei.value)
    assert f.read_bytes() == before          # 原子：第一步也没落盘


async def test_multiedit_requires_prior_read(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "guard.txt"
    f.write_text("x\n")
    with pytest.raises(ToolError) as ei:
        await MultiEditTool().execute({"file_path": str(f), "edits": [
            {"old_string": "x", "new_string": "y"}]}, ctx)
    assert "读后写守卫" in str(ei.value)


async def test_multiedit_empty_edits_rejected(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "e.txt"
    f.write_text("x\n")
    await _read_for_edit(ctx, f)
    with pytest.raises(ToolError):
        await MultiEditTool().execute({"file_path": str(f), "edits": []}, ctx)


async def test_multiedit_mtime_external_change(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("x\n")
    await _read_for_edit(ctx, f)
    # 外部改动：重写同内容但刷新 mtime
    import time as _t
    _t.sleep(0.01)
    f.write_text("x\n")
    with pytest.raises(ToolError) as ei:
        await MultiEditTool().execute({"file_path": str(f), "edits": [
            {"old_string": "x", "new_string": "y"}]}, ctx)
    assert "外部变更" in str(ei.value)


async def test_multiedit_diff_limit_rejected(ctx: ToolContext, tmp_path: Path):
    f = tmp_path / "big.txt"
    f.write_text("".join(f"line{i}\n" for i in range(EDIT_DIFF_MAX_LINES + 50)))
    await _read_for_edit(ctx, f)
    with pytest.raises(ToolError) as ei:
        await MultiEditTool().execute({"file_path": str(f), "edits": [
            {"old_string": f"line{i}\n", "new_string": f"LINE{i}!!\n"}
            for i in range(EDIT_DIFF_MAX_LINES + 50)]}, ctx)
    assert "超过上限" in str(ei.value)
    assert f.read_text().splitlines()[0] == "line0"   # 零写入


# ---------------------------------------------------------------- 截断即行动
async def test_read_footer_only_when_more_lines(ctx: ToolContext, tmp_path: Path):
    """读完整个文件（无剩余行）→ 无 footer。"""
    f = tmp_path / "full.txt"
    f.write_text("a\nb\n")
    out = await ReadTool().execute({"file_path": str(f)}, ctx)
    assert "继续读用" not in out and "文件共" not in out


async def test_read_long_line_sed_hint(ctx: ToolContext, tmp_path: Path):
    """超长单行截断 → footer 给 sed -n 'Xp' 取整行命令。"""
    f = tmp_path / "wide.txt"
    f.write_text("short\n" + "x" * (READ_LINE_CHARS_MAX + 300) + "\nlast\n")
    out = await ReadTool().execute({"file_path": str(f)}, ctx)
    assert "行超长被截断" in out
    assert "sed -n '2p'" in out and str(f) in out


async def test_read_exact_window_no_footer(ctx: ToolContext, tmp_path: Path):
    """limit 恰好覆盖到文件尾 → 无 footer。"""
    f = tmp_path / "two.txt"
    f.write_text("l1\nl2\n")
    out = await ReadTool().execute({"file_path": str(f), "offset": 2,
                                    "limit": 5}, ctx)
    assert "l2" in out and "继续读用" not in out


# ---------------------------------------------------------------- 归一化 fuzzy
async def test_edit_smart_quotes_normalized(ctx: ToolContext, tmp_path: Path):
    """精确 0 命中 + 智能引号差异 → 归一化命中；区间外原文字节不变。"""
    f = tmp_path / "q.py"
    f.write_bytes('print(“hello”)\nKEEP_ME  \nother = 1\n'.encode())
    await _read_for_edit(ctx, f)
    await EditTool().execute({"file_path": str(f),
                              "old_string": 'print("hello")',
                              "new_string": 'print("bye")'}, ctx)
    body = f.read_bytes().decode()
    assert 'print("bye")' in body
    assert "KEEP_ME  \n" in body            # 未动行的原始字节保留


async def test_edit_trailing_whitespace_and_crlf(ctx: ToolContext, tmp_path: Path):
    """行尾空白差异 + CRLF 文件：命中且替换段跟随 \r\n。"""
    f = tmp_path / "c.txt"
    f.write_bytes(b"alpha  \r\nbeta\r\ngamma\r\n")
    await _read_for_edit(ctx, f)
    await EditTool().execute({"file_path": str(f),
                              "old_string": "alpha\nbeta",
                              "new_string": "ALPHA\nBETA"}, ctx)
    body = f.read_bytes().decode()
    assert "ALPHA\r\nBETA\r\ngamma" in body.replace("  ", "")


async def test_edit_nfkc_fullwidth(ctx: ToolContext, tmp_path: Path):
    """NFKC 归一：全角字母 vs 半角命中。"""
    f = tmp_path / "n.txt"
    f.write_text("value = ａｂｃ\nrest = 2\n")
    await _read_for_edit(ctx, f)
    await EditTool().execute({"file_path": str(f),
                              "old_string": "value = abc",
                              "new_string": "value = xyz"}, ctx)
    assert "value = xyz" in f.read_text()


async def test_edit_fuzzy_rejects_degenerate(ctx: ToolContext, tmp_path: Path):
    """空窗守卫：过短 old（归一后 <8 非空白字符）拒绝 → 走 _no_hit 报错。"""
    f = tmp_path / "d.txt"
    f.write_text("aaa\nbbb\n")
    await _read_for_edit(ctx, f)
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f),
                                  "old_string": "zzz",
                                  "new_string": "y"}, ctx)
    assert "未找到" in str(ei.value)


async def test_edit_fuzzy_multiple_normalized_hits_rejected(
        ctx: ToolContext, tmp_path: Path):
    """归一后多命中 → 拒绝（不猜）。"""
    f = tmp_path / "m.txt"
    f.write_text('print(“a”)\nmid\nprint(“a”)\n')
    await _read_for_edit(ctx, f)
    with pytest.raises(ToolError) as ei:
        await EditTool().execute({"file_path": str(f),
                                  "old_string": 'print("a")',
                                  "new_string": 'print("b")'}, ctx)
    assert "未找到" in str(ei.value)      # fuzzy 层放弃 → 0 命中报错


async def test_edit_ipynb_no_fuzzy(ctx: ToolContext, tmp_path: Path):
    """.ipynb 源文本禁用 fuzzy（JSON 语义安全）。"""
    f = tmp_path / "x.ipynb"
    f.write_text('{"cells": [ ], "metadata": {}}\n')
    await _read_for_edit(ctx, f)
    with pytest.raises(ToolError):
        await EditTool().execute({"file_path": str(f),
                                  "old_string": '"cells" : [ ]',
                                  "new_string": '"cells": []'}, ctx)


async def test_multiedit_inherits_fuzzy(ctx: ToolContext, tmp_path: Path):
    """MultiEdit 经 _apply_one 自动获得 fuzzy 层。"""
    f = tmp_path / "mm.txt"
    f.write_bytes('print(“one”)\nkeep\n'.encode())
    await _read_for_edit(ctx, f)
    await MultiEditTool().execute({"file_path": str(f), "edits": [
        {"old_string": 'print("one")', "new_string": 'print("uno")'},
    ]}, ctx)
    assert 'print("uno")' in f.read_text()


def test_fuzzy_replace_last_line_no_eol():
    """末行无换行文件：不引入尾换行。"""
    from hahaness.tools.edit import _fuzzy_replace
    out = _fuzzy_replace("aaa\ncontent-line  \ntail", "content-line", "CHANGED")
    assert out == "aaa\nCHANGED\ntail"
    assert not out.endswith("\n")
