"""项目 → 子任务（v0.6 独立任务目录语义）。

核心断言：① 子任务 workspace=<项目根>/tasks/<NN>-<slug>/ 私有目录
（PROGRESS/state/notes/artifacts/settings 全任务级；项目共享文件不被触碰，
任务目录不落宪法——祖先链到项目根读）；② 对话历史/turn 按 sid 隔离；
③ steer 文件带 sid 段不串台；④ purge 任务目录私有可独删、项目根引用计数；
⑤ 归档连坐与恢复；⑥ API 守卫（禁移动/宪法不变）。
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


async def _mk_subtask(client, pid: str, title: str, first: str | None = None) -> str:
    body: dict = {"project_id": pid, "title": title}
    if first is not None:
        body["first_message"] = first
    r = await client.post("/api/sessions", json=body)
    assert r.status_code == 200
    return r.json()["session"]["id"]


async def test_subtask_private_task_dir(client, ws_root):
    """子任务建在 <项目根>/tasks/<NN>-<slug>/：私有台账/settings 齐备、
    不落宪法（祖先链读项目根）、无 inputs 目录（共享在项目根）、
    项目共享文件逐字节不变。"""
    pid, pws = await _mk_project(client)
    before = {name: _digest(ws_root / pid / name) for name in
              ("CLAUDE.md", "AGENTS.md", "state.json", "PROGRESS.md",
               ".mcp.json", ".claude/settings.json", ".agent/settings.json")}
    sid = await _mk_subtask(client, pid, "子任务A")
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
        assert row["project_id"] == pid
        assert row["workspace"].startswith(pws + "/tasks/"), row["workspace"]
    tws = ws_of(sid)
    assert tws.parent == ws_root / pid / "tasks"
    # 任务级私有物
    assert (tws / "state.json").exists() and (tws / "PROGRESS.md").exists()
    assert (tws / ".claude/settings.json").exists()
    for d in ("artifacts", "work", "notes", "logs"):
        assert (tws / d).is_dir()
    # 宪法不落任务目录（祖先链到项目根读）；inputs 共享在项目根
    assert not (tws / "CLAUDE.md").exists() and not (tws / "inputs").exists()
    assert (ws_root / pid / "inputs").is_dir()
    # 项目共享文件零触碰
    after = {name: _digest(ws_root / pid / name) for name in before}
    assert after == before, "子任务创建覆盖了项目共享文件！"
    # 第二个子任务：不同目录（序号递增）
    sid2 = await _mk_subtask(client, pid, "子任务B")
    assert ws_of(sid2) != tws and ws_of(sid2).parent == tws.parent


async def test_subtasks_isolated_history(client):
    """两子任务各自 turn/消息按 sid 隔离（fake claude 秒回）。"""
    pid, _ = await _mk_project(client)
    sids = [await _mk_subtask(client, pid, f"子{i}", first=f"任务{i}")
            for i in range(2)]
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
    """并发 loadn 子任务插话各归各（.steer.<sid>.jsonl 落各自任务目录）。"""
    from loadn_webui.config import CONFIG
    from loadn_webui.engine import ENGINE
    monkeypatch.setattr(CONFIG.engines, "default", "hahaness")
    monkeypatch.setenv("HAHANESS_HOME", str(tmp_path / "hh"))
    monkeypatch.setenv("HAHANESS_PROVIDER", "fake")
    pid, pws = await _mk_project(client)
    sids = [await _mk_subtask(client, pid, f"并发{i}") for i in range(2)]
    # fake 控制文件落各自任务目录（cwd=任务目录，.fake 就近读取）
    for sid in sids:
        fake = ws_of(sid) / ".fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "tools").write_text(json.dumps(
            {"name": "Bash", "input": {"command": "sleep 4"}}))
        await client.post(f"/api/sessions/{sid}/messages",
                          json={"text": f"跑{sids.index(sid)}"})
    # 等 turn 都 running 且过了首轮
    for sid in sids:
        t0 = asyncio.get_running_loop().time()
        while asyncio.get_running_loop().time() - t0 < 15:
            if any(t.session_id == sid for t in ENGINE.active.values()):
                break
            await asyncio.sleep(0.05)
    # 各自插话
    for i, sid in enumerate(sids):
        r = await client.post(f"/api/sessions/{sid}/steer",
                              json={"text": f"给{i}的专属指令"})
        assert r.json()["steered"] is True
    for i, sid in enumerate(sids):
        f = ws_of(sid) / f".steer.{sid}.jsonl"
        content = f.read_text()
        assert f"给{i}的专属指令" in content
        assert f"给{1 - i}的专属指令" not in content   # 不串台
    # 收尾：停掉两 turn 防测试残留
    for sid in sids:
        for t in list(ENGINE.active.values()):
            if t.session_id == sid:
                await ENGINE.stop_turn(t.turn_id)


async def test_purge_refcount(client, ws_root):
    """purge：子任务目录私有可独删（项目根保留）；项目 purge 连坐删根。"""
    pid, pws = await _mk_project(client)
    r1 = await _mk_subtask(client, pid, "A")
    r2 = await _mk_subtask(client, pid, "B")
    # 删 A：其任务目录独属 → 直接删；项目根与 B 不受影响
    await client.delete(f"/api/sessions/{r1}?purge=true")
    assert not ws_of(r1).exists()
    assert (ws_root / pid).exists() and ws_of(r2).exists()
    with db_mod.conn() as c:
        assert db_mod.get_session(c, r1) is None
        assert db_mod.get_session(c, r2) is not None
    # 删项目（连坐 B）→ 项目根整树删除
    r = await client.delete(f"/api/projects/{pid}?purge=true")
    assert r.status_code == 200
    assert not ws_root.joinpath(pid).exists()
    with db_mod.conn() as c:
        assert db_mod.get_session(c, r2) is None
        assert db_mod.get_project(c, pid) is None


async def test_archive_cascade(client):
    """项目归档连坐子任务；恢复反向连坐。"""
    pid, _ = await _mk_project(client)
    sid = await _mk_subtask(client, pid, "S")
    await client.delete(f"/api/projects/{pid}")
    with db_mod.conn() as c:
        assert db_mod.get_project(c, pid)["status"] == "archived"
        assert db_mod.get_session(c, sid)["status"] == "archived"
    await client.patch(f"/api/projects/{pid}/restore")
    with db_mod.conn() as c:
        assert db_mod.get_project(c, pid)["status"] == "active"
        assert db_mod.get_session(c, sid)["status"] == "active"


async def test_api_guards(client, ws_root):
    """API 守卫：project_id 404 / 禁移动 / 子任务改 profile 不动宪法
    （但任务级 settings 随 profile 刷新）。"""
    pid, _ = await _mk_project(client)
    r = await client.post("/api/sessions",
                          json={"project_id": "no-such", "title": "x"})
    assert r.status_code == 404
    sid = await _mk_subtask(client, pid, "S")
    r = await client.patch(f"/api/sessions/{sid}", json={"project_id": pid})
    assert r.status_code == 400
    # 子任务改 profile：DB 更新但项目宪法不变
    before = _digest(ws_root / pid / "CLAUDE.md")
    r = await client.patch(f"/api/sessions/{sid}", json={"profile": "coder"})
    assert r.status_code == 200
    assert _digest(ws_root / pid / "CLAUDE.md") == before
    assert not (ws_of(sid) / "CLAUDE.md").exists()   # 任务目录仍无宪法
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
    settings env 翻 PROJECT_ID；之后新建子任务落 tasks/ 子目录。"""
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
    assert "LOADN_SESSION_ID" not in env and env["LOADN_PROJECT_ID"] == sid
    # 升级后 + 子任务：落项目根 tasks/ 下（v0.6 独立任务目录）
    kid = await _mk_subtask(client, sid, "新子任务")
    with db_mod.conn() as c:
        kid_ws = db_mod.get_session(c, kid)["workspace"]
    assert kid_ws.startswith(old_ws + "/tasks/") and kid_ws != old_ws
    # 重复升级 → 400
    assert (await client.post(f"/api/sessions/{sid}/promote")).status_code == 400


