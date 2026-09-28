"""例 4/10：PostToolUse 审计留痕——全部工具调用追加 JSONL。

进程内 handler（无子进程开销）；落 cwd/.loadn/audit.jsonl。
返回 None = 不干预（观察者语义）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _on_post_tool_use(payload: dict):
    try:
        p = Path(".loadn") / "audit.jsonl"
        p.parent.mkdir(exist_ok=True)
        rec = {"ts": round(time.time(), 3), "tool": payload.get("tool"),
               "input": payload.get("input")}
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return None


def load(ext) -> None:
    ext.on("PostToolUse", _on_post_tool_use)
