"""每日维护任务（PERF-3）：冷归档 + WAL checkpoint + 周度 VACUUM。

挂 scheduler.tick（prune_broadcast 同款 try/except 兜底小块）——凌晨低峰
窗口（本地 03:00-06:00）且当天未跑过时执行一轮。窗口判定+游标全在 kv，
窗口外每 20s tick 只多一次 kv 读。

游标语义（崩溃安全）：全部步骤完成后才写 maint_last_run——中途崩溃下个
tick 重跑（归档幂等 + VACUUM 重试），无丢失窗口；VACUUM 自身失败只不推进
vacuum_last_run（下周/下轮重试），不影响其余步骤与日游标。

VACUUM 铁律：不能在事务内执行（python sqlite3 legacy 模式 DML 隐式
BEGIN）；WAL 下需独占——用独立连接 isolation_level=None（autocommit），
busy_timeout 内拿不到独占即失败，静默留待下轮（调度主路径不受影响）。

删行不缩文件：SQLite DELETE 只进 freelist，主库瘦身必须 VACUUM（生产
freelist 已 37MB，PERF-2 归档后更多）。维护汇总记 audit type=system
（AC-4.1 先例；归档明细在 archive_session 内逐会话审计）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from . import archive as archive_mod
from . import db as db_mod
from .config import PATHS

DAILY_WINDOW = (3, 6)        # 本地时区凌晨窗口（低峰）
VACUUM_EVERY_DAYS = 7        # 周度：上距超过即回收


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def _days_since(date_str: str | None, now: datetime | None = None) -> int:
    if not date_str:
        return 1 << 30
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return 1 << 30          # 坏游标按「很久没跑」处理（触发而非跳过）
    return ((now or datetime.now()) - d).days


def maybe_daily(c: sqlite3.Connection, *,
                now: datetime | None = None) -> bool:
    """tick 挂载入口：窗口内且今天未跑 → 跑一轮维护。返回是否执行。
    只做判定与触发——执行体（run_maintenance）自管连接，避免 VACUUM
    独占需求与 tick 传入连接的事务状态耦合。"""
    t = now or datetime.now()
    if not (DAILY_WINDOW[0] <= t.hour < DAILY_WINDOW[1]):
        return False
    if db_mod.kv_get(c, "maint_last_run") == _today(t):
        return False
    run_maintenance(now=t)
    return True


def _freelist_pages(c: sqlite3.Connection) -> int:
    return int(c.execute("PRAGMA freelist_count").fetchone()[0])


def _vacuum(force: bool, days: int, last: str | None,
            dry_run: bool) -> dict[str, Any]:
    """周度 VACUUM 判定+执行。返回步骤报告（vacuum 字段 skipped/done/failed）。"""
    if not force and days < VACUUM_EVERY_DAYS:
        return {"step": "vacuum", "skipped": True, "reason":
                f"last={last or 'never'} {days}d<{VACUUM_EVERY_DAYS}d"}
    if dry_run:
        return {"step": "vacuum", "skipped": True, "dry_run": True}
    # 独立连接 autocommit：VACUUM 不能在事务内（conn() 的 with 出口 commit
    # 语义与之冲突）；WAL 下需独占，busy 即失败留待下轮
    raw = sqlite3.connect(PATHS["db"], timeout=30, isolation_level=None)
    try:
        raw.execute("PRAGMA busy_timeout=30000")
        before = _freelist_pages(raw)
        raw.execute("VACUUM")
        return {"step": "vacuum", "skipped": False, "done": True,
                "freelist_before": before, "freelist_after": _freelist_pages(raw)}
    except sqlite3.OperationalError as e:
        # 独占拿不到（并发写等超时）——不推进周度游标，下轮重试
        return {"step": "vacuum", "skipped": False, "done": False,
                "failed": str(e)}
    finally:
        raw.close()


def run_maintenance(*, force_vacuum: bool = False, dry_run: bool = False,
                    now: datetime | None = None) -> dict[str, Any]:
    """一轮维护：冷归档 → WAL checkpoint → 周度 VACUUM → 推进日游标。
    各步骤独立 try/except 汇总（单步失败不拖垮其余）；日游标只在非
    dry_run 完成后推进。"""
    report: dict[str, Any] = {"steps": [], "ok": True}
    try:
        with db_mod.conn() as c:
            arch = archive_mod.archive_cold(c, dry_run=dry_run)
        report["steps"].append({"step": "archive_cold", "cold_sessions":
                                arch["cold_sessions"], "archived_messages":
                                arch.get("archived_messages", 0),
                                "dry_run": arch.get("dry_run", False)})
    except Exception as e:                                # noqa: BLE001
        report["steps"].append({"step": "archive_cold", "failed": str(e)})
        report["ok"] = False
    try:
        with db_mod.conn() as c:
            last = db_mod.kv_get(c, "vacuum_last_run")
            days = _days_since(last, now)
            if not dry_run:
                # VACUUM 前先 checkpoint：归档删行留下的 WAL 尽量并入主文件
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        vac = _vacuum(force_vacuum, days, last, dry_run)
        if vac.get("done") and not dry_run:
            with db_mod.conn() as c:
                db_mod.kv_set(c, "vacuum_last_run", _today(now))
        report["steps"].append(vac)
        if vac.get("failed"):
            report["ok"] = False
    except Exception as e:                                # noqa: BLE001
        report["steps"].append({"step": "vacuum", "failed": str(e)})
        report["ok"] = False
    if not dry_run:
        with db_mod.conn() as c:
            db_mod.kv_set(c, "maint_last_run", _today(now))
        from .security.audit import audit
        audit("system", {"action": "daily_maintenance",
                         "ok": report["ok"], "steps": report["steps"]})
    return report
