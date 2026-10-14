"""PERF-4：文件层 retention + 一次性清理——年龄边界/目录零接触/
orphans 守卫（loadn.db 永不在清单+state.db 仅 0 字节）/dry_run 默认值/
audit/概览形状。零 token。"""
from __future__ import annotations

import os
import time
from datetime import datetime

import pytest

from loadn_webui import db as db_mod
from loadn_webui import maintenance as maint
from loadn_webui.config import PATHS
from loadn_webui.security import audit as audit_mod

from .test_perf_sessions import _mk_session

pytestmark = [pytest.mark.coverage("perf.files")]


def _touch(path, age_s: float, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (time.time() - age_s, time.time() - age_s))


def _step(rep: dict, name: str) -> dict:
    return next(s for s in rep["steps"] if s["step"] == name)


# ---------------------------------------------------------------- ① 滚动守卫
def test_sweep_age_boundary_and_dirs_untouched():
    """30 天+1s 必删、窗口内不删、目录永不删（写死天数防自引用漂移）。"""
    cl = PATHS["call_logs"]
    old = cl / "turn1_000.out"
    new = cl / "turn2_000.out"
    subdir = cl / "subdir"
    _touch(old, 30 * 86400 + 1)
    _touch(new, 3600)
    _touch(subdir / "keep.out", 30 * 86400 + 1)   # 超龄但在目录里
    gone = maint._sweep_files(cl, 30)
    assert gone["files"] == 1, "只有超龄的顶层文件被删"
    assert not old.exists() and new.exists()
    assert subdir.exists(), "目录永不删"
    assert (subdir / "keep.out").exists(), "glob 单层不递归"


def test_sweep_missing_root_zero():
    assert maint._sweep_files(PATHS["call_logs"] / "nope", 30) == {
        "files": 0, "bytes": 0}


# ---------------------------------------------------------------- ② 维护挂载
async def test_maintenance_sweeps_wired(client):
    """run_maintenance 三 sweep 步骤在位：真跑删超龄 call log + audit 汇总。"""
    old = PATHS["call_logs"] / "turn9_000.err"
    _touch(old, 31 * 86400)
    rep = maint.run_maintenance(now=datetime.now())
    s = _step(rep, "sweep_call_logs")
    assert s["files"] >= 1
    assert not old.exists()
    for name in ("sweep_pages_cache", "sweep_backup_files"):
        assert any(x["step"] == name for x in rep["steps"])
    tail = audit_mod.tail(10, "system")
    assert any("daily_maintenance" in e["detail_json"] for e in tail)
    # dry_run：sweep 全部跳过（不删窗口内文件）
    fresh = PATHS["call_logs"] / "turn8_000.out"
    _touch(fresh, 60)
    rep2 = maint.run_maintenance(dry_run=True, now=datetime.now())
    assert _step(rep2, "sweep_call_logs")["skipped"] is True
    assert fresh.exists()


def test_backup_files_sweep_keeps_dirs():
    """var/backups 散文件（config.yaml.*）按天清，备份目录原样。"""
    bk = PATHS["backups"]
    stale = bk / "config.yaml.20260902T000000+0000"
    _touch(stale, 31 * 86400)
    keep_dir = bk / "full-20261014"
    _touch(keep_dir / "manifest.json", 31 * 86400)   # 目录内超龄文件也不动
    gone = maint._sweep_files(bk, 30)
    assert gone["files"] >= 1
    assert not stale.exists()
    assert keep_dir.exists() and (keep_dir / "manifest.json").exists()


# ---------------------------------------------------------------- ③ orphans 守卫
def test_cleanup_orphans_guards():
    var = PATHS["var"]
    (var / "workdaddy.db").write_bytes(b"legacy")
    (var / "workdaddy.db-wal").write_bytes(b"w")
    (var / "workdaddy.db.bak.x").write_bytes(b"b")
    (var / "state.db").write_bytes(b"")              # 0 字节 → 可清
    (var / "state.db.real").write_bytes(b"data")     # 非空 → 不进清单

    r = maint.cleanup_orphans(dry_run=True)
    assert r["removed"] == 0 and len(r["candidates"]) == 4
    assert (var / "workdaddy.db").exists(), "dry_run 零副作用"

    r2 = maint.cleanup_orphans(dry_run=False)
    assert r2["removed"] == 4
    assert not (var / "workdaddy.db").exists()
    assert not (var / "state.db").exists()
    assert (var / "state.db.real").exists(), "非空 state.db 名单都不进"
    assert PATHS["db"].exists(), "主库永不在清理清单"
    # 非空 state.db 补验：改名为 state.db 后不再可清
    (var / "state.db").write_bytes(b"now-in-use")
    r3 = maint.cleanup_orphans(dry_run=True)
    assert not any(p.endswith("state.db") for p in
                   (c["file"] for c in r3["candidates"])), \
        "非空 state.db 不得进候选（守卫反转可杀）"


async def test_cleanup_orphans_endpoint_default_dry(client):
    """端点默认 dry_run；显式 false 真删（audit 留痕）。"""
    var = PATHS["var"]
    (var / "workdaddy.db").write_bytes(b"legacy")
    r = await client.post("/api/admin/cleanup/orphans", json={})
    assert r.json()["dry_run"] is True
    assert (var / "workdaddy.db").exists()
    r2 = await client.post("/api/admin/cleanup/orphans",
                           json={"dry_run": False})
    assert r2.json()["removed"] == 1
    assert not (var / "workdaddy.db").exists()
    assert any("cleanup_orphans" in e["detail_json"]
               for e in audit_mod.tail(10, "system"))


# ---------------------------------------------------------------- ④ 概览
async def test_storage_overview(client):
    _mk_session("20261015_ov-sess", "2000-01-01T00:00:00+00:00")
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET size_bytes=? WHERE id=?",
                  (1 << 40, "20261015_ov-sess"))
    r = await client.get("/api/admin/storage/overview")
    assert r.status_code == 200
    body = r.json()
    assert set(body["trees"]) >= {"call_logs", "pages_cache", "backups",
                                  "archive", "db"}
    top = body["workspace_top"]
    assert top and top[0]["sid"] == "20261015_ov-sess"
    assert top[0]["size_bytes"] == 1 << 40
