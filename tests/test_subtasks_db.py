"""rev10（会话内子任务）：subtasks 表 + turns.subtask_id 打标 + CRUD + 连坐删除。

隔离纪律：db PATHS 必须用 monkeypatch.setattr **替换引用**（test_artifacts_units
先例）——原地改 dict 会污染 config.PATHS 同一对象，整个 app 被带偏到临时
目录（全量跑 approvals 表消失/token 文件找不到的串扰实证）。
"""
from __future__ import annotations

import pytest

from loadn_webui import db as db_mod


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "PATHS", {"root": tmp_path,
                                          "var": tmp_path / "var",
                                          "db": tmp_path / "var" / "loadn.db"})
    with db_mod.conn() as c:
        yield c


def test_rev10_migration_and_crud(conn):
    # 首连建 schema：subtasks 表 + turns.subtask_id 都在
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "subtasks" in tables
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(turns)")}
    assert "subtask_id" in tcols
    assert db_mod.SCHEMA_REV == 11               # rev10 建 subtasks；rev11 加 projects.repo
    # 旧库路径：ALTER 幂等可验——重跑 _migrate 不炸
    db_mod._migrate(conn)  # noqa: SLF001

    conn.execute("INSERT INTO sessions(id, title) VALUES('s1','测试')")
    sid1 = db_mod.create_subtask(conn, "s1", "调研任务")
    sid2 = db_mod.create_subtask(conn, "s1", "decting项目")
    assert sid1 != sid2
    titles = [r["title"] for r in db_mod.list_subtasks(conn, "s1")]
    assert titles == ["调研任务", "decting项目"]      # 按 id 序（创建序）
    assert db_mod.find_subtask_by_title(conn, "s1", "decting项目")["id"] == sid2
    assert db_mod.find_subtask_by_title(conn, "s1", "不存在") is None
    db_mod.update_subtask(conn, sid1, status="archived")
    assert db_mod.find_subtask_by_title(conn, "s1", "调研任务")["status"] == "archived"


def test_delete_session_cascades_subtasks(conn):
    """>>> 连坐对赌：purge 会话时 subtasks 一并删（漏了=悬挂行）。"""
    conn.execute("INSERT INTO sessions(id, title) VALUES('s1','连坐')")
    db_mod.create_subtask(conn, "s1", "A")
    db_mod.create_subtask(conn, "s1", "B")
    conn.execute("INSERT INTO sessions(id, title) VALUES('s2','旁证')")
    db_mod.create_subtask(conn, "s2", "C")
    db_mod.delete_session(conn, "s1")
    left = [r["session_id"] for r in conn.execute("SELECT session_id FROM subtasks")]
    assert left == ["s2"]


def test_turn_tagging_via_update_turn(conn):
    """update_turn 泛型 kwargs 直接打标（turns.subtask_id 写读回路）。"""
    conn.execute("INSERT INTO sessions(id, title) VALUES('s1','打标')")
    tid = db_mod.create_turn(conn, session_id="s1", status="queued")
    stid = db_mod.create_subtask(conn, "s1", "调研任务")
    assert db_mod.update_turn(conn, tid, subtask_id=stid) == 1
    assert db_mod.get_turn(conn, tid)["subtask_id"] == stid
    # 竞态路径：turn 行已删 → UPDATE rowcount=0（撤回后分类完成）
    conn.execute("DELETE FROM turns WHERE id=?", (tid,))
    assert db_mod.update_turn(conn, tid, subtask_id=stid) == 0
    assert db_mod.get_turn(conn, tid) is None
