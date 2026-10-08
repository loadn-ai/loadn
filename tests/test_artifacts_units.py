"""产物中文标题/摘要回填（titlegen）单测：扫描保题、批量回填、幂等、静默降级。"""
from __future__ import annotations

import asyncio
import time

import pytest

from loadn_webui import artifacts as art
from loadn_webui import db as db_mod
from loadn_webui import workspace as ws_mod


@pytest.fixture()
def aw(tmp_path, monkeypatch):
    """artifacts 单测上下文：workspace/db 的 PATHS 指 tmp，titlegen 强制开。"""
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(ws_mod, "PATHS", {"root": tmp_path,
                                          "workspace": tmp_path / "ws"})
    monkeypatch.setattr(db_mod, "PATHS", {"root": tmp_path,
                                          "var": tmp_path / "var",
                                          "db": tmp_path / "var" / "loadn.db"})
    with db_mod.conn() as c:   # 首个 conn 自动建 schema
        c.execute("INSERT INTO sessions(id, title) VALUES('s1','测试会话')")
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)
    monkeypatch.setattr(CONFIG.titlegen, "api_key", "fake-key")
    return tmp_path


def _mk(sid: str, name: str, content: str = "正文内容"):
    d = ws_mod.PATHS["workspace"] / sid / "artifacts"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(content, encoding="utf-8")
    return f


def test_scan_preserves_backfilled_title(aw):
    """>>> 对赌：已回填中文标题/摘要的行不被文件名标题覆盖（mtime/size 照常更）。"""
    _mk("s1", "final_report.md", "v1")
    art.scan_session("s1")
    with db_mod.conn() as c:
        row = c.execute("SELECT * FROM artifacts WHERE session_id='s1'").fetchone()
        assert row["summary"] is None and row["title"] == "final report"
        c.execute("UPDATE artifacts SET title='终版实验报告', summary='主结果与结论' "
                  "WHERE id=?", (row["id"],))
    _mk("s1", "final_report.md", "v2 更长内容" * 100)
    art.scan_session("s1")
    with db_mod.conn() as c:
        row2 = c.execute("SELECT * FROM artifacts WHERE session_id='s1'").fetchone()
        assert row2["title"] == "终版实验报告"          # 不被 filename 顶掉
        assert row2["summary"] == "主结果与结论"
        assert row2["size"] > row["size"]               # 物理变更照常同步


async def test_ensure_summaries_backfill_and_idempotent(aw, monkeypatch):
    _mk("s1", "alpha.md", "这是关于因子回测的方法文档")
    _mk("s1", "beta.json", '{"data": 1}')
    art.scan_session("s1")
    calls = []

    async def fake_chat(system, user, max_tokens=64):
        calls.append(user)
        return '[{"i":0,"t":"因子回测方法","s":"描述回测协议与样本区间"},' \
               '{"i":1,"t":"数据集","s":"数值数据文件"}]'

    from loadn_webui.integrations import titlegen
    monkeypatch.setattr(titlegen, "_chat", fake_chat)
    n = await art.ensure_summaries("s1")
    assert n == 2 and len(calls) == 1                    # 一次调用出全批
    with db_mod.conn() as c:
        rows = {r["path"]: r for r in
                c.execute("SELECT * FROM artifacts WHERE session_id='s1'")}
        assert rows["artifacts/alpha.md"]["title"] == "因子回测方法"
        assert rows["artifacts/alpha.md"]["summary"] == "描述回测协议与样本区间"
    assert await art.ensure_summaries("s1") == 0         # 幂等：无待回填


async def test_ensure_summaries_disabled_and_failure(aw, monkeypatch):
    """>>> 守卫对赌：titlegen 未启用=零调用即返回；调用失败=静默 0 不落半截。"""
    from loadn_webui.config import CONFIG
    _mk("s1", "x.md", "内容")
    art.scan_session("s1")
    monkeypatch.setattr(CONFIG.titlegen, "enabled", False)
    assert await art.ensure_summaries("s1") == 0
    monkeypatch.setattr(CONFIG.titlegen, "enabled", True)

    async def boom(system, user, max_tokens=64):
        raise RuntimeError("net down")

    from loadn_webui.integrations import titlegen
    monkeypatch.setattr(titlegen, "_chat", boom)
    assert await art.ensure_summaries("s1") == 0
    with db_mod.conn() as c:
        row = c.execute("SELECT * FROM artifacts WHERE session_id='s1'").fetchone()
        assert row["summary"] is None and row["title"] == "x"   # 原状未动


def test_inflight_dedup(aw):
    """同会话并发去重：占用期间第二调用直接 0。"""
    _mk("s1", "y.md", "内容")
    art.scan_session("s1")
    art._SUMMARY_INFLIGHT.add("s1")
    assert asyncio.run(art.ensure_summaries("s1")) == 0
    art._SUMMARY_INFLIGHT.discard("s1")


