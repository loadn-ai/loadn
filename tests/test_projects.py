"""项目 → 子任务（共享工作区，CC 多窗口语义）。

核心断言：① 子任务 workspace=项目目录且 scaffold 不覆盖共享文件；
② 对话历史/turn 按 sid 隔离；③ steer 文件带 sid 段不串台；④ purge 引用
计数（共享目录不连坐）；⑤ 归档连坐与恢复；⑥ API 守卫（禁移动/宪法不变）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from loadn_webui import db as db_mod
from loadn_webui.workspace import ws_of


def _digest(p):
    """存在则 sha256，不存在记 None（如无 MCP 配置时不落 .mcp.json）。"""
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


async def _mk_project(client, title="打比赛") -> tuple[str, str]:
    r = await client.post("/api/projects", json={"title": title})
    assert r.status_code == 200
    pid = r.json()["project"]["id"]
    return pid, str(ws_of(pid))


async def test_subtask_shares_workspace_no_overwrite(client, ws_root):
    """子任务 workspace=项目目录；创建后项目宪法/settings/.mcp.json/state.json
    逐字节不变（scaffold 不覆盖共享文件）。"""
    pid, pws = await _mk_project(client)
    before = {name: _digest(ws_root / pid / name) for name in
              ("CLAUDE.md", "AGENTS.md", "state.json", "PROGRESS.md",
               ".mcp.json", ".claude/settings.json", ".agent/settings.json")}
    r = await client.post("/api/sessions", json={"project_id": pid, "title": "子任务A"})
    assert r.status_code == 200
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
        assert row["workspace"] == pws and row["project_id"] == pid
    assert ws_of(sid) == ws_of(pid)
    after = {name: _digest(ws_root / pid / name) for name in before}
    assert after == before, "子任务创建覆盖了项目共享文件！"


async def test_subtasks_isolated_history(client):
    """两子任务各自 turn/消息按 sid 隔离（fake claude 秒回）。"""
    pid, _ = await _mk_project(client)
    sids = []
    for i in range(2):
        r = await client.post("/api/sessions",
                              json={"project_id": pid, "title": f"子{i}",
                                    "first_message": f"任务{i}"})
        sids.append(r.json()["session"]["id"])
    for i, sid in enumerate(sids):
        t0 = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - t0 < 15:
            d = (await client.get(f"/api/sessions/{sid}")).json()
            if d["turns"] and d["turns"][-1]["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.2)
        msgs = [m["content"] for m in d["messages"] if m["role"] == "user"]
        assert msgs == [f"任务{i}"], (sid, msgs)   # 各自只看到自己的消息


async def test_subtask_engine_env_and_steer_no_crosstalk(
        client, ws_root, tmp_path, monkeypatch):
    """共享目录下两 hahaness 子任务并发运行，插话各归各（.steer.<sid>.jsonl）。"""
    from loadn_webui.config import CONFIG
    from loadn_webui.engine import ENGINE
    monkeypatch.setattr(CONFIG.engines, "default", "hahaness")
    monkeypatch.setenv("HAHANESS_HOME", str(tmp_path / "hh"))
    monkeypatch.setenv("HAHANESS_PROVIDER", "fake")
    pid, pws = await _mk_project(client)
    sids = []
    for i in range(2):
        fake = ws_root / pid / ".fake"
        fake.mkdir(parents=True, exist_ok=True)     # 共享 .fake：两子任务同控
        (fake / "tools").write_text(json.dumps(
            {"name": "Bash", "input": {"command": "sleep 4"}}))
        r = await client.post("/api/sessions",
                              json={"project_id": pid, "title": f"并发{i}"})
        sids.append(r.json()["session"]["id"])
        await client.post(f"/api/sessions/{sids[-1]}/messages",
                          json={"text": f"跑{i}"})
    # 等 turn 都 running 且过了首轮
    ats = {}
    for sid in sids:
        t0 = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - t0 < 15:
            if any(t.session_id == sid for t in ENGINE.active.values()):
                break
            await asyncio.sleep(0.05)
        ats[sid] = next(t for t in ENGINE.active.values() if t.session_id == sid)
    # 各自插话
    for i, sid in enumerate(sids):
        r = await client.post(f"/api/sessions/{sid}/steer",
                              json={"text": f"给{i}的专属指令"})
        assert r.json()["steered"] is True
    for i, sid in enumerate(sids):
        f = ws_root / pid / f".steer.{sid}.jsonl"
        content = f.read_text()
        assert f"给{i}的专属指令" in content
        other = sids[1 - i]
        assert f"给{1 - i}的专属指令" not in content   # 不串台
    # 收尾：停掉两 turn 防测试残留
    for sid in sids:
        for t in list(ENGINE.active.values()):
            if t.session_id == sid:
                await ENGINE.stop_turn(t.turn_id)


async def test_purge_refcount(client, ws_root):
    """共享目录 purge：兄弟在只删 DB 行；最后一个引用者（项目行）删目录。"""
    pid, pws = await _mk_project(client)
    r1 = (await client.post("/api/sessions", json={"project_id": pid, "title": "A"})).json()["session"]["id"]
    r2 = (await client.post("/api/sessions", json={"project_id": pid, "title": "B"})).json()["session"]["id"]
    # 删 A：B 和项目还引用 → 目录保留
    await client.delete(f"/api/sessions/{r1}?purge=true")
    assert ws_root.joinpath(pid).exists()
    with db_mod.conn() as c:
        assert db_mod.get_session(c, r1) is None
        assert db_mod.get_session(c, r2) is not None
    # 删项目（连坐 B）→ 目录最终删除
    r = await client.delete(f"/api/projects/{pid}?purge=true")
    assert r.status_code == 200
    assert not ws_root.joinpath(pid).exists()
    with db_mod.conn() as c:
        assert db_mod.get_session(c, r2) is None
        assert db_mod.get_project(c, pid) is None


async def test_archive_cascade(client):
    """项目归档连坐子任务；恢复反向连坐。"""
    pid, _ = await _mk_project(client)
    sid = (await client.post("/api/sessions", json={"project_id": pid, "title": "S"})).json()["session"]["id"]
    await client.delete(f"/api/projects/{pid}")
    with db_mod.conn() as c:
        assert db_mod.get_project(c, pid)["status"] == "archived"
        assert db_mod.get_session(c, sid)["status"] == "archived"
    await client.patch(f"/api/projects/{pid}/restore")
    with db_mod.conn() as c:
        assert db_mod.get_project(c, pid)["status"] == "active"
        assert db_mod.get_session(c, sid)["status"] == "active"


async def test_api_guards(client, ws_root):
    """API 守卫：project_id 404 / 禁移动 / 子任务改 profile 不动宪法。"""
    pid, _ = await _mk_project(client)
    r = await client.post("/api/sessions",
                          json={"project_id": "no-such", "title": "x"})
    assert r.status_code == 404
    sid = (await client.post("/api/sessions", json={"project_id": pid, "title": "S"})).json()["session"]["id"]
    r = await client.patch(f"/api/sessions/{sid}", json={"project_id": pid})
    assert r.status_code == 400
    # 子任务改 profile：DB 更新但项目宪法不变
    before = _digest(ws_root / pid / "CLAUDE.md")
    r = await client.patch(f"/api/sessions/{sid}", json={"profile": "coder"})
    assert r.status_code == 200
    assert _digest(ws_root / pid / "CLAUDE.md") == before
    # GET /api/sessions 带 project 字段
    rows = (await client.get("/api/sessions")).json()["sessions"]
    me = next(x for x in rows if x["id"] == sid)
    assert me["project_id"] == pid and me["project_title"] == "打比赛"
    projs = (await client.get("/api/projects")).json()["projects"]
    assert any(p["id"] == pid and p["n_sessions"] == 1 for p in projs)
    # 项目改名 → 宪法重渲染（项目自己有权）
    await client.patch(f"/api/projects/{pid}", json={"title": "打比赛v2"})
    assert _digest(ws_root / pid / "CLAUDE.md") != before


async def test_promote_session_to_project(client, ws_root):
    """现有任务一键升级为项目：workspace 零迁移、原任务成首个子任务、
    settings env 翻 PROJECT_ID、之后可 + 子任务共享同目录。"""
    import json as j
    r = await client.post("/api/sessions", json={"title": "旧任务"})
    sid = r.json()["session"]["id"]
    old_ws = str(ws_root / sid)
    r = await client.post(f"/api/sessions/{sid}/promote")
    assert r.status_code == 200 and r.json()["project_id"] == sid
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        proj = db_mod.get_project(c, sid)
    assert sess["project_id"] == sid and proj["workspace"] == old_ws   # 目录没搬
    env = j.load(open(ws_root / sid / ".claude/settings.json"))["env"]
    assert "WORKDADDY_SESSION_ID" not in env and env["WORKDADDY_PROJECT_ID"] == sid
    # 升级后 + 子任务：同一目录（查 workspace 列，不是路径拼接）
    r = await client.post("/api/sessions", json={"project_id": sid, "title": "新子任务"})
    kid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        assert db_mod.get_session(c, kid)["workspace"] == old_ws
    # 重复升级 → 400
    assert (await client.post(f"/api/sessions/{sid}/promote")).status_code == 400