# ---------------------------------------------------------------- rev11 代码仓绑定
async def test_project_repo_binding(client, ws_root, tmp_path):
    """rev11 项目规则遵循端到端：绑定仓→任务目录落 <repo>/tasks/、仓内
    CLAUDE.md 经宪法祖先链真进任务上下文、绑定=admit（gate 放行）、
    .gitignore 缺 tasks/ 给提示；解绑回落项目工作区；非法路径 400。"""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "CLAUDE.md").write_text("# 仓规则：commit 用 feat/fix(模块) 前缀")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}")   # 资源标记→admit 才放行

    pid, pws = await _mk_project(client)
    r = await client.patch(f"/api/projects/{pid}", json={"repo": str(repo)})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["repo"] == str(repo.resolve())
    assert "tasks/" in (r.json().get("repo_hint") or "")   # gitignore 建议

    from loadn.truststore import gate
    ok, _why = gate(repo)
    assert ok, _why                                           # 绑定即 admit

    sid = await _mk_subtask(client, pid, "改登录")
    assert ws_of(sid).parent == repo / "tasks"                # 任务目录在仓内
    assert (ws_root / pid / "CLAUDE.md").exists()             # 项目宪法仍住自建区
    assert not (repo / "PROGRESS.md").exists()                # scaffold 不污染仓根
    # 宪法祖先链端到端：仓规则真进任务上下文（引擎同一装配器）
    from loadn.core.context import ContextAssembler
    assert "feat/fix(模块)" in ContextAssembler(ws_of(sid)).build()

    # 解绑：新任务回落项目工作区；守卫：非法路径 400
    r2 = await client.patch(f"/api/projects/{pid}", json={"repo": ""})
    assert r2.status_code == 200 and r2.json()["project"]["repo"] is None
    sid2 = await _mk_subtask(client, pid, "解绑后任务")
    assert ws_of(sid2).parent == ws_root / pid / "tasks"
    assert (await client.patch(
        f"/api/projects/{pid}", json={"repo": "relative/x"})).status_code == 400
    assert (await client.patch(
        f"/api/projects/{pid}",
        json={"repo": str(tmp_path / "nope")})).status_code == 400


async def test_project_create_with_repo(client, ws_root, tmp_path):
    """POST 建项目直带仓：repo 落列、提示回传、admit 生效。"""
    repo = tmp_path / "repo2"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".gitignore").write_text("tasks/\n")              # 已配置→无提示
    r = await client.post("/api/projects",
                          json={"title": "带仓项目", "repo": str(repo)})
    assert r.status_code == 200, r.text
    proj = r.json()["project"]
    assert proj["repo"] == str(repo.resolve())
    assert not r.json().get("repo_hint")
    from loadn.truststore import gate
    assert gate(repo)[0]
