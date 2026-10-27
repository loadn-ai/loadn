"""db rev19（品牌统一）：引擎旧名 hahaness → loadn 全量数据迁移对赌。

三处 engine 字段 + engine_session_ids JSON 键重写（loadn 键已存在时保
loadn 值不覆盖）+ 幂等（重开连接重跑迁移零变化不炸）。
"""
import json

import pytest

from loadn_webui import db as db_mod


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """PATHS.db 指向 tmp 库（全新建库 + 控制短路缓存模拟重启）。"""
    dbfile = tmp_path / "loadn.db"
    monkeypatch.setitem(db_mod.PATHS, "db", dbfile)
    return dbfile


def _reopen(dbfile) -> None:
    """模拟进程重启：清进程内短路缓存后重连（重跑 SCHEMA+_migrate）。"""
    db_mod._SCHEMA_READY.clear()
    with db_mod.conn():
        pass


def test_rev19_engine_fields_and_json_keys(fresh_db):
    # ① 首连建全新库
    with db_mod.conn() as c:
        for sid, m in (("s-hh-only", {"claude": "c1", "hahaness": "h1"}),
                       ("s-both", {"claude": "c2", "hahaness": "h2",
                                   "loadn": "l2"}),
                       ("s-clean", {"loadn": "l3"})):
            db_mod.create_session(c, id=sid, engine_session_ids=json.dumps(m))
        # hahaness 形态：engine / engine_override / turns.engine
        c.execute("UPDATE sessions SET engine='hahaness' WHERE id='s-hh-only'")
        c.execute("UPDATE sessions SET engine_override='hahaness'"
                  " WHERE id='s-hh-only'")
        c.execute("UPDATE sessions SET engine='claude' WHERE id='s-both'")
        db_mod.create_turn(c, session_id="s-hh-only", engine="hahaness")
        db_mod.create_turn(c, session_id="s-both", engine="loadn")
        c.execute("INSERT INTO scheduled_jobs(session_id, kind, title, engine)"
                  " VALUES('s-hh-only', 'message', '每晚比赛', 'hahaness')")

    # ② 模拟升级重启 → _migrate 重跑
    _reopen(fresh_db)

    with db_mod.conn() as c:
        # engine / engine_override → loadn；干净行不受扰
        assert c.execute("SELECT COUNT(*) FROM sessions"
                         " WHERE engine='hahaness'").fetchone()[0] == 0
        assert c.execute("SELECT engine, engine_override FROM sessions"
                         " WHERE id='s-hh-only'").fetchone()[:] == ("loadn",
                                                                   "loadn")
        assert c.execute("SELECT engine FROM sessions"
                         " WHERE id='s-both'").fetchone()[0] == "claude"
        # turns / scheduled_jobs 同迁
        assert c.execute("SELECT engine FROM turns WHERE session_id='s-hh-only'"
                         ).fetchone()[0] == "loadn"
        assert c.execute("SELECT engine FROM scheduled_jobs"
                         " WHERE session_id='s-hh-only'").fetchone()[0] == "loadn"
        # engine_session_ids JSON 键重写：
        #   hahaness-only → loadn 值原样（h1）；both → 保 loadn 值（l2）；
        #   clean → 原样（l3）
        m1 = json.loads(c.execute("SELECT engine_session_ids FROM sessions"
                                  " WHERE id='s-hh-only'").fetchone()[0])
        assert m1 == {"claude": "c1", "loadn": "h1"}
        m2 = json.loads(c.execute("SELECT engine_session_ids FROM sessions"
                                  " WHERE id='s-both'").fetchone()[0])
        assert m2 == {"claude": "c2", "loadn": "l2"}
        m3 = json.loads(c.execute("SELECT engine_session_ids FROM sessions"
                                  " WHERE id='s-clean'").fetchone()[0])
        assert m3 == {"loadn": "l3"}

        # ③ 幂等：再重启一次零变化
        _reopen(fresh_db)
        m1b = json.loads(c.execute("SELECT engine_session_ids FROM sessions"
                                   " WHERE id='s-hh-only'").fetchone()[0])
        assert m1b == m1
        assert c.execute("SELECT COUNT(*) FROM sessions"
                         " WHERE engine='hahaness'").fetchone()[0] == 0


def test_rev19_non_json_mapping_untouched(fresh_db):
    """坏数据容错：engine_session_ids 非 JSON → 迁移跳过不炸。"""
    with db_mod.conn() as c:
        db_mod.create_session(c, id="s-bad",
                              engine="hahaness",
                              engine_session_ids="{not-json")
    _reopen(fresh_db)
    with db_mod.conn() as c:
        # 非 JSON 原样保留（字段迁移仍生效）
        assert c.execute("SELECT engine, engine_session_ids FROM sessions"
                         " WHERE id='s-bad'").fetchone()[:] == ("loadn",
                                                               "{not-json")


def test_engine_registry_no_legacy_alias():
    """alias 退役对赌：注册表只有 claude/loadn/opencode，ALIASES 符号已删。"""
    import loadn_webui.engines as eng_mod
    assert set(eng_mod.ENGINES) == {"claude", "loadn", "opencode"}
    assert not hasattr(eng_mod, "ALIASES")
