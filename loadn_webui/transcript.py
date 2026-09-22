"""claude CLI transcript（~/.claude/projects/*/<uuid>.jsonl）解析。

承袭 papergo/webapp.py 的 _transcript_tail / _latest_todos / _recent_ws_files
三件套：服务重启后 session_events 可能缺失时的兜底重建（resync）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def transcript_path(session_id: str) -> Path | None:
    if not session_id:
        return None
    try:
        hits = list((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
        return hits[0] if hits else None
    except OSError:
        return None


def tail_events(session_id: str, n: int = 40) -> list[dict]:
    """尾部活动事件（文本/工具摘要）——实时活动流兜底。"""
    events: list[dict] = []
    p = transcript_path(session_id)
    if p is None:
        return events
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return events
    window = max(120, n * 6)
    for ln in lines[-window:]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        content = (d.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text" and (b.get("text") or "").strip():
                events.append({"kind": "text", "text": b["text"].strip().replace("\n", " ")[:160]})
            elif bt == "tool_use":
                name = b.get("name", "?")
                inp = b.get("input") or {}
                brief = (inp.get("query") or inp.get("command") or inp.get("file_path")
                         or inp.get("path") or inp.get("pattern") or inp.get("url") or "")
                events.append({"kind": "tool", "text": f"{name}  {str(brief)[:110]}"})
    return events[-n:]


def latest_todos(session_id: str) -> list[dict] | None:
    """任务清单重建：TaskCreate/TaskUpdate 重放，TodoWrite 兜底。"""
    p = transcript_path(session_id)
    if p is None:
        return None
    try:
        lines = p.read_text(errors="replace").splitlines()
    except OSError:
        return None
    tasks: list[dict] = []
    todo_write = None
    for ln in lines:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            name = str(b.get("name", ""))
            inp = b.get("input") or {}
            if name == "TaskCreate":
                tasks.append({"subject": inp.get("subject") or inp.get("description") or "任务",
                              "status": "pending"})
            elif name == "TaskUpdate":
                try:
                    tid = int(inp.get("taskId"))
                except (TypeError, ValueError):
                    continue
                if 1 <= tid <= len(tasks) and inp.get("status"):
                    tasks[tid - 1]["status"] = inp["status"]
            elif "todo" in name.lower() and isinstance(inp.get("todos"), list):
                todo_write = [{"subject": t.get("content", ""),
                               "status": t.get("status", "pending")}
                              for t in inp["todos"] if isinstance(t, dict)]
    return tasks or todo_write


def recent_ws_files(ws: Path, max_age_s: int = 1800, limit: int = 25) -> list[dict]:
    """工作区最近生成/修改的文件。"""
    out = []
    now = time.time()
    for p in ws.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(ws))
        if any(part.startswith(".") for part in p.parts):
            continue
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age < max_age_s:
            out.append({"path": rel, "age_s": int(age),
                        "kb": round(p.stat().st_size / 1024, 1)})
    return sorted(out, key=lambda x: x["age_s"])[:limit]
