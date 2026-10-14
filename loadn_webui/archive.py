"""messages 冷归档（PERF-2）：主库最重资产的冷热分层。

messages 表是主库最大头（生产 avg 23KB/行、max 3.2MB，assistant 行带
tool 输出全文），且除删会话外从不清理。本模块把冷会话（终态+超期无活动）
的中间段消息移出主库到 gzip 归档分片，库内留头尾供侧栏/详情直读，全文
按需回源（GET /sessions/{sid}/messages/archived）。

分层语义：
- 热层（库内）：所有会话元数据 + 未归档窗口（每轮归档留头 KEEP_HEAD 尾
  KEEP_TAIL）——列表/详情/SSE resync 直读不回源。窗口是滚动的：上一轮
  保留的尾段在下轮归档时也进窗口、只再留头 3——库内驻留不随归档轮数
  无限累积（封顶≈每轮 23 条合并收敛）。
- 冷层（文件）：var/archive/messages/<sid>/<seq>.jsonl.gz，每轮归档一个
  分片，kv 游标 archived_upto 记增量边界（二次归档只处理新增段）。

消费面安全（2026-10-14 侦查结论）：引擎 turn 执行只按 turn.message_id 读
当条 user 消息（尾部保留区）；resume/轮换走引擎 transcript；rollback 走
workspace 快照；hooks 取 ORDER BY id DESC LIMIT 1 尾部。已知口径变化：
stats 工具调用统计变「未归档窗口口径」（记台账）。

纪律：归档动作全程过审计（行数/字节/sha256/文件名）；gzip 标准库零新依赖。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from . import db as db_mod
from .config import PATHS

COLD_DAYS = 30        # 冷判定：updated_at 落后今天数
KEEP_HEAD = 3         # 每个归档窗口保留的头/尾消息数
KEEP_TAIL = 20
MIN_FOLD = 8          # 中间段不足此数不值得折叠（索引/游标开销不划算）


def _archive_root(sid: str) -> Path:
    return PATHS["var"] / "archive" / "messages" / sid


def _upto(c: sqlite3.Connection, sid: str) -> int:
    return int(db_mod.kv_get(c, f"archived_upto:{sid}") or 0)


def folded_count(c: sqlite3.Connection, sid: str) -> int:
    """已折叠条数（详情 messages_folded 字段；非会话恒 0）。"""
    return int(db_mod.kv_get(c, f"archived_count:{sid}") or 0)


def cold_session_ids(c: sqlite3.Connection, *, now: str | None = None,
                     cold_days: int = COLD_DAYS) -> list[str]:
    """冷会话：超期无活动 且 无活跃 turn 且 无 active 定时任务（有唤醒
    任务的会话随时复活，不冷）。updated_at 与 iso() 同口径字符串比较。"""
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=cold_days)).isoformat(
        timespec="seconds") if now is None else now
    rows = c.execute(
        "SELECT s.id FROM sessions s WHERE s.updated_at < ?"
        " AND NOT EXISTS (SELECT 1 FROM turns t WHERE t.session_id=s.id"
        "  AND t.status IN ('queued','running'))"
        " AND NOT EXISTS (SELECT 1 FROM scheduled_jobs j"
        "  WHERE j.session_id=s.id AND j.status='active')"
        " ORDER BY s.updated_at", (cutoff,)).fetchall()
    return [r["id"] for r in rows]


def archive_session(c: sqlite3.Connection, sid: str, *,
                    keep_head: int = KEEP_HEAD, keep_tail: int = KEEP_TAIL,
                    min_fold: int = MIN_FOLD) -> dict[str, Any]:
    """单会话增量归档：中间段原文 → gzip 分片（tmp+os.replace 原子）→
    同事务按 id 精确 DELETE + 推进 kv 游标。

    事务边界铁律：文件写成功后才 DELETE——归档文件是原文的唯一新载体，
    反序（先删后写）在崩溃窗口里=永久丢数据。文件写失败直接抛出，库不动。
    """
    upto = _upto(c, sid)
    msgs = c.execute(
        "SELECT * FROM messages WHERE session_id=? AND id>? ORDER BY id",
        (sid, upto)).fetchall()
    if len(msgs) <= keep_head + keep_tail:
        return {"sid": sid, "archived": 0, "reason": "below_keep"}
    mid = msgs[keep_head:-keep_tail]
    if len(mid) < min_fold:
        return {"sid": sid, "archived": 0, "reason": "below_min_fold"}

    root = _archive_root(sid)
    root.mkdir(parents=True, exist_ok=True)
    seq = len(list(root.glob("*.jsonl.gz")))
    name = f"{seq:04d}.jsonl.gz"
    dest, tmp = root / name, root / (name + ".tmp")
    raw = "".join(
        json.dumps(db_mod.to_dict(m), ensure_ascii=False) + "\n"
        for m in mid).encode("utf-8")
    with open(tmp, "wb") as f:
        with gzip.GzipFile(filename=name, mode="wb", fileobj=f, mtime=0) as gz:
            gz.write(raw)              # mtime=0：同输入同输出（可重现归档）
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()[:16]

    # 文件已原子落盘 → 删行+游标（conn() 出口统一 commit）
    c.executemany("DELETE FROM messages WHERE id=?", [(m["id"],) for m in mid])
    db_mod.kv_set(c, f"archived_upto:{sid}", str(mid[-1]["id"]))
    db_mod.kv_set(c, f"archived_count:{sid}",
                  str(folded_count(c, sid) + len(mid)))
    from .security.audit import audit
    audit("archive", {"action": "messages_fold", "sid": sid,
                      "rows": len(mid), "bytes": len(raw), "file": str(dest),
                      "sha256": sha, "upto": mid[-1]["id"]})
    return {"sid": sid, "archived": len(mid), "bytes": len(raw),
            "file": str(dest), "sha256": sha}


def archive_cold(c: sqlite3.Connection, *, dry_run: bool = False,
                 cold_days: int = COLD_DAYS) -> dict[str, Any]:
    """批量归档全部冷会话（管理面入口；PERF-3 挂每日维护任务）。"""
    sids = cold_session_ids(c, cold_days=cold_days)
    if dry_run:
        pending = []
        for s in sids:
            n = c.execute("SELECT COUNT(*) FROM messages WHERE session_id=?"
                          " AND id>?", (s, _upto(c, s))).fetchone()[0]
            pending.append({"sid": s, "pending_messages": n})
        return {"dry_run": True, "cold_sessions": len(sids),
                "sessions": pending}
    results = [archive_session(c, s) for s in sids]
    archived = sum(r["archived"] for r in results)
    return {"cold_sessions": len(sids), "archived_messages": archived,
            "results": results}


def read_archived(sid: str) -> list[dict[str, Any]]:
    """回源：按分片序读全部归档消息（id 升序）。坏分片抛带文件名的
    ValueError——回源路径不许静默吞半截（宪法 6 审计同款姿态）。"""
    root = _archive_root(sid)
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(root.glob("*.jsonl.gz")):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        out.append(json.loads(line))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"归档分片损坏: {p.name} ({e})") from e
    out.sort(key=lambda m: m.get("id") or 0)
    return out


def purge_archive(sid: str) -> bool:
    """删会话时清归档目录（与 workspace rmtree 同生命周期）。"""
    root = _archive_root(sid)
    if root.exists():
        shutil.rmtree(root)
        return True
    return False
