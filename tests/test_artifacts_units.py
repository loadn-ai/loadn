"""产物中文标题/摘要回填（titlegen）单测：扫描保题、批量回填、幂等、静默降级。"""
from __future__ import annotations

import asyncio

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
