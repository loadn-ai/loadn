"""rev10（会话内子任务）：subtasks 表 + turns.subtask_id 打标 + CRUD + 连坐删除。"""
from __future__ import annotations

from loadn_webui import db as db_mod


def _mk_conn():
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp(prefix="wd-rev10-"))
    db_mod.PATHS["root"] = tmp
    db_mod.PATHS["var"] = tmp / "var"
    db_mod.PATHS["db"] = tmp / "var" / "loadn.db"
    return db_mod.conn()


def test_rev10_migration_and_crud():
    with _mk_conn() as c:
        # 首连建 schema：subtasks 表 + turns.subtask_id 都在
        tables = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "subtasks" in tables
        tcols = {r["name"] for r in c.execute("PRAGMA table_info(turns)")}
        assert "subtask_id" in tcols
        assert db_mod.SCHEMA_REV == 10
        # 旧库路径：手工删列不可能（sqlite），但 ALTER 幂等可验——重跑 _migrate 不炸
        db_mod._migrate(c)  # noqa: SLF001

        c.execute("INSERT INTO sessions(id, title) VALUES('s1','测试')")
        sid1 = db_mod.create_subtask(c, "s1", "调研任务")
        sid2 = db_mod.create_subtask(c, "s1", "decting项目")
        assert sid1 != sid2
        titles = [r["title"] for r in db_mod.list_subtasks(c, "s1")]
        assert titles == ["调研任务", "decting项目"]      # 按 id 序（创建序）
        assert db_mod.find_subtask_by_title(c, "s1", "decting项目")["id"] == sid2
        assert db_mod.find_subtask_by_title(c, "s1", "不存在") is None
        db_mod.update_subtask(c, sid1, status="archived")
        assert db_mod.find_subtask_by_title(c, "s1", "调研任务")["status"] == "archived"


def test_delete_session_cascades_subtasks():
    """>>> 连坐对赌：purge 会话时 subtasks 一并删（漏了=悬挂行）。"""
    with _mk_conn() as c:
        c.execute("INSERT INTO sessions(id, title) VALUES('s1','连坐')")
        db_mod.create_subtask(c, "s1", "A")
        db_mod.create_subtask(c, "s1", "B")
        c.execute("INSERT INTO sessions(id, title) VALUES('s2','旁证')")
        db_mod.create_subtask(c, "s2", "C")
        db_mod.delete_session(c, "s1")
        left = [r["session_id"] for r in c.execute("SELECT session_id FROM subtasks")]
        assert left == ["s2"]


def test_turn_tagging_via_update_turn():
    """update_turn 泛型 kwargs 直接打标（turns.subtask_id 写读回路）。"""
    with _mk_conn() as c:
        c.execute("INSERT INTO sessions(id, title) VALUES('s1','打标')")
        tid = db_mod.create_turn(c, session_id="s1", status="queued")
        stid = db_mod.create_subtask(c, "s1", "调研任务")
        db_mod.update_turn(c, tid, subtask_id=stid)
        assert db_mod.get_turn(c, tid)["subtask_id"] == stid
        # 竞态路径：turn 行已删 → UPDATE rowcount=0（撤回后分类完成）
        db_mod.delete_turn(c, tid) if hasattr(db_mod, "delete_turn") else \
            c.execute("DELETE FROM turns WHERE id=?", (tid,))
        db_mod.update_turn(c, tid, subtask_id=stid)
        assert db_mod.get_turn(c, tid) is None
