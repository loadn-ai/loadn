"""PERF-3：每日维护任务——窗口/游标守卫、VACUUM 周度与瘦身、dry_run
零副作用、audit 汇总、admin 手动触发。零 token。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from loadn_webui import archive as arch
from loadn_webui import db as db_mod
from loadn_webui import maintenance as maint
from loadn_webui.security import audit as audit_mod

from .test_perf_sessions import _mk_session

pytestmark = [pytest.mark.coverage("perf.maint")]


def _mk_cold(sid: str, n_msgs: int = 40) -> None:
    _mk_session(sid, "2000-01-01T00:00:00+00:00")
    from .test_perf_archive import _mk_messages
    _mk_messages(sid, n_msgs)


def _freelist() -> int:
    with db_mod.conn() as c:
        return int(c.execute("PRAGMA freelist_count").fetchone()[0])


def _db_bytes() -> int:
    return db_mod.PATHS["db"].stat().st_size


def _step(rep: dict, name: str) -> dict:
    return next(s for s in rep["steps"] if s["step"] == name)


# ---------------------------------------------------------------- ① 窗口/游标守卫
def test_window_and_daily_cursor_gates():
    """否定路径×2+窗口闭开边界：窗口外不跑；窗口内今天已跑不跑；3 点整
    在窗、6 点整出窗（写死小时——引用 DAILY_WINDOW 会变异同步漂移）。"""
    sid = "20261014_mnt-gate"
    _mk_cold(sid)
    with db_mod.conn() as c:
        assert maint.maybe_daily(
            c, now=datetime(2026, 10, 14, 12, 0)) is False     # 正午窗口外
        assert arch.folded_count(c, sid) == 0, "窗口外不得动库"
        assert maint.maybe_daily(
            c, now=datetime(2026, 10, 14, 6, 0)) is False, "6 点整已出窗"
        assert maint.maybe_daily(
            c, now=datetime(2026, 10, 14, 3, 0)) is True, "3 点整在窗内"
    with db_mod.conn() as c:
        assert arch.folded_count(c, sid) == 17, "窗口内首跑须归档"
        assert db_mod.kv_get(c, "maint_last_run") == "2026-10-14"
        # 游标守卫：同日再进窗口 → 零动作
        assert maint.maybe_daily(
            c, now=datetime(2026, 10, 14, 4, 0)) is False


# ---------------------------------------------------------------- ② VACUUM 周度+瘦身
def test_vacuum_weekly_and_shrink():
    sid = "20261014_mnt-vac"
    _mk_cold(sid)
    with db_mod.conn() as c:
        # 6 天前跑过 → 跳过；恰 7 天前 → 必须跑（写死天数边界，杀 7→8：
        # 8 天判定下 7d<8d 仍 skip）
        db_mod.kv_set(c, "vacuum_last_run",
                      (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"))
    rep = maint.run_maintenance(now=datetime.now())
    assert _step(rep, "vacuum")["skipped"] is True
    with db_mod.conn() as c:
        db_mod.kv_set(c, "vacuum_last_run",
                      (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
    rep7 = maint.run_maintenance(now=datetime.now())
    assert _step(rep7, "vacuum")["done"] is True, "恰 7 天必须回收"
    assert _step(rep7, "vacuum")["skipped"] is False
    assert rep7["ok"] is True, "成功路径汇总 ok 必须为 True"
    assert _step(rep7, "archive_cold")["dry_run"] is False, \
        "真跑时归档步骤报告不得标 dry_run"

    # 造大 free 空间：归档删行 + 再插删一批，freelist >0
    with db_mod.conn() as c:
        arch.archive_session(c, sid)
        for _ in range(500):
            c.execute("INSERT INTO messages(session_id, role, content)"
                      " VALUES(?, 'user', ?)", (sid, "x" * 2000))
        c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
    free_before = _freelist()
    assert free_before > 0, "前置：删行后必须留 free 页（否则断言无意义）"
    size_before = _db_bytes()

    with db_mod.conn() as c:
        # 坏游标按「很久没跑」触发
        db_mod.kv_set(c, "vacuum_last_run", "not-a-date")
    rep2 = maint.run_maintenance(now=datetime.now())
    vac = _step(rep2, "vacuum")
    assert vac["done"] is True
    assert vac["freelist_before"] == free_before
    assert _freelist() == 0, "VACUUM 后 freelist 必须归零"
    assert _db_bytes() < size_before, "文件必须实际缩小（删行不缩文件）"
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, "vacuum_last_run") == datetime.now().strftime(
            "%Y-%m-%d")


# ---------------------------------------------------------------- ③ force+幂等
def test_force_vacuum_and_idempotent():
    with db_mod.conn() as c:
        db_mod.kv_set(c, "vacuum_last_run",
                      datetime.now().strftime("%Y-%m-%d"))   # 今天刚跑
    rep = maint.run_maintenance(force_vacuum=True, now=datetime.now())
    assert _step(rep, "vacuum")["done"] is True, "force 跳过周度判定"
    # force 后普通跑：周度未到 → skipped
    rep2 = maint.run_maintenance(now=datetime.now())
    assert _step(rep2, "vacuum")["skipped"] is True


# ---------------------------------------------------------------- ④ dry_run 零副作用
def test_dry_run_zero_side_effects():
    sid = "20261014_mnt-dry"
    _mk_cold(sid)
    with db_mod.conn() as c:
        db_mod.kv_del(c, "maint_last_run")
        db_mod.kv_del(c, "vacuum_last_run")
    free0, size0 = _freelist(), _db_bytes()
    rep = maint.run_maintenance(dry_run=True, now=datetime.now())
    assert _step(rep, "vacuum")["dry_run"] is True
    assert _step(rep, "vacuum")["skipped"] is True
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, "maint_last_run") is None, "dry_run 不得推进日游标"
        assert db_mod.kv_get(c, "vacuum_last_run") is None
        assert arch.folded_count(c, sid) == 0, "dry_run 不得归档"
    assert _freelist() == free0 and _db_bytes() == size0


# ---------------------------------------------------------------- ⑤ 失败注入
def test_vacuum_failure_no_cursor_advance(monkeypatch):
    """VACUUM 失败（独占拿不到）：汇总 ok=False + failed 字段 + 周度游标
    不推进（下轮重试语义）——杀 done/failed 分支的报告字段族变异。"""
    with db_mod.conn() as c:
        db_mod.kv_del(c, "vacuum_last_run")
    def _fail(force, days, last, dry_run):
        return {"step": "vacuum", "skipped": False, "done": False,
                "failed": "database is locked (注入)"}
    monkeypatch.setattr(maint, "_vacuum", _fail)
    rep = maint.run_maintenance(now=datetime.now())
    vac = _step(rep, "vacuum")
    assert vac["failed"] and vac["done"] is False
    assert vac["skipped"] is False, "失败不是跳过（重试语义的口径区分）"
    assert rep["ok"] is False, "VACUUM 失败必须拉低汇总 ok"
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, "vacuum_last_run") is None, \
            "失败不得推进周度游标（下轮重试）"
        assert db_mod.kv_get(c, "maint_last_run") is not None, \
            "日游标照常推进（归档已成功，崩溃安全语义）"

    # 外层 except 分支：_vacuum 抛异常（非返回 failed dict）同样拉低 ok
    def _raise(force, days, last, dry_run):
        raise RuntimeError("注入的独占异常")
    monkeypatch.setattr(maint, "_vacuum", _raise)
    rep2 = maint.run_maintenance(now=datetime.now())
    assert rep2["ok"] is False
    assert _step(rep2, "vacuum").get("failed")


def test_archive_failure_ok_false(monkeypatch):
    """归档步骤异常：汇总 ok=False + steps 带 failed 字段（杀失败分支
    ok=False→True 变异）。"""
    def _boom(c, **k):
        raise RuntimeError("注入的归档故障")
    monkeypatch.setattr(maint.archive_mod, "archive_cold", _boom)
    rep = maint.run_maintenance(now=datetime.now())
    arc = _step(rep, "archive_cold")
    assert arc.get("failed") == "注入的归档故障"
    assert rep["ok"] is False


# ---------------------------------------------------------------- ⑥ audit 汇总
def test_audit_system_summary():
    now = datetime.now()
    rep = maint.run_maintenance(now=now)
    assert rep["ok"] in (True, False)          # 汇总形状
    tail = audit_mod.tail(10, "system")
    ev = next(e for e in tail if "daily_maintenance" in e["detail_json"])
    d = json.loads(ev["detail_json"])
    assert d["action"] == "daily_maintenance" and "steps" in d


# ---------------------------------------------------------------- ⑥ admin 端点
async def test_admin_maintenance_endpoint(client):
    sid = "20261014_mnt-api"
    _mk_cold(sid)
    r = await client.post("/api/admin/maintenance", json={"force_vacuum": True})
    assert r.status_code == 200
    body = r.json()
    assert any(s["step"] == "archive_cold" for s in body["steps"])
    assert _step(body, "vacuum")["done"] is True
    with db_mod.conn() as c:
        assert arch.folded_count(c, sid) == 17
