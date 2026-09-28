"""会话级参数覆盖（属性面板「模型参数」段）：校验/生效链/迁移/API/argv 集成。

- validate：整体替换语义（null=全清存 NULL）、单键 null=跟随 profile、
  0 哨兵（max_turns/rotate_input_tokens 的 profile None 语义只能用 0 表达）
- effective：覆盖优先 + 0→None 换算；defaults 与 profile 对齐
- SCHEMA_REV 3：旧结构库 conn() 补 params_json 列 + kv 记版本
- PATCH /api/sessions/{sid} params 分支：roundtrip / 单键清除 / 400
- fake_claude 集成：PATCH 后下一 turn 的 argv 携带新值（下一轮生效语义）
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from loadn_webui import params as params_mod


# ---------------------------------------------------------------- 校验单元
def test_validate_roundtrip_and_null_semantics():
    out = params_mod.validate({"model": " glm-5.3 ", "effort": "low",
                               "max_turns": 0, "timeout_s": 3600,
                               "stall_timeout_s": 600,
                               "rotate_input_tokens": 0})
    assert json.loads(out) == {"model": "glm-5.3", "effort": "low",
                               "max_turns": 0, "timeout_s": 3600,
                               "stall_timeout_s": 600,
                               "rotate_input_tokens": 0}
    # 单键 null = 该项跟随 profile（从覆盖里清除）
    out = params_mod.validate({"model": None, "effort": "high"})
    assert json.loads(out) == {"effort": "high"}
    # 整体 null / 清完剩空壳 → None（存 NULL）
    assert params_mod.validate(None) is None
    assert params_mod.validate({}) is None
    assert params_mod.validate({"model": None}) is None


@pytest.mark.parametrize("body,msg", [
    ("not-a-dict", "params"),
    ({"nope": 1}, "未知参数"),
    ({"effort": "turbo"}, "effort"),
    ({"model": ""}, "model"),
    ({"model": "x" * 101}, "model"),
    ({"max_turns": "7"}, "max_turns"),
    ({"max_turns": True}, "max_turns"),
    ({"max_turns": 201}, "max_turns"),
    ({"timeout_s": 10}, "timeout_s"),
    ({"stall_timeout_s": 86401}, "stall_timeout_s"),
    ({"rotate_input_tokens": -1}, "rotate_input_tokens"),
])
def test_validate_rejects(body, msg):
    with pytest.raises(ValueError, match=msg):
        params_mod.validate(body)


def test_effective_zero_sentinels():
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("researcher")
    # 覆盖优先；0 哨兵 → None（不限制/禁用轮换——PATCH null 语义是「清除」，
    # 这两个态只能用 0 表达）
    eff = params_mod.effective(prof, {"max_turns": 0, "effort": "low",
                                      "model": "m-x",
                                      "rotate_input_tokens": 0})
    assert eff["max_turns"] is None
    assert eff["rotate_input_tokens"] is None
    assert eff["effort"] == "low" and eff["model"] == "m-x"
    assert eff["timeout_s"] == prof.timeout_s           # 未覆盖跟 profile
    # 无覆盖 = 全跟 profile
    assert params_mod.effective(prof, {}) == params_mod.defaults(prof)


def test_load_fail_open():
    assert params_mod.load(None) == {}
    assert params_mod.load("") == {}
    assert params_mod.load("not json") == {}            # 坏数据 fail-open + log
    assert params_mod.load('{"model":"m","bogus":1}') == {"model": "m"}


# ---------------------------------------------------------------- 迁移
def test_schema_rev3_migration(tmp_path, monkeypatch):
    """旧结构库（rev2：无 params_json）→ conn() 打开即补列 + kv 记版本 3。"""
    from loadn_webui import db as db_mod
    from loadn_webui.config import PATHS
    db_path = tmp_path / "old.db"
    monkeypatch.setitem(PATHS, "db", db_path)
    c = sqlite3.connect(db_path)
    c.executescript("""
      CREATE TABLE sessions (
        id TEXT PRIMARY KEY, title TEXT, profile TEXT,
        status TEXT DEFAULT 'active', starred INTEGER DEFAULT 0,
        claude_session_id TEXT, session_fresh INTEGER DEFAULT 1,
        resume_failures INTEGER DEFAULT 0, workspace TEXT, skills_json TEXT,
        mcp_json TEXT, cost_usd REAL DEFAULT 0, usage_json TEXT,
        engine TEXT DEFAULT 'claude', engine_session_ids TEXT,
        engine_override TEXT, created_at TEXT, updated_at TEXT);
    """)
    c.commit()
    c.close()
    with db_mod.conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
        assert "params_json" in cols
        assert "pending_anchor" in cols                    # 更早的迁移照跑
        row = c.execute("SELECT value FROM kv WHERE key='schema_rev'").fetchone()
        assert row[0] == "3"
    assert db_mod.SCHEMA_REV == 3


# ---------------------------------------------------------------- API 面
async def test_patch_params_roundtrip_and_clear(client):
    r = await client.post("/api/sessions", json={"title": "参数面板"})
    sid = r.json()["session"]["id"]
    d = (await client.get(f"/api/sessions/{sid}")).json()
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get(d["profile"])
    assert d["params"] == {}
    assert d["params_effective"] == d["params_profile"] == params_mod.defaults(prof)

    r = await client.patch(f"/api/sessions/{sid}", json={
        "params": {"effort": "low", "max_turns": 0}})
    assert r.status_code == 200, r.text
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["params"] == {"effort": "low", "max_turns": 0}
    assert d["params_effective"]["effort"] == "low"
    assert d["params_effective"]["max_turns"] is None     # 0 哨兵 → 不限制
    assert d["params_profile"]["effort"] == prof.effort  # profile 侧提示仍在

    # 单键清除（null）→ 整体空壳也存 NULL
    r = await client.patch(f"/api/sessions/{sid}", json={"params": {"effort": None}})
    assert r.status_code == 200
    assert (await client.get(f"/api/sessions/{sid}")).json()["params"] == {}
    # 整体清除
    await client.patch(f"/api/sessions/{sid}", json={"params": {"effort": "high"}})
    r = await client.patch(f"/api/sessions/{sid}", json={"params": None})
    assert r.status_code == 200
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["params"] == {} and d["params_json"] is None

    # 校验失败 400（整组都不落）
    for bad in ({"effort": "turbo"}, {"max_turns": "7"}, {"nope": 1},
                {"timeout_s": 1}, {"params": 1}):
        r = await client.patch(f"/api/sessions/{sid}", json={"params": bad})
        assert r.status_code == 400, bad
    assert (await client.get(f"/api/sessions/{sid}")).json()["params"] == {}


# ---------------------------------------------------------------- 引擎接线
async def test_params_reach_engine_argv(client, ws_root, fake_calls):
    """PATCH 参数 → 下一 turn 的 argv 携带新值（下一轮生效）。"""
    r = await client.post("/api/sessions", json={"title": "参数接线"})
    sid = r.json()["session"]["id"]
    fake = ws_root / sid / ".fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "reply").write_text("ok")

    r = await client.patch(f"/api/sessions/{sid}", json={
        "params": {"model": "glm-5.3", "effort": "low", "max_turns": 5}})
    assert r.status_code == 200

    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "跑一轮"})
    tid = r.json()["turn"]["id"]
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < 30:
        await asyncio.sleep(0.2)
        turns = (await client.get(f"/api/sessions/{sid}")).json()["turns"]
        t = next((x for x in turns if x["id"] == tid), None)
        if t and t["status"] in ("done", "error", "stopped", "interrupted"):
            break
    assert t["status"] == "done", t.get("error")
    argv = fake_calls()[-1]["argv"]

    def flag(f: str) -> str:
        return argv[argv.index(f) + 1]

    assert flag("--model") == "glm-5.3"
    assert flag("--effort") == "low"
    assert flag("--max-turns") == "5"
