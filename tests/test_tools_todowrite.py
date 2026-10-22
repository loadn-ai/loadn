"""TodoWrite 工具单测——subject 别名（a060 复盘改进 4：GLM 系模型对
Anthropic 原生字段名的稳定幻觉点，作别名接受而非整单拒绝）。"""
from pathlib import Path

from loadn.tools.base import ToolContext
from loadn.tools.todowrite import tool
from loadn.types import SessionState


def _ctx() -> ToolContext:
    return ToolContext(cwd=Path("."), extras={"state": SessionState()})


async def test_subject_alias_accepted():
    out = await tool.execute({"todos": [
        {"id": "1", "subject": "调研计划", "status": "in_progress"}]}, _ctx())
    assert "1 项 todo" in out and "调研计划" in out


async def test_content_still_primary():
    out = await tool.execute({"todos": [
        {"content": "正文优先", "status": "pending"}]}, _ctx())
    assert "正文优先" in out


async def test_both_missing_still_rejected():
    import pytest

    from loadn.tools.base import ToolError
    with pytest.raises(ToolError, match=r"content 缺失"):
        await tool.execute({"todos": [{"id": "1", "status": "pending"}]}, _ctx())
