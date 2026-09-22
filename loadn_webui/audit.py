"""审计账本（W6.1 最小版，随 W1-1 先行落地）。

append-only 写入 audit_events 表；UI/API 无 UPDATE/DELETE。
哈希链（prev_hash/hash）、按月分表、链头外置锚点、loadn-web audit
tail|verify|export 在 W6.1 完整化（周 4）——本版先把事件留痕做实，
字段已占位（prev_hash 恒创世值），完整化时回填不影响存量行结构。

写入失败策略：log.error 且不阻塞调用方（当前是策略判定的旁路记录；
W6.1 哈希链上线后改为 fail-closed——写不进账本=动作不执行）。
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from .config import PATHS
from .util import get_logger

log = get_logger(__name__)

# 事件类型枚举（v1.1 §6.7）：policy 拦截先行，其余随各工作流落地启用
TYPES = (
    "permission_decision",   # policy.py 判定（allow/block/warn）
    "approval_request", "approval_decision",
    "tool_call_blocked",
    "egress_request", "credential_use", "policy_change",
    "skill_install", "skill_scan",
    "canary_hit", "sandbox_violation",
    "rollback", "kill_switch", "snapshot",
    "anomaly",
)

GENESIS = "0" * 64

_DDL = """
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  sid TEXT,
  turn_id INTEGER,
  type TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_type_ts ON audit_events(type, ts);
"""


def _conn() -> sqlite3.Connection:
    PATHS["var"].mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(PATHS["var"] / "audit.db", timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


_initialized = False


def _ensure_table(c: sqlite3.Connection) -> None:
    global _initialized
    if not _initialized:
        c.executescript(_DDL)
        _initialized = True


def audit(type_: str, detail: dict, *, sid: str | None = None,
          turn_id: int | None = None) -> None:
    """追加一条审计事件（永不抛——见模块 docstring 写失败策略）。"""
    if type_ not in TYPES:
        raise ValueError(f"未知审计事件类型 {type_!r}（TYPES 枚举外）")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with _conn() as c:
            _ensure_table(c)
            c.execute(
                "INSERT INTO audit_events (ts, sid, turn_id, type, detail_json,"
                " prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (ts, sid, turn_id, type_,
                 json.dumps(detail, ensure_ascii=False, default=str),
                 GENESIS, GENESIS))       # W6.1 哈希链完整化前占位
    except sqlite3.Error:
        log.exception("审计写入失败（不阻塞调用方）type=%s", type_)


def tail(n: int = 20, type_: str | None = None) -> list[dict]:
    """最近事件（loadn-web audit tail / 调试用）。"""
    with _conn() as c:
        _ensure_table(c)
        q = "SELECT * FROM audit_events"
        args: list = []
        if type_:
            q += " WHERE type=?"
            args.append(type_)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(n)
        return [dict(r) for r in c.execute(q, args)]


def _now_epoch() -> float:      # 测试可 monkeypatch 的时间源
    return time.time()
