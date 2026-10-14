"""PERF-1：sessions 列表热路径——连接数对赌 / owner 下推隔离 / usage 物化
组装 / conn 初始化短路+自愈 / 续期节流 / 索引与旧库自愈 / 分页语义。

背景（生产 189 会话即慢的根因）：修前列表一次请求 ≈2N+2 条 sqlite 连接
（每 session N+1：active_turns/usage_totals 全量重扫 turns/kv_get/next_wake
各自再开连接），且每条连接重跑 executescript(SCHEMA)+_migrate。零 token。
"""
from __future__ import annotations

import secrets
import sqlite3
import time

import httpx
import pytest

from loadn_webui import db as db_mod
from loadn_webui.security import userauth as ua

pytestmark = [pytest.mark.coverage("perf.sessions")]


# ---------------------------------------------------------------- 建数 helper
def _mk_user(c: sqlite3.Connection, name: str, role: str = "user") -> int:
    c.execute("INSERT INTO users(username, password_hash, role) VALUES(?,?,?)",
              (name, "pbkdf2$1$00$00", role))
    return c.execute("SELECT last_insert_rowid()").fetchone()[0]


def _mk_auth_sess(c: sqlite3.Connection, uid: int, remain_s: float) -> str:
    """直接落 auth_sessions（绕开 setup 端点——server 是 session 级共享，
    e2e 的 needs_setup 断言不能被本文件污染）。"""
    sid = secrets.token_urlsafe(16)
    now = time.time()
    c.execute(
        "INSERT INTO auth_sessions(id, user_id, created_at, expires_at,"
        " last_seen_at) VALUES(?,?,?,?,?)",
        (sid, uid, ua._iso_ts(now), ua._iso_ts(now + remain_s), ua._iso_ts(now)))
    return sid


def _mk_session(sid: str, updated: str, owner_id: int | None = None,
                status: str = "active") -> None:
    with db_mod.conn() as c:
        c.execute(
            "INSERT INTO sessions(id, title, profile, status, owner_id,"
            " workspace, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (sid, f"perf-{sid}", "assistant", status, owner_id,
             "/tmp/perf-unused", updated, updated))


# ---------------------------------------------------------------- ① 连接数对赌
async def test_list_conn_count(client, monkeypatch):
    """修前 2N+2（每 session 循环内再开 2 条）——本对赌修后 ≤3。"""
    for i in range(3):
        _mk_session(f"20261014_000{i}-perf", f"2026-10-01T00:00:0{i}+00:00")
    calls = {"n": 0}
    orig = db_mod.sqlite3.connect

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(db_mod.sqlite3, "connect", counting)
    r = await client.get("/api/sessions")
    monkeypatch.setattr(db_mod.sqlite3, "connect", orig)
    assert r.status_code == 200, r.text
    assert calls["n"] <= 3, f"列表请求连接数 {calls['n']} > 3（N+1 回归）"
    assert len(r.json()["sessions"]) >= 3


# ---------------------------------------------------------------- ② owner 下推隔离
async def test_owner_pushdown_isolation(server_url):
    """否定路径对赌：普通用户严格等值——他人/legacy NULL 都不可见（若下推
    写成 admin 面的 `IS NULL OR` 归并语义，legacy 行即泄漏=守卫反转被杀）。"""
    with db_mod.conn() as c:
        uid_b = _mk_user(c, "perf-userb")
        uid_adm = _mk_user(c, "perf-admin2", role="admin")
        sid_b = _mk_auth_sess(c, uid_b, 25 * 86400)
        sid_adm = _mk_auth_sess(c, uid_adm, 25 * 86400)
    _mk_session("20261014_0100-b", "2026-10-01T01:00:00+00:00", owner_id=uid_b)
    _mk_session("20261014_0200-adm", "2026-10-01T02:00:00+00:00", owner_id=uid_adm)
    _mk_session("20261014_0300-legacy", "2026-10-01T03:00:00+00:00",
                owner_id=None)   # legacy 无主（token 时代）

    async with httpx.AsyncClient(
            base_url=server_url, timeout=10,
            cookies={"loadn_session": sid_b}) as ub:
        ids = {s["id"] for s in (await ub.get("/api/sessions")).json()["sessions"]}
        assert "20261014_0100-b" in ids
        assert "20261014_0200-adm" not in ids, "他人会话泄漏"
        assert "20261014_0300-legacy" not in ids, "legacy NULL 不得并入普通用户"

    async with httpx.AsyncClient(
            base_url=server_url, timeout=10,
            cookies={"loadn_session": sid_adm}) as adm:
        ids = {s["id"] for s in (await adm.get("/api/sessions")).json()["sessions"]}
        assert {"20261014_0100-b", "20261014_0200-adm",
                "20261014_0300-legacy"} <= ids, "admin 须全见"


