"""R7 发布系统单元测试（tmp_path 假部署根+假数据根，绝不碰生产）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from loadn_webui import ops


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    """假部署根+假数据根（ops 全部路径 env 覆盖）。"""
    deploy = tmp_path / "deploy"
    data = tmp_path / "data"
    for d in ("releases", "wheelhouse", "state", "logs"):
        (deploy / d).mkdir(parents=True)
    (data / "var").mkdir(parents=True)
    # 最小数据根（preflight 要求）
    (data / "config.yaml").write_text("# test\n")
    (data / "var" / "loadn.db").write_bytes(b"")
    (data / "var" / "server_token").write_text("test-token")

    monkeypatch.setattr(ops, "DEPLOY_ROOT", deploy)
    monkeypatch.setattr(ops, "RELEASES_DIR", deploy / "releases")
    monkeypatch.setattr(ops, "WHEELHOUSE_DIR", deploy / "wheelhouse")
    monkeypatch.setattr(ops, "STATE_DIR", deploy / "state")
    monkeypatch.setattr(ops, "LOGS_DIR", deploy / "logs")
    monkeypatch.setattr(ops, "CURRENT_LINK", deploy / "current")
    monkeypatch.setattr(ops, "DEPLOY_JSON", deploy / "state" / "deploy.json")
    monkeypatch.setattr(ops, "OPS_LOCK", deploy / "state" / "ops.lock")
    monkeypatch.setattr(ops, "OPS_LOG", deploy / "logs" / "ops.log")
    monkeypatch.setattr(ops, "DATA_ROOT", data)
    return {"deploy": deploy, "data": data}


def _make_release(env: dict, version: str, schema_rev: int = 1,
                  broken: bool = False):
    d = env["deploy"] / "releases" / version
    for item in ops.RELEASE_LAYOUT:
        if item == "pyproject.toml":
            (d / item).write_text("[project]\nname='loadn'\n")
        else:
            (d / item).mkdir(parents=True, exist_ok=True)
    (d / "loadn_webui" / "ops.py").write_text("# stub")
    (d / ".venv" / "bin").mkdir(parents=True)
    if not broken:
        (d / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (d / "RELEASE.json").write_text(json.dumps({
        "version": version, "git_sha": "a" * 40, "tag": version,
        "built_at": "2026-09-23T00:00:00+00:00", "lock_hash": "abc12345",
        "schema_rev": schema_rev}))
    return d


# ---------------------------------------------------------------- versions

def test_versions_lists(fake_env):
    _make_release(fake_env, "v1.0.0")
    _make_release(fake_env, "v1.0.1")
    assert ops.cmd_versions() == 0
    # _list_releases 只收有 loadn_webui/ops.py 的目录
    assert ops._list_releases() == ["v1.0.0", "v1.0.1"]


def test_versions_empty(fake_env):
    assert ops.cmd_versions() == 0


# ---------------------------------------------------------------- 指针

def test_switch_current_atomic(fake_env):
    _make_release(fake_env, "v1.0.0")
    _make_release(fake_env, "v1.0.1")
    ops._switch_current("v1.0.0")
    assert ops._current_version() == "v1.0.0"
    ops._switch_current("v1.0.1")
    assert ops._current_version() == "v1.0.1"
    # tmp 文件不残留
    assert not (fake_env["deploy"] / ".current.tmp").exists()


# ---------------------------------------------------------------- deploy.json

def test_deploy_json_roundtrip(fake_env):
    dep = {"current": "v1.0.0", "previous": None, "history": []}
    ops._write_deploy_json(dep)
    assert ops._read_deploy_json() == dep


# ---------------------------------------------------------------- GC

def test_gc_protects_current_and_previous(fake_env):
    for v in ("v0.9.0", "v1.0.0", "v1.0.1", "v1.0.2"):
        _make_release(fake_env, v)
    ops._switch_current("v1.0.1")
    ops._write_deploy_json(
        {"current": "v1.0.1", "previous": "v1.0.0", "history": []})
    ops.cmd_gc(keep=2)     # 保留 2 个+current+previous
    remaining = ops._list_releases()
    assert "v1.0.1" in remaining       # current 不删
    assert "v1.0.0" in remaining       # previous 不删
    assert "v1.0.2" in remaining       # 最新不删（schema_rev 兜底）
    assert "v0.9.0" not in remaining   # 最旧的可删


def test_gc_protects_referenced_by_process(fake_env):
    _make_release(fake_env, "v0.9.0")
    _make_release(fake_env, "v1.0.0")
    ops._switch_current("v1.0.0")
    ops._write_deploy_json(
        {"current": "v1.0.0", "previous": None, "history": []})
    # 模拟有进程引用 v0.9.0
    ops._count_procs_referencing = lambda v: 5 if v == "v0.9.0" else 0
    ops.cmd_gc(keep=0)
    assert "v0.9.0" in ops._list_releases()   # 被引用不删


# ---------------------------------------------------------------- schema gate

def test_schema_gate_downgrade_warns(fake_env):
    _make_release(fake_env, "v1.0.0", schema_rev=1)
    # 模拟 DB rev=2
    import sqlite3
    db = fake_env["data"] / "var" / "loadn.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,"
                 " value TEXT)")
    conn.execute("INSERT INTO kv VALUES('schema_rev','2')")
    conn.commit(); conn.close()
    problems = ops._schema_gate("v1.0.0")
    assert problems and "降级" in problems[0]


def test_schema_gate_same_rev_no_problem(fake_env):
    _make_release(fake_env, "v1.0.0", schema_rev=1)
    import sqlite3
    db = fake_env["data"] / "var" / "loadn.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,"
                 " value TEXT)")
    conn.execute("INSERT INTO kv VALUES('schema_rev','1')")
    conn.commit(); conn.close()
    assert ops._schema_gate("v1.0.0") == []


# ---------------------------------------------------------------- health

def test_healthcheck_unreachable(fake_env, monkeypatch):
    monkeypatch.setattr(ops, "HEALTH_URL", "http://127.0.0.1:1/api/health")
    assert not ops._healthcheck(1)


# ---------------------------------------------------------------- preflight

def test_preflight_missing_venv(fake_env):
    d = _make_release(fake_env, "v1.0.0", broken=True)
    problems = ops._preflight(d)
    assert any("venv" in p for p in problems)


def test_preflight_venv_import_fails(fake_env, monkeypatch):
    """venv 存在但 import 失败（假 python 文件→mock subprocess）"""
    import types
    d = _make_release(fake_env, "v1.0.0")
    mock_result = types.SimpleNamespace(returncode=1, stderr="ModuleNotFoundError")
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: mock_result)
    problems = ops._preflight(d)
    assert any("import 失败" in p for p in problems)


def test_preflight_missing_data(fake_env, monkeypatch):
    import types
    d = _make_release(fake_env, "v1.0.0")
    mock_result = types.SimpleNamespace(returncode=0, stderr="")
    monkeypatch.setattr(ops.subprocess, "run", lambda *a, **k: mock_result)
    monkeypatch.setattr(ops, "DATA_ROOT", fake_env["data"] / "nonexistent")
    problems = ops._preflight(d)
    assert any("数据根" in p for p in problems)


def test_backup_retention_version_downgrade(fake_env, monkeypatch):
    """备份保留：版本回退（1.x→0.x）时刚写的备份不许被文件名排序自删。"""
    import time as _time
    bk = fake_env["data"] / "var" / "backups"
    bk.mkdir(parents=True)
    # 预置 3 份「更新」的旧备份（mtime 更晚，但文件名排序在 v0.3.0 之后）
    for i, v in enumerate(("v1.0.0", "v1.0.2", "v1.0.3")):
        f = bk / f"db-pre-{v}.db"
        f.write_bytes(b"x" * 8)
        stamp = _time.time() + 100 + i   # mtime 在未来也无所谓，只要有序
        import os as _os
        _os.utime(f, (stamp, stamp))
    ops._backup_db("v0.3.0")
    assert (bk / "db-pre-v0.3.0.db").exists(), "新备份被保留了"
    assert len(list(bk.glob("db-pre-*.db"))) == 3, "总量仍是 3 份"
