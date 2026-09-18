"""NotebookEdit 测试：cell 增删改 / cell_id 定位 / 守卫 / 产物可再解析。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hahaness.tools.base import ToolContext, ToolError
from hahaness.tools.notebook import NotebookEditTool
from hahaness.tools.read import ReadTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


@pytest.fixture
def nb(tmp_path: Path) -> Path:
    """两 cell 的最小 notebook（nbformat 4 形态）。"""
    path = tmp_path / "demo.ipynb"
    path.write_text(json.dumps({
        "cells": [
            {"cell_type": "code", "id": "c1", "metadata": {},
             "source": ["print(1)\n"], "outputs": [], "execution_count": None},
            {"cell_type": "markdown", "id": "c2", "metadata": {},
             "source": ["# 标题\n"]},
        ],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }, ensure_ascii=False))
    return path


async def _read(ctx: ToolContext, path: Path) -> None:
    await ReadTool().execute({"file_path": str(path)}, ctx)   # 满足读后写守卫


async def test_replace_source(ctx: ToolContext, nb: Path):
    await _read(ctx, nb)
    out = await NotebookEditTool().execute(
        {"notebook_path": str(nb), "cell_id": "c1",
         "new_source": "print(42)\nprint(43)"}, ctx)
    assert "已替换" in out
    d = json.loads(nb.read_text())
    assert d["cells"][0]["source"] == ["print(42)\n", "print(43)"]


async def test_insert_before_target(ctx: ToolContext, nb: Path):
    await _read(ctx, nb)
    out = await NotebookEditTool().execute(
        {"notebook_path": str(nb), "cell_id": "c2", "edit_mode": "insert",
         "cell_type": "markdown", "new_source": "插入段"}, ctx)
    assert "插入" in out
    d = json.loads(nb.read_text())
    assert [c["id"] for c in d["cells"]][:2] == ["c1", d["cells"][1]["id"]]
    assert d["cells"][1]["source"] == ["插入段"]
    assert d["cells"][2]["id"] == "c2"


async def test_insert_append_when_no_cell_id(ctx: ToolContext, nb: Path):
    await _read(ctx, nb)
    await NotebookEditTool().execute(
        {"notebook_path": str(nb), "edit_mode": "insert",
         "cell_type": "raw", "new_source": "尾部"}, ctx)
    d = json.loads(nb.read_text())
    assert d["cells"][-1]["cell_type"] == "raw"
    assert d["cells"][-1]["source"] == ["尾部"]


async def test_insert_code_cell_shape(ctx: ToolContext, nb: Path):
    await _read(ctx, nb)
    await NotebookEditTool().execute(
        {"notebook_path": str(nb), "edit_mode": "insert",
         "cell_type": "code", "new_source": "x = 1"}, ctx)
    d = json.loads(nb.read_text())
    cell = d["cells"][-1]
    assert cell["cell_type"] == "code" and cell["outputs"] == []
    assert cell["execution_count"] is None and cell["id"]


async def test_delete_cell(ctx: ToolContext, nb: Path):
    await _read(ctx, nb)
    out = await NotebookEditTool().execute(
        {"notebook_path": str(nb), "cell_id": "c1", "edit_mode": "delete",
         "new_source": ""}, ctx)
    assert "已删除" in out
    d = json.loads(nb.read_text())
    assert [c["id"] for c in d["cells"]] == ["c2"]


async def test_unknown_cell_id_rejected(ctx: ToolContext, nb: Path):
    await _read(ctx, nb)
    with pytest.raises(ToolError) as ei:
        await NotebookEditTool().execute(
            {"notebook_path": str(nb), "cell_id": "nope",
             "new_source": "x"}, ctx)
    assert "cell_id 不存在" in str(ei.value)


async def test_requires_prior_read(ctx: ToolContext, nb: Path):
    with pytest.raises(ToolError) as ei:
        await NotebookEditTool().execute(
            {"notebook_path": str(nb), "cell_id": "c1",
             "new_source": "x"}, ctx)
    assert "读后写守卫" in str(ei.value)


async def test_invalid_json_rejected(ctx: ToolContext, tmp_path: Path):
    bad = tmp_path / "bad.ipynb"
    bad.write_text("{not json")
    await _read(ctx, bad)
    with pytest.raises(ToolError) as ei:
        await NotebookEditTool().execute(
            {"notebook_path": str(bad), "cell_id": "c1",
             "new_source": "x"}, ctx)
    assert "JSON" in str(ei.value)


async def test_not_a_notebook_rejected(ctx: ToolContext, tmp_path: Path):
    plain = tmp_path / "plain.ipynb"
    plain.write_text(json.dumps({"foo": 1}))
    await _read(ctx, plain)
    with pytest.raises(ToolError) as ei:
        await NotebookEditTool().execute(
            {"notebook_path": str(plain), "cell_id": "c1",
             "new_source": "x"}, ctx)
    assert "cells" in str(ei.value)


async def test_output_still_parseable(ctx: ToolContext, nb: Path):
    """编辑产物仍是合法 JSON 且 nbformat 字段保留。"""
    await _read(ctx, nb)
    await NotebookEditTool().execute(
        {"notebook_path": str(nb), "cell_id": "c1",
         "new_source": "print(9)"}, ctx)
    await _read(ctx, nb)   # mtime 已刷新，重新 Read 后再编辑不报守卫错
    await NotebookEditTool().execute(
        {"notebook_path": str(nb), "cell_id": "c2", "edit_mode": "delete",
         "new_source": ""}, ctx)
    d = json.loads(nb.read_text())
    assert d["nbformat"] == 4 and len(d["cells"]) == 1