# ---------------------------------------------------------------- ③ usage 物化组装
async def test_usage_materialized_shape_and_parity(client):
    sid = "20261014_0400-usage"
    _mk_session(sid, "2026-10-01T04:00:00+00:00")
    with db_mod.conn() as c:
        # engine 每 turn 收尾的等价物化（增量 merge 的终态）
        c.execute("UPDATE sessions SET usage_json=?, cost_usd=? WHERE id=?",
                  ('{"input_tokens": 100, "output_tokens": 40,'
                   ' "cache_read_input_tokens": 7,'
                   ' "cache_creation_input_tokens": 3}', 1.5, sid))
        # 两行历史 turns（列表不再扫它们；usage_totals 对照用）
        for uj, cost in (('{"input_tokens": 60, "output_tokens": 20}', 0.9),
                         ('{"input_tokens": 40, "output_tokens": 20,'
                          ' "cache_read_input_tokens": 7,'
                          ' "cache_creation_input_tokens": 3}', 0.6)):
            c.execute("INSERT INTO turns(session_id, status, usage_json,"
                      " cost_usd, started_at) VALUES(?,?,?,?,?)",
                      (sid, "done", uj, cost, "2026-10-01T00:00:00+00:00"))

    r = await client.get("/api/sessions")
    row = next(s for s in r.json()["sessions"] if s["id"] == sid)
    u = row["usage"]
    assert u["in"] == 100 and u["out"] == 40 and u["total"] == 140
    assert u["cache_read"] == 7 and u["cache_write"] == 3
    assert u["total_all"] == 150
    assert u["cost_usd"] == 1.5
    # 两口径一致：物化（engine 维护）vs usage_totals（全量重扫）
    with db_mod.conn() as c:
        assert db_mod.usage_totals(c, sid)["total"] == u["total"]
        assert db_mod.usage_totals(c, sid)["total_all"] == u["total_all"]

    # 坏物化值容错：不炸、回零（不静默 500）
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET usage_json='{bad' WHERE id=?", (sid,))
    r2 = await client.get("/api/sessions")
    row2 = next(s for s in r2.json()["sessions"] if s["id"] == sid)
    assert row2["usage"]["total"] == 0 and row2["usage"]["in"] == 0


# ---------------------------------------------------------------- ④ 批量附属字段
async def test_list_batch_fields(client):
    sid = "20261014_0500-batch"
    _mk_session(sid, "2026-10-01T05:00:00+00:00")
    other = "20261014_0501-batch2"
    _mk_session(other, "2026-10-01T05:00:01+00:00")
    with db_mod.conn() as c:
        # active_turn：同会话两条 active → 取 id 最大（停止按钮目标=最新）
        t1 = c.execute("INSERT INTO turns(session_id, status)"
                       " VALUES(?, 'running')", (sid,)).lastrowid
        t2 = c.execute("INSERT INTO turns(session_id, status)"
                       " VALUES(?, 'queued')", (sid,)).lastrowid
        assert t2 > t1
        # 终态 turn 不算 active
        c.execute("INSERT INTO turns(session_id, status)"
                  " VALUES(?, 'done')", (other,))
        # profile_auto 标记（kv 批量前缀取）
        db_mod.kv_set(c, f"profile_auto:{sid}", "1")
        # next_wake：active 取 due_at 最近；paused 不算
        db_mod.create_job(c, session_id=sid, label="perf-wake", prompt="hi",
                          due_at="2030-01-01T00:00:00+00:00")
        # 同会话更晚的第二条 active：首见即最近的循环守卫若 or/and 反转，
        # 会被更晚的覆盖（对赌杀 bool:BoolOp 变异）
        db_mod.create_job(c, session_id=sid, label="perf-wake2", prompt="hi",
                          due_at="2031-01-01T00:00:00+00:00")
        db_mod.create_job(c, session_id=sid, label="perf-paused", prompt="hi",
                          due_at="2029-01-01T00:00:00+00:00", status="paused")

    rows = {s["id"]: s for s in (await client.get("/api/sessions")).json()["sessions"]}
    assert rows[sid]["active_turn"]["id"] == t2
    assert rows[other]["active_turn"] is None
    assert rows[sid]["profile_auto"] is True
    assert rows[other]["profile_auto"] is False
    assert rows[sid]["next_wake"]["label"] == "perf-wake"
    assert rows[other]["next_wake"] is None