def test_r3_upsert_artifact_atomic_no_dup(tmp_path):
    """三轮修（backlog 清）对赌：同 (sid,path) 两次 upsert 恰一行且值取
    后写（原 SELECT→INSERT 竞态=双行，fetchone 恒命中旧行 mtime 丢失）。"""
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        db_mod.upsert_artifact(c, session_id="s-r3", path="a.md", kind="md",
                               title="t1", size=1, mtime=1.0)
        db_mod.upsert_artifact(c, session_id="s-r3", path="a.md", kind="md",
                               title="t2", size=2, mtime=2.0)
        rows = c.execute(
            "SELECT title, size FROM artifacts WHERE session_id='s-r3' "
            "AND path='a.md'").fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == ("t2", 2)


def _mk_turn(c, sid, tid, *, started, finished, status="done"):
    c.execute(
        "INSERT INTO turns(id, session_id, status, started_at, finished_at,"
        " updated_at) VALUES(?,?,?,?,?,?)",
        (tid, sid, status, started, finished, finished))


def test_agent_attribution_explicit_and_window(aw):
    """产物按 agent 归属（rev9）：显式轨迹优先；单 agent turn 窗口兜底；
    多 agent turn 不兜底（诚实上限）。"""
    import datetime as _dt

    def iso_at(sec_ago: float) -> str:
        return (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(seconds=sec_ago)).isoformat()

    # turn1：单 agent（马洛）窗口 [100s 前, 10s 前]
    # turn2：双 agent（显式轨迹各写一文件）
    with db_mod.conn() as c:
        _mk_turn(c, "s1", 1, started=iso_at(100), finished=iso_at(10))
        _mk_turn(c, "s1", 2, started=iso_at(9), finished=iso_at(1))
        # turn1 单 agent（马洛）：一条显式轨迹让它进单 agent 窗口集合，
        # Bash 产的文件（无轨迹）走窗口兜底
        db_mod.record_agent_file(c, "s1", 1, "artifacts/turn1-direct.md",
                                 "sub_1", "马洛")
        # turn2 显式轨迹：两个 agent 各写一文件（双 agent → 窗口不兜底）
        db_mod.record_agent_file(c, "s1", 2, "artifacts/by-depp.md",
                                 "sub_1", "马洛")
        db_mod.record_agent_file(c, "s1", 2, "artifacts/by-poirot.md",
                                 "sub_2", "波洛")
    # 四份产物：显式×3 + 窗口兜底×1（mtime 在 turn1 窗口内）
    import os
    _mk("s1", "turn1-direct.md")
    _mk("s1", "by-depp.md")
    _mk("s1", "by-poirot.md")
    bash_made = _mk("s1", "bash-made.md")
    past = time.time() - 50
    os.utime(bash_made, (past, past))
    art.scan_session("s1")
    with db_mod.conn() as c:
        rows = {r["path"]: r for r in
                c.execute("SELECT * FROM artifacts WHERE session_id='s1'")}
    assert rows["artifacts/by-depp.md"]["agent_name"] == "马洛"
    assert rows["artifacts/by-depp.md"]["agent_id"] == "sub_1"
    assert rows["artifacts/by-depp.md"]["turn_id"] == 2
    assert rows["artifacts/by-poirot.md"]["agent_name"] == "波洛"
    # 窗口兜底：turn1 只有马洛一个 agent → bash-made 归马洛
    assert rows["artifacts/bash-made.md"]["agent_name"] == "马洛"
    assert rows["artifacts/bash-made.md"]["turn_id"] == 1


def test_agent_attribution_multi_agent_no_window_fallback(aw):
    """>>> 守卫对赌：turn 内两个 agent 轨迹 → 无显式轨迹的文件不瞎归属（留 NULL）。"""
    import datetime as _dt

    def iso_at(sec_ago: float) -> str:
        return (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(seconds=sec_ago)).isoformat()

    with db_mod.conn() as c:
        _mk_turn(c, "s1", 5, started=iso_at(60), finished=iso_at(5))
        db_mod.record_agent_file(c, "s1", 5, "artifacts/a.md", "sub_1", "马洛")
        db_mod.record_agent_file(c, "s1", 5, "artifacts/b.md", "sub_2", "波洛")
    _mk("s1", "a.md")
    _mk("s1", "b.md")
    _mk("s1", "mystery.md")           # 无轨迹且多 agent → 不兜底
    art.scan_session("s1")
    with db_mod.conn() as c:
        row = c.execute("SELECT * FROM artifacts WHERE session_id='s1'"
                        " AND path='artifacts/mystery.md'").fetchone()
    assert row["agent_id"] is None and row["agent_name"] is None


def test_record_agent_file_last_writer_wins(aw):
    """>>> 对赌：同 (turn,path) 重复记录取最后写者（upsert 原子，非双行）。"""
    with db_mod.conn() as c:
        db_mod.record_agent_file(c, "s1", 7, "artifacts/x.md", "sub_1", "马洛")
        db_mod.record_agent_file(c, "s1", 7, "artifacts/x.md", "sub_2", "波洛")
        rows = c.execute("SELECT agent_id, agent_name FROM agent_files"
                         " WHERE session_id='s1'").fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == ("sub_2", "波洛")
