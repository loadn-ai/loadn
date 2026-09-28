"""transcript.py 单元测试（覆盖率从 0% 补齐——SSE 断线恢复的兜底路径）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from loadn_webui import transcript as ts

SID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """伪 ~/.claude/projects/<slug>/<sid>.jsonl + 工作区。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / ".claude" / "projects" / "-data-code-x"
    proj.mkdir(parents=True)
    ws = tmp_path / "ws"
    (ws / "notes").mkdir(parents=True)
    return proj, ws


def _write(p: Path, events: list[dict]):
    with (p / f"{SID}.jsonl").open("w") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def test_path_found_missing_empty(fake_home):
    proj, _ = fake_home
    _write(proj, [{"x": 1}])
    assert ts.transcript_path(SID) == proj / f"{SID}.jsonl"
    assert ts.transcript_path("00000000-0000-0000-0000-000000000000") is None
    assert ts.transcript_path("") is None


def test_tail_events_kinds(fake_home):
    proj, _ = fake_home
    _write(proj, [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "第一步"},
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "ls -la"}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "WebSearch",
             "input": {"query": "测试查询"}},
        ]}},
        {"garbage": True},
    ])
    evs = ts.tail_events(SID, n=10)
    kinds = [e["kind"] for e in evs]
    assert kinds == ["text", "tool", "tool"]
    assert "ls -la" in evs[1]["text"] and "WebSearch" in evs[2]["text"]


def test_tail_events_corrupt_and_window(fake_home):
    proj, _ = fake_home
    (proj / f"{SID}.jsonl").write_text("not json\n{bad\n")
    assert ts.tail_events(SID) == []
    assert ts.tail_events("00000000-0000-0000-0000-000000000000") == []
    # 窗口截尾：n=1 只留最后一条
    _write(proj, [
        {"message": {"content": [{"type": "text", "text": "A"}]}},
        {"message": {"content": [{"type": "text", "text": "B"}]}},
    ])
    assert [e["text"] for e in ts.tail_events(SID, n=1)] == ["B"]


def test_latest_todos_taskcreate_update(fake_home):
    proj, _ = fake_home
    _write(proj, [
        {"message": {"content": [{"type": "tool_use", "name": "TaskCreate",
                                  "input": {"subject": "调研"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "TaskCreate",
                                  "input": {"subject": "写作"}}]}},
        {"message": {"content": [{"type": "tool_use", "name": "TaskUpdate",
                                  "input": {"taskId": 1, "status": "completed"}}]}},
    ])
    todos = ts.latest_todos(SID)
    assert todos[0] == {"subject": "调研", "status": "completed"}
    assert todos[1]["status"] == "pending"


def test_latest_todos_todowrite_fallback(fake_home):
    proj, _ = fake_home
    _write(proj, [
        {"message": {"content": [{"type": "tool_use", "name": "TodoWrite",
                                  "input": {"todos": [
                                      {"content": "甲", "status": "completed"},
                                      {"content": "乙", "status": "in_progress"}]}}]}},
    ])
    todos = ts.latest_todos(SID)
    assert todos[0]["subject"] == "甲" and todos[1]["status"] == "in_progress"


def test_latest_todos_missing(fake_home):
    assert ts.latest_todos("00000000-0000-0000-0000-000000000000") is None


def test_recent_ws_files(fake_home):
    _, ws = fake_home
    fresh = ws / "notes" / "a.md"
    fresh.write_text("x")
    stale = ws / "notes" / "old.md"
    stale.write_text("y")
    old = time.time() - 3600
    import os
    os.utime(stale, (old, old))
    hidden = ws / ".hidden"
    hidden.mkdir()
    (hidden / "h.txt").write_text("z")
    files = ts.recent_ws_files(ws)
    names = [f["path"] for f in files]
    assert "notes/a.md" in names
    assert "notes/old.md" not in names           # 超 max_age 排除
    assert not any(".hidden" in n for n in names)  # 隐藏目录排除