# ---------------------------------------------------------------- ⑤ 分页/过滤参数
async def test_list_pagination_semantics(client):
    # 2030 时间戳：其他测试造的会话 updated_at 都是 now(2026)，未来时间
    # 保证本组在全局倒序里排最前（全量跑时的顺序断言不被污染）
    for i in range(1, 6):
        _mk_session(f"20261014_06{i:02d}-page",
                    f"2030-10-02T00:0{i}:00+00:00")
    with db_mod.conn() as c:
        c.execute("UPDATE sessions SET status='archived' WHERE id=?",
                  ("20261014_0605-page",))

    r = await client.get("/api/sessions?limit=2")
    ids = [s["id"] for s in r.json()["sessions"]]
    assert ids == ["20261014_0605-page", "20261014_0604-page"]  # updated_at 倒序
    r = await client.get("/api/sessions?limit=2&offset=2")
    assert [s["id"] for s in r.json()["sessions"]] == [
        "20261014_0603-page", "20261014_0602-page"]
    # 默认：全量+含 archived（前端侧栏本地分组依赖——零契约破坏）
    r = await client.get("/api/sessions")
    assert len([s for s in r.json()["sessions"]
                if s["id"].startswith("20261014_06")]) == 5
    r = await client.get("/api/sessions?include_archived=false")
    assert "20261014_0605-page" not in [s["id"] for s in r.json()["sessions"]]
    # 超上限钳制（同 admin 面 ≤500）：不炸、正常返回
    r = await client.get("/api/sessions?limit=999")
    assert r.status_code == 200


def test_list_sessions_db_defaults():
    """db 层默认参对赌：不传参=全量含 archived；limit=1 恰一行（杀
    默认参 True→False 与 max(1,…) 的 1→2 变异——路由层显式传参掩盖了
    db 层默认，无本用例则两者无观察点）。"""
    arc = "20261014_0700-defaults"
    _mk_session(arc, "2030-10-03T00:00:00+00:00", status="archived")
    with db_mod.conn() as c:
        ids_default = {r["id"] for r in db_mod.list_sessions(c)}
        ids_active = {r["id"] for r in db_mod.list_sessions(c,
                                                            include_archived=False)}
        assert arc in ids_default, "db 层默认必须含 archived"
        assert arc not in ids_active
        assert len(db_mod.list_sessions(c, limit=1)) == 1


# ---------------------------------------------------------------- ⑥ conn 短路+自愈
def test_conn_schema_shortcut_and_selfheal(tmp_path, monkeypatch):
    monkeypatch.setitem(db_mod.PATHS, "db", tmp_path / "a.db")
    db_mod._SCHEMA_READY.clear()
    calls = {"n": 0}
    orig = db_mod._migrate

    def counting(c):
        calls["n"] += 1
        return orig(c)

    monkeypatch.setattr(db_mod, "_migrate", counting)
    with db_mod.conn() as c:
        c.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
    assert calls["n"] == 1
    with db_mod.conn() as c:
        c.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
    assert calls["n"] == 1, "同库第二条连接必须短路（不重跑 DDL+迁移）"
    # 自愈：库文件被删/替换（backup restore 场景）→ 哨兵降级重跑
    for suffix in ("", "-wal", "-shm"):
        p = tmp_path / f"a.db{suffix}"
        if p.exists():
            p.unlink()
    with db_mod.conn() as c:
        c.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()
    assert calls["n"] == 2
    # DROP TABLE 自愈（test_target_policy 有故意 DROP 的 fail-closed 用例，
    # 修前靠每连接 executescript 隐式重建——短路后由哨兵表集降级承接）
    with db_mod.conn() as c:
        c.execute("DROP TABLE ping_history")
    with db_mod.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
                      " AND name='ping_history'").fetchone()[0]
    assert n == 1, "DROP 后下一条连接必须重建表（哨兵表集降级）"
    assert calls["n"] == 3
    db_mod._SCHEMA_READY.clear()


