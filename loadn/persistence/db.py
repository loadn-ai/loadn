"""session.db（SQLite）：会话索引 + usage 累计（工程详设 §4.11）。

真相源是 transcript JSONL；本库只做索引（列目录/resume 定位/用量累计），
损坏可由 transcript 全量重建。无头 CLI 每次 turn 结束 upsert。
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from loadn import loadn_home

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  title TEXT,
  cwd TEXT,
  parent_id TEXT,              -- fork 来源会话
  created_at REAL,
  updated_at REAL,
  usage_json TEXT,             -- 累计 token 用量（四键）
  models_json TEXT             -- 按模型拆分 {model: {…camelCase}}
);
"""


@contextmanager
def conn(home: Path | None = None):
    p = (home or loadn_home()) / "session.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=15)
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def upsert_session(c: sqlite3.Connection, sid: str, *, title: str = "", cwd: str = "",
                   parent_id: str = "") -> None:
    now = time.time()
    c.execute(
        "INSERT INTO sessions(id, title, cwd, parent_id, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=?",
        (sid, title, cwd, parent_id, now, now, now))


def set_title(c: sqlite3.Connection, sid: str, title: str) -> None:
    """P1-7 引擎侧标题写（仅当现题为空——不覆盖用户手改）。"""
    row = c.execute("SELECT title FROM sessions WHERE id=?", (sid,)).fetchone()
    if row and (row["title"] or "").strip():
        return
    c.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?",
              (title[:80], time.time(), sid))


def add_usage(c: sqlite3.Connection, sid: str, usage: dict, model_usage: dict | None,
              model: str) -> None:
    """turn 级 usage 累加进会话行（usage 四键 snake + per-model camel）。"""
    row = c.execute("SELECT usage_json, models_json FROM sessions WHERE id=?",
                    (sid,)).fetchone()
    try:
        total = json.loads(row["usage_json"]) if row and row["usage_json"] else {}
    except (TypeError, json.JSONDecodeError):
        total = {}
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
              "cache_creation_input_tokens"):
        total[k] = (total.get(k) or 0) + (usage.get(k) or 0)
    try:
        models = json.loads(row["models_json"]) if row and row["models_json"] else {}
    except (TypeError, json.JSONDecodeError):
        models = {}
    if model_usage:
        for m, mu in model_usage.items():
            cur = models.get(m) or {}
            for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                      "cacheCreationInputTokens"):
                cur[k] = (cur.get(k) or 0) + (mu.get(k) or 0)
            cur["costUSD"] = (cur.get("costUSD") or 0) + (mu.get("costUSD") or 0)
            models[m] = cur
    c.execute("UPDATE sessions SET usage_json=?, models_json=?, updated_at=? "
              "WHERE id=?", (json.dumps(total), json.dumps(models), time.time(), sid))


def list_sessions(c: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return c.execute("SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                     (limit,)).fetchall()
