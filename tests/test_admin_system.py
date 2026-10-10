"""系统页数据面 GET /api/admin/system + 备份触发端点（AC-4.1 MISS-1）。

对赌焦点：①触发即返回（分钟级备份不得挂死请求）②running 状态可见 +
重复触发 409 ③线程收敛后 rc/detail 落状态 ④备份清单 manifest 摘要
（半成品目录 complete=False）⑤敏感读分级（普通 cookie 用户 403——
全量清单见 test_w0_security.py）。
"""
from __future__ import annotations

import asyncio
import json
import threading


async def _wait_backup_idle(client, timeout_s: float = 5) -> dict:
    for _ in range(int(timeout_s / 0.1)):
        d = (await client.get("/api/admin/system")).json()
        if not d["backup"]["running"]:
            return d
        await asyncio.sleep(0.1)
    return d


async def test_system_overview_shape(client):
    d = (await client.get("/api/admin/system")).json()
    for k in ("version", "runtime", "scheduler", "backup"):
        assert k in d, f"缺 {k}"
    assert d["version"]["running"].get("version"), "运行版本必填（RELEASE.json/包版本兜底）"
    assert d["runtime"]["uptime_s"] is None or d["runtime"]["uptime_s"] >= 0
    assert "free_gb" in d["runtime"]["disk"] and "db_size_mb" in d["runtime"]
    assert d["scheduler"]["check_interval_s"] > 0
    assert isinstance(d["scheduler"]["jobs"], dict)
    assert isinstance(d["backup"]["recent"], list)


async def test_backup_run_background_and_409(client, monkeypatch):
    """>>> 触发即返回 + running 中重复触发 409 + 线程收敛 rc=0（全流程对赌）。"""
    from loadn_webui import backup as backup_mod
    calls: list[bool] = []
    ev_started, ev_release = threading.Event(), threading.Event()

    def fake_run(fw: bool = False) -> int:
        calls.append(fw)
        ev_started.set()
        assert ev_release.wait(timeout=5), "测试释放事件超时"
        return 0

    monkeypatch.setattr(backup_mod, "cmd_backup_run", fake_run)

    r = await client.post("/api/admin/system/backup",
                          json={"full_workspace": True})
    assert r.status_code == 200, r.text
    assert r.json()["started"] is True
    assert ev_started.wait(timeout=5), "后台线程未启动"
    # running 中：GET 可见 + 重复触发被拒
    d = (await client.get("/api/admin/system")).json()
    assert d["backup"]["running"] is True and d["backup"]["kind"] == "backup"
    r2 = await client.post("/api/admin/system/backup", json={})
    assert r2.status_code == 409
    # 放行 → 收敛 rc=0
    ev_release.set()
    d = await _wait_backup_idle(client)
    assert d["backup"]["running"] is False and d["backup"]["rc"] == 0
    assert calls == [True], "full_workspace 参数须透传"


async def test_backup_verify_detail_on_failure(client, monkeypatch):
    """>>> 验证失败 rc≠0 时 detail 捕获输出（前端可见原因）。"""
    from loadn_webui import backup as backup_mod

    def fake_verify() -> int:
        print("✗ 无备份可验证", flush=True)
        return 1

    monkeypatch.setattr(backup_mod, "cmd_backup_verify", fake_verify)
    r = await client.post("/api/admin/system/backup/verify")
    assert r.status_code == 200 and r.json()["started"] is True
    d = await _wait_backup_idle(client)
    assert d["backup"]["rc"] == 1
    assert "无备份" in d["backup"]["detail"]


async def test_backup_listing_manifest_summary(client, monkeypatch, tmp_path):
    from loadn_webui import backup as backup_mod
    monkeypatch.setattr(backup_mod, "BACKUP_ROOT", tmp_path)
    full = tmp_path / "20260101-000000"
    full.mkdir()
    (full / "manifest.json").write_text(json.dumps(
        {"timestamp": "2026-01-01T00:00:00+00:00",
         "full_workspace": True, "errors": []}))
    half = tmp_path / "20260102-000000"     # 备份中断的半成品（无 manifest）
    half.mkdir()
    d = (await client.get("/api/admin/system")).json()
    rec = {b["name"]: b for b in d["backup"]["recent"]}
    assert rec["20260101-000000"]["full_workspace"] is True
    assert rec["20260101-000000"]["complete"] is True
    assert rec["20260101-000000"]["errors"] == []
    assert rec["20260102-000000"]["complete"] is False
    # 倒序：最新目录在前
    assert d["backup"]["recent"][0]["name"] == "20260102-000000"