def test_old_db_index_selfheal(tmp_path, monkeypatch):
    """旧库（无 PERF-1 索引）首连 executescript IF NOT EXISTS 自愈补上。"""
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT,"
                " updated_at TEXT)")
    raw.commit()
    raw.close()
    monkeypatch.setitem(db_mod.PATHS, "db", p)
    db_mod._SCHEMA_READY.clear()
    with db_mod.conn() as c:
        names = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_sess_updated" in names
    assert "idx_job_session" in names
    db_mod._SCHEMA_READY.clear()


def test_indexes_on_main_db():
    with db_mod.conn() as c:
        names = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_sess_updated", "idx_job_session"} <= names


# ---------------------------------------------------------------- ⑦ 续期节流
def test_session_user_renew_throttle():
    """剩余 TTL>半衰不写（expires_at/last_seen_at 原样）；<半衰续期。
    修前每请求 UPDATE——前端轮询下持续写锁压力。"""
    ua.ensure_tables()
    with ua._conn() as c:
        uid = _mk_user(c, "perf-throttle")
        sid_fresh = _mk_auth_sess(c, uid, 25 * 86400)     # 剩 25d > 15d
        sid_stale = _mk_auth_sess(c, uid, 5 * 86400)      # 剩 5d < 15d
        row_fresh = c.execute("SELECT expires_at, last_seen_at FROM"
                              " auth_sessions WHERE id=?",
                              (sid_fresh,)).fetchone()
        row_stale = c.execute("SELECT expires_at FROM auth_sessions"
                              " WHERE id=?", (sid_stale,)).fetchone()

    assert ua.session_user(sid_fresh)["username"] == "perf-throttle"
    with ua._conn() as c:
        after = c.execute("SELECT expires_at, last_seen_at FROM auth_sessions"
                          " WHERE id=?", (sid_fresh,)).fetchone()
    assert after["expires_at"] == row_fresh["expires_at"], "半衰内不得写续期"
    assert after["last_seen_at"] == row_fresh["last_seen_at"]

    assert ua.session_user(sid_stale) is not None
    with ua._conn() as c:
        after2 = c.execute("SELECT expires_at FROM auth_sessions WHERE id=?",
                           (sid_stale,)).fetchone()
    assert after2["expires_at"] != row_stale["expires_at"], "过半衰必须续期"
    assert after2["expires_at"] > ua._iso_ts(time.time() + 29 * 86400)

    # 边界（杀半衰除数变异 2→3）：剩 11 天——低于半衰（15d）必须续；
    # 若阈值漂成 1/3 TTL（10d）则不续，本断言即失败
    with ua._conn() as c:
        uid2 = _mk_user(c, "perf-edge11")
        sid_edge = _mk_auth_sess(c, uid2, 11 * 86400)
    assert ua.session_user(sid_edge) is not None
    with ua._conn() as c:
        after3 = c.execute("SELECT expires_at FROM auth_sessions WHERE id=?",
                           (sid_edge,)).fetchone()
    assert after3["expires_at"] > ua._iso_ts(time.time() + 29 * 86400), \
        "剩余 11 天（<半衰 15 天）必须续期"


# ---------------------------------------------------------------- ⑧ 发版感知数据源
async def test_health_release_version_shape(client):
    """v0.7.29 stale-pill 触发依赖：health 必须带 release 键（前端 60s 轮询
    对比基线亮「平台已更新」——键漂移=横幅永远不亮）。version 值仅 release
    构建存在（git 仓无 RELEASE.json=空 dict 属预期），真值形状由 release
    build 冒烟（healthcheck 验证 release=vX.Y.Z）覆盖。"""
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert isinstance(r.json().get("release"), dict)
