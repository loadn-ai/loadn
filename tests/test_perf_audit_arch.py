"""PERF-5：audit 冻结月表导出归档——当月表不导（守卫）/冻结表往返
逐字段一致/幂等 skip/dry_run 零副作用/审计留痕/维护挂载+端点。零 token。"""
from __future__ import annotations

import gzip
import json

import pytest

from loadn_webui import maintenance as maint
from loadn_webui.security import audit as audit_mod

pytestmark = [pytest.mark.coverage("perf.audit_arch")]


FROZEN = "audit_events_202601"      # 写死冻结月（2026-01 远早于当月）
_OTHER = "audit_events_202602"


@pytest.fixture(autouse=True)
def _cleanup_frozen_tables():
    """自清理（关键）：造的冻结表带**不自洽的假哈希链行**（导出不管链
    语义），而 audit.db 是 session 级共享——残留会让后跑的链验证测试
    （test_perf_gate 的 verify 预算等）断链误挂。表+归档文件都清。"""
    yield
    with audit_mod._conn() as c:
        for t in (FROZEN, _OTHER):
            c.execute(f"DROP TABLE IF EXISTS {t}")
    for t in (FROZEN, _OTHER):
        p = _dest(t)
        if p.exists():
            p.unlink()


def _seed_frozen(n: int = 5) -> None:
    """直插冻结月表（绕过 _table_for 的当月路由）——行内容真实哈希链
    不必自洽（导出不管链语义，verify 有专项测试）。"""
    with audit_mod._conn() as c:
        audit_mod._ensure_table(c, FROZEN)
        for i in range(n):
            c.execute(
                f"INSERT INTO {FROZEN}(ts, sid, turn_id, type, detail_json,"
                " prev_hash, hash) VALUES(?,?,?,?,?,?,?)",
                (f"2026-01-{i + 10:02d}T00:00:00+00:00", None, None,
                 "system", json.dumps({"i": i}), "0" * 64, "f" * 64))


def _rows_in(table: str) -> list[dict]:
    with audit_mod._conn() as c:
        return [{k: r[k] for k in ("id", "ts", "sid", "turn_id", "type",
                                   "detail_json", "prev_hash", "hash")}
                for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]


def _dest(table: str):
    return audit_mod._archive_dir() / f"{table}.jsonl.gz"


# ---------------------------------------------------------------- ① 当月守卫
def test_current_month_never_exported():
    """当月表正在写——导出=竞态丢行。守卫反转本用例即杀。"""
    audit_mod.audit("system", {"probe": "current-month"})
    r = audit_mod.archive_months()
    tables = [x["table"] for x in r["results"]]
    assert audit_mod._table_for(__import__("loadn_webui.util", fromlist=["iso"]).iso()) \
        not in tables


# ---------------------------------------------------------------- ② 冻结表往返
def test_frozen_month_roundtrip():
    _seed_frozen(5)
    before = _rows_in(FROZEN)
    assert before
    r = audit_mod.archive_months()
    out = next(x for x in r["results"] if x["table"] == FROZEN)
    assert out["rows"] == len(before) and len(out["sha256"]) == 16
    dest = _dest(FROZEN)
    assert dest.exists()
    with gzip.open(dest, "rt", encoding="utf-8") as f:
        got = [json.loads(line) for line in f if line.strip()]
    assert got == before, "导出必须逐字段一致（含 prev_hash/hash 可离线验链）"
    # 账本本体不动（只导不删——DROP 断链，另卡）
    assert _rows_in(FROZEN) == before
    # 幂等：目标已存在即 skip
    r2 = audit_mod.archive_months()
    assert not any(x["table"] == FROZEN for x in r2["results"])
    # 审计留痕
    assert any("audit_month_export" in e["detail_json"]
               for e in audit_mod.tail(20, "archive"))


# ---------------------------------------------------------------- ③ dry_run
def test_dry_run_zero_side_effects():
    other = _OTHER
    with audit_mod._conn() as c:
        audit_mod._ensure_table(c, other)
        c.execute(
            f"INSERT INTO {other}(ts, sid, turn_id, type, detail_json,"
            " prev_hash, hash) VALUES('2026-02-01T00:00:00+00:00', NULL,"
            " NULL, 'system', '{}', '0', '0')")
    r = audit_mod.archive_months(dry_run=True)
    entry = next(x for x in r["results"] if x["table"] == other)
    assert entry["dry_run"] is True
    assert not _dest(other).exists(), "dry_run 不得落文件"
    assert _rows_in(other), "dry_run 不得动账本"


# ---------------------------------------------------------------- ④ 维护挂载+端点
async def test_maintenance_and_endpoint(client):
    r = await client.post("/api/admin/audit/export", json={})
    assert r.status_code == 200
    assert "results" in r.json()
    rep = maint.run_maintenance(now=__import__("datetime").datetime.now())
    step = next(s for s in rep["steps"] if s["step"] == "audit_export")
    assert "tables" in step
