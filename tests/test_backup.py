"""T2：backup.py 全单测（原 0% 覆盖——数据安全最高风险面）。

- run：关键文件拷贝（0600）/sqlite backup API 一致性/manifest 落盘
  /缺文件跳过不报错；workspace 排除项（node_modules 排、正文留；
  full 档带 node_modules）
- verify：完整备份过；缺 DB/缺 manifest/备份时 errors → rc 1 列问题；
  无备份 rc 1；指定 date 验旧档
- restore：roundtrip（config/db/workspace 全回来+权限）；非 yes 取消
  不动数据
- 保留期清理：>30 天删、新留
- list：名字/大小/档位/错误数
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from loadn_webui import backup as bk
from loadn_webui.config import PATHS


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    """数据根与备份根全隔离（PATHS dict 项 + 模块常量）。"""
    root = tmp_path / "root"
    (root / "var").mkdir(parents=True)
    monkeypatch.setitem(PATHS, "root", root)
    monkeypatch.setattr(bk, "BACKUP_ROOT", tmp_path / "backup")
    return root


def _mk_db(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT)")
    conn.executemany("INSERT INTO sessions VALUES (?)",
                     [(f"s-{i}",) for i in range(rows)])
    conn.commit()
    conn.close()


def _seed(root: Path, ws_file: str = "work/a.txt") -> None:
    (root / "config.yaml").write_text("server: {}\n", encoding="utf-8")
    (root / "var" / "server_token").write_text("tok", encoding="utf-8")
    (root / "var" / "vault.enc").write_bytes(b"LDV1xxxx")
    (root / "var" / "audit.db").write_bytes(b"audit")
    _mk_db(root / "var" / "loadn.db", rows=3)
    f = root / "workspace" / ws_file
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("data", encoding="utf-8")


# ---------------------------------------------------------------- run
def test_run_copies_critical_and_db(tmp_path, _iso, capsys):
    _seed(_iso)
    rc = bk.cmd_backup_run()
    assert rc == 0, capsys.readouterr().err
    dirs = sorted((tmp_path / "backup").iterdir())
    assert len(dirs) == 1
    dst = dirs[0]
    assert (dst / "config.yaml").read_text() == "server: {}\n"
    # 0600 纪律（备份面同凭证面权限）
    for rel in ("var/server_token", "var/vault.enc", "var/audit.db",
                "var/loadn.db"):
        p = dst / rel
        assert p.exists(), rel
        assert oct(p.stat().st_mode & 0o777) == "0o600", rel
    # DB 经 backup API 复制后可读且行数一致
    conn = sqlite3.connect(f"file:{dst / 'var' / 'loadn.db'}?mode=ro",
                           uri=True)
    n = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    conn.close()
    assert n == 3
    m = json.loads((dst / "manifest.json").read_text())
    assert m["errors"] == [] and m["data_root"].endswith("root")


def test_run_empty_root_no_errors(_iso, capsys):
    """数据根缺文件=跳过（不误报）；manifest 仍落。"""
    rc = bk.cmd_backup_run()
    assert rc == 0
    dirs = sorted(bk.BACKUP_ROOT.iterdir())
    m = json.loads((dirs[0] / "manifest.json").read_text())
    assert m["errors"] == []


def test_run_wal_db_consistency(_iso):
    """WAL 模式下 backup API 拷贝仍一致（不止 file copy 的关键时刻）。"""
    p = _iso / "var" / "loadn.db"
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sessions (id TEXT)")
    conn.execute("INSERT INTO sessions VALUES ('w1')")
    conn.execute("INSERT INTO sessions VALUES ('w2')")
    conn.commit()                                        # 已提交但未 checkpoint
    assert bk.cmd_backup_run() == 0
    dst = next(bk.BACKUP_ROOT.iterdir())
    c2 = sqlite3.connect(f"file:{dst / 'var' / 'loadn.db'}?mode=ro", uri=True)
    assert c2.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    c2.close()
    conn.close()


@pytest.mark.skipif(not shutil.which("rsync"), reason="rsync 缺席")
def test_workspace_excludes(_iso):
    _seed(_iso)
    nm = _iso / "workspace" / "proj" / "node_modules" / "big.bin"
    nm.parent.mkdir(parents=True)
    nm.write_bytes(b"x" * 100)
    gt = _iso / "workspace" / "proj" / ".git"
    gt.mkdir()
    (gt / "HEAD").write_text("ref", encoding="utf-8")
    assert bk.cmd_backup_run() == 0
    dst = next(bk.BACKUP_ROOT.iterdir())
    assert (dst / "workspace" / "work" / "a.txt").exists()
    assert not (dst / "workspace" / "proj" / "node_modules").exists()
    assert not (dst / "workspace" / "proj" / ".git").exists()
    # full 档：.git/chrome* 等带上（node_modules 恒排——可重建物永不备份）
    assert bk.cmd_backup_run(full_workspace=True) == 0
    dst2 = sorted(bk.BACKUP_ROOT.iterdir())[-1]
    assert dst2 != dst                                     # 同秒也不混目录
    assert (dst2 / "workspace" / "proj" / ".git" / "HEAD").exists()
    assert not (dst2 / "workspace" / "proj" / "node_modules").exists()


def test_run_same_second_new_dir(_iso):
    """备份不可变（T2 bug 修）：同秒重入换名，不混写完成品。"""
    _seed(_iso)
    assert bk.cmd_backup_run() == 0
    assert bk.cmd_backup_run() == 0                        # 同秒第二次
    dirs = sorted(bk.BACKUP_ROOT.iterdir())
    assert len(dirs) == 2                                  # 两个独立目录
    first, second = dirs
    assert first.name != second.name
    # 两次都有完整 manifest（第二次不是半成品）
    for d in dirs:
        assert json.loads((d / "manifest.json").read_text())["errors"] == []


# ---------------------------------------------------------------- list
def test_list(_iso, capsys):
    assert bk.cmd_backup_list() == 0                   # 无根目录
    assert "无备份" in capsys.readouterr().out
    _seed(_iso)
    bk.cmd_backup_run()
    (bk.BACKUP_ROOT / "19990101-000000").mkdir()
    (bk.BACKUP_ROOT / "19990101-000000" / "manifest.json").write_text(
        json.dumps({"full_workspace": True, "errors": ["x"]}), encoding="utf-8")
    assert bk.cmd_backup_list() == 0
    out = capsys.readouterr().out
    assert "full" in out and "select" in out            # 两档都列出


# ---------------------------------------------------------------- verify
def test_verify_ok_recent(_iso, capsys):
    _seed(_iso)
    bk.cmd_backup_run()
    assert bk.cmd_backup_verify() == 0
    assert "完整" in capsys.readouterr().out


def test_verify_problems(_iso, capsys):
    d = bk.BACKUP_ROOT / "20260101-000000"
    d.mkdir(parents=True)
    # 空目录：缺关键文件+缺 DB+缺 manifest
    assert bk.cmd_backup_verify("20260101-000000") == 1
    out = capsys.readouterr().out
    assert "缺" in out and "manifest" in out


def test_verify_manifest_errors_flagged(_iso, capsys):
    d = bk.BACKUP_ROOT / "20260102-000000"
    (d / "var").mkdir(parents=True)
    _mk_db(d / "var" / "loadn.db")
    for rel in ("config.yaml", "var/server_token", "var/vault.enc",
                "var/audit.db"):
        (d / rel).touch()
    (d / "manifest.json").write_text(
        json.dumps({"errors": ["workspace rsync: boom"]}), encoding="utf-8")
    assert bk.cmd_backup_verify("20260102-000000") == 1
    assert "workspace rsync" in capsys.readouterr().out


def test_verify_no_backup(_iso, capsys):
    bk.BACKUP_ROOT.mkdir(parents=True)
    assert bk.cmd_backup_verify() == 1
    assert "无备份" in capsys.readouterr().err


# ---------------------------------------------------------------- restore
def test_restore_roundtrip(_iso, monkeypatch, capsys):
    _seed(_iso)
    bk.cmd_backup_run()
    src = next(bk.BACKUP_ROOT.iterdir())
    # 改坏数据根：删 db/config，workspace 塞垃圾
    (_iso / "var" / "loadn.db").unlink()
    (_iso / "config.yaml").unlink()
    junk = _iso / "workspace" / "junk.txt"
    junk.write_text("junk", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert bk.cmd_backup_restore(src.name) == 0
    assert (_iso / "config.yaml").exists()             # 关键文件回来
    assert oct((_iso / "var" / "vault.enc").stat().st_mode & 0o777) == "0o600"
    conn = sqlite3.connect(str(_iso / "var" / "loadn.db"))
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 3
    conn.close()
    assert not junk.exists()                           # --delete 语义（垃圾清）


def test_restore_confirm_refused(_iso, monkeypatch):
    _seed(_iso)
    bk.cmd_backup_run()
    src = next(bk.BACKUP_ROOT.iterdir())
    (_iso / "config.yaml").unlink()
    monkeypatch.setattr("builtins.input", lambda: "no")
    assert bk.cmd_backup_restore(src.name) == 1
    assert not (_iso / "config.yaml").exists()         # 取消=零副作用


def test_restore_missing_date(_iso, capsys):
    assert bk.cmd_backup_restore("20200101-000000") == 1
    assert "不存在" in capsys.readouterr().err


# ---------------------------------------------------------------- 清理
def test_cleanup_old(_iso):
    old = bk.BACKUP_ROOT / "20200101-000000"
    new = bk.BACKUP_ROOT / "20990101-000000"
    for d in (old, new):
        d.mkdir(parents=True)
    # mtime 设为 40 天前（>RETENTION_DAYS）
    old_ts = __import__("time").time() - 40 * 86400
    os.utime(old, (old_ts, old_ts))
    bk._cleanup_old()
    assert not old.exists() and new.exists()


def test_backup_dir_form():
    assert bk._backup_dir("20260101-120000") == bk.BACKUP_ROOT / "20260101-120000"
    assert bk._backup_dir().parent == bk.BACKUP_ROOT


def test_r3_restore_rejects_incomplete_and_locked(monkeypatch, tmp_path):
    """三轮修（backlog 清）对赌：restore 三守卫——①源缺 manifest（半成品）
    拒；②主库 WAL 非空（服务在跑）拒；③预备份失败中止（当前状态不动）。"""
    import loadn_webui.backup as bk
    from loadn_webui.config import PATHS
    # 隔离数据根与备份根
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setitem(PATHS, "root", root)
    monkeypatch.setattr(bk, "BACKUP_ROOT", tmp_path / "bk")
    # ① 半成品源（无 manifest）
    half = tmp_path / "bk" / "half"
    half.mkdir(parents=True)
    (half / "var").mkdir(parents=True)
    (half / "var" / "loadn.db").write_bytes(b"sqlite-truncated")
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert bk.cmd_backup_restore("half") == 1
    # ② WAL 非空（活库）
    (root / "var").mkdir(parents=True, exist_ok=True)
    (root / "var" / "loadn.db-wal").write_bytes(b"x" * 32)
    full = tmp_path / "bk" / "full"
    full.mkdir(parents=True)
    (full / "manifest.json").write_text("{}")
    assert bk.cmd_backup_restore("full") == 1
    # ③ 预备份失败 → 中止（当前状态零改动）
    (root / "var" / "loadn.db-wal").unlink()
    monkeypatch.setattr(bk, "cmd_backup_run", lambda *a, **k: 1)
    assert bk.cmd_backup_restore("full") == 1
    assert not (root / "var" / "loadn.db").exists(), "失败中止：不得落任何恢复"


def test_r13_refresh_default_assets(tmp_path, monkeypatch):
    """十三轮对赌（生产实证：v0.7.2 执行环境节被数据根 v0.6.x 旧拷贝
    遮蔽）：refresh_default_assets——与历史 release 原版相同的未定制
    拷贝被新版覆盖；用户定制版（与全部历史版不同）原样保留。"""
    import loadn_webui.ops as ops

    rels = tmp_path / "releases"
    # 历史 release vOld：旧模板；新 release vNew：新模板
    old_dir = rels / "vOld" / "prompts"
    old_dir.mkdir(parents=True)
    (old_dir / "workspace.md.tmpl").write_text("旧模板内容", encoding="utf-8")
    new_root = rels / "vNew"
    (new_root / "prompts").mkdir(parents=True)
    (new_root / "prompts" / "workspace.md.tmpl").write_text(
        "新模板内容+执行环境节", encoding="utf-8")
    # 数据根：一份旧拷贝（未定制）+ 一份定制（与历史都不同）
    data = tmp_path / "data" / "prompts"
    data.mkdir(parents=True)
    (data / "workspace.md.tmpl").write_text("旧模板内容", encoding="utf-8")
    (data / "other.md.tmpl").write_text("用户定制版", encoding="utf-8")
    (rels / "vOld" / "prompts" / "other.md.tmpl").write_text(
        "旧 other", encoding="utf-8")
    import loadn_webui.config as cfg_mod
    monkeypatch.setattr(ops, "RELEASES_DIR", rels)
    monkeypatch.setattr(cfg_mod, "PATHS", type("P", (), {
        "root": tmp_path / "data"}))

    n = ops.refresh_default_assets(new_root)
    # 只刷新「新版有同名文件」的未定制拷贝；other 在新版已不存在→不动
    assert n == 1
    assert (data / "workspace.md.tmpl").read_text(
        encoding="utf-8") == "新模板内容+执行环境节"
    assert (data / "other.md.tmpl").read_text(
        encoding="utf-8") == "用户定制版"
