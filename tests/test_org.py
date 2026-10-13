"""数字员工（DW-1）：组织架构数据面（init 种子/CRUD/recruit/池）。

对赌焦点：①init 幂等护栏（已有数据再 init 报错）+种子形状（8 部门
18 人、MBTI 合法、名字唯一）②API CRUD 全链（部门→员工→编辑→删除）
③recruit LLM 失败自动兜底（n 条、MBTI 白名单、名字唯一）+ 池退役。
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest


def _fresh_db(tmp_path):
    """独立净库（函数层对赌用；与 API 测试的共享库隔离）。"""
    from loadn_webui.db import SCHEMA
    c = sqlite3.connect(tmp_path / "org.db")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


# ------------------------------------------------ DW-6：职责说明+任务记录
def test_duty_seed_and_def_integration(tmp_path, monkeypatch):
    """>>> 种子全员有职责；persona/def 双集成：职责独立成节进 system_add、
    截断进 description（模型选人依据）。"""
    from loadn_webui import db as db_mod
    from loadn_webui import org as org_mod
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    c = _fresh_db(tmp_path)
    org_mod.init_org(c)
    emps = c.execute("SELECT * FROM org_employees").fetchall()
    assert all(e["duty"] for e in emps), "种子员工职责必须全员非空"

    emp = db_mod.to_dict(next(e for e in emps if e["name"] == "方维正"))
    dept = c.execute("SELECT name FROM org_departments WHERE id=?",
                     (emp["dept_id"],)).fetchone()["name"]
    sysadd = org_mod.persona_system_add(emp, dept)
    assert "职责：" in sysadd and emp["duty"] in sysadd
    assert sysadd.index("职责：") < sysadd.index(emp["persona"]), \
        "职责节在人设之前（岗位事实先于行事风格）"

    org_mod.sync_agent_def(c, emp["id"])
    body = (tmp_path / "home" / "agents"
            / org_mod._def_filename("方维正")).read_text(encoding="utf-8")
    assert "职责：" in body and "subagent_type" in body


async def test_duty_api_roundtrip(client):
    """>>> duty 字段 API 往返：建员带职责→PATCH 改职责→GET 树回读。"""
    tag = uuid.uuid4().hex[:6]
    d = (await client.post("/api/org/departments",
                           json={"name": f"职责部-{tag}"})).json()
    e = (await client.post("/api/org/employees", json={
        "dept_id": d["id"], "name": f"职责员-{tag}", "title": "合规专员",
        "duty": "审查合同条款与数据合规风险", "mbti": "ESTJ",
        "traits": "严谨", "persona": "测试人设"})).json()
    assert e["duty"] == "审查合同条款与数据合规风险"
    e2 = (await client.patch(f"/api/org/employees/{e['id']}",
                             json={"duty": "改：负责开源许可证审查"})).json()
    assert e2["duty"] == "改：负责开源许可证审查"
    tree = (await client.get("/api/org")).json()
    got = next(x for dd in tree["departments"] if dd.get("employees")
               for x in dd["employees"] if x["id"] == e["id"])
    assert got["duty"].startswith("改：")
    await client.delete(f"/api/org/employees/{e['id']}")
    await client.delete(f"/api/org/departments/{d['id']}")


async def test_org_tasks_endpoint(client):
    """>>> 任务记录端点：name 缺失 400；双归属（subagent_type=名 点名/
    agent_name=名 出勤）都命中；描述取 agent_role；不串人。"""
    import json as _j

    from loadn_webui import db as db_mod

    r = await client.get("/api/org/tasks")
    assert r.status_code == 400, "name 必填守卫"

    tag = uuid.uuid4().hex[:6]
    sid = f"20261013_1200-orgtask{tag}"
    with db_mod.conn() as c:
        c.execute("INSERT INTO sessions (id, title, created_at, updated_at) "
                  "VALUES (?,?,datetime('now'),datetime('now'))",
                  (sid, f"任务记录会话-{tag}"))
    # 双归属两条 Task 卡：点名派发（subagent_type）+ 临时工出勤（agent_name）
    blocks = [
        {"name": "Task", "subagent_type": "方维正",
         "agent_role": f"审合同-{tag}", "agent_id": "sub_1"},
        {"name": "Task", "agent_name": f"临时甲{tag}",
         "agent_role": f"搬数据-{tag}", "agent_id": "sub_2"},
    ]
    for b in blocks:
        with db_mod.conn() as c:
            t = c.execute(
                "INSERT INTO turns (session_id, status, started_at) VALUES "
                "(?, 'done', datetime('now'))", (sid,)).lastrowid
            c.execute(
                "INSERT INTO messages (session_id, turn_id, role, content, "
                "blocks_json, created_at) VALUES (?,?,?,?,?,datetime('now'))",
                (sid, t, "assistant", "",
                 _j.dumps([b], ensure_ascii=False)))

    r = (await client.get("/api/org/tasks",
                          params={"name": "方维正"})).json()
    assert any(f"审合同-{tag}" == t2["description"] for t2 in r["tasks"]), \
        "点名派发（subagent_type）须命中"
    assert not any(f"搬数据-{tag}" == t2["description"] for t2 in r["tasks"]), \
        "别人（临时工）的任务不得串入"
    assert all(t2["session_id"] == sid for t2 in r["tasks"])
    r2 = (await client.get("/api/org/tasks",
                           params={"name": f"临时甲{tag}"})).json()
    assert any(f"搬数据-{tag}" == t2["description"] for t2 in r2["tasks"]), \
        "出勤取名（agent_name）同样命中"
    # 清理（防污染共享库的其他 org 断言）
    with db_mod.conn() as c:
        c.execute("DELETE FROM messages WHERE session_id=?", (sid,))
        c.execute("DELETE FROM turns WHERE session_id=?", (sid,))
        c.execute("DELETE FROM sessions WHERE id=?", (sid,))


def test_init_seed_shape_and_guard(tmp_path):
    """>>> init 一键 18 人 + 再 init 幂等护栏（ValueError）。"""
    from loadn_webui import org as org_mod
    c = _fresh_db(tmp_path)
    n = org_mod.init_org(c)
    assert n == 18, f"种子员工数 {n} != 18"
    depts = c.execute("SELECT * FROM org_departments ORDER BY sort").fetchall()
    assert [d["name"] for d in depts] == [
        "总经办", "产品部", "研发部", "设计部", "法务部",
        "人力资源部", "运营部", "数据部"], "部门序/名与设计 §6 不符"
    emps = c.execute("SELECT * FROM org_employees").fetchall()
    assert len({e["name"] for e in emps}) == 18, "员工名必须唯一"
    for e in emps:
        assert e["mbti"] in org_mod.MBTI_TYPES, f"{e['name']} MBTI 非法: {e['mbti']}"
        assert e["persona"] and e["traits"], f"{e['name']} 人设/性格缺失"
        assert e["color"], f"{e['name']} 归属色缺失"
    # 幂等护栏：已有数据再 init 拒绝
    with pytest.raises(ValueError, match="已存在"):
        org_mod.init_org(c)


def test_dept_delete_detaches_employees(tmp_path):
    """>>> 删部门员工不连带消失（ON DELETE SET NULL → dept_id 空）。"""
    from loadn_webui import org as org_mod
    c = _fresh_db(tmp_path)
    org_mod.init_org(c)
    dept = c.execute("SELECT id FROM org_departments WHERE name='法务部'").fetchone()
    org_mod.delete_department(c, dept["id"])
    orphans = c.execute(
        "SELECT name FROM org_employees WHERE dept_id IS NULL").fetchall()
    assert {o["name"] for o in orphans} == {"方维正", "顾聆"}, "法务部两人应挪未分配"


async def test_org_api_crud_roundtrip(client):
    """>>> API 全链：建部→建员→PATCH 编辑/停用→树查询→删除。"""
    tag = uuid.uuid4().hex[:6]
    d = (await client.post("/api/org/departments",
                           json={"name": f"测试部-{tag}", "icon": "star"})).json()
    assert d["id"] and d["icon"] == "star"
    e = (await client.post("/api/org/employees", json={
        "dept_id": d["id"], "name": f"测试员-{tag}", "title": "测试头衔",
        "mbti": "INFP", "traits": "细致,安静",
        "persona": "测试人设", "tools": ["Read", "Grep"]})).json()
    assert e["dept_id"] == d["id"] and e["mbti"] == "INFP"
    tree = (await client.get("/api/org")).json()
    mine = next(x for x in tree["departments"] if x["id"] == d["id"])
    assert any(x["id"] == e["id"] for x in mine["employees"]), "树里查不到新员工"
    e2 = (await client.patch(f"/api/org/employees/{e['id']}",
                             json={"status": "disabled",
                                   "persona": "改后的人设"})).json()
    assert e2["status"] == "disabled" and e2["persona"] == "改后的人设"
    assert (await client.delete(f"/api/org/employees/{e['id']}")).status_code == 200
    assert (await client.delete(f"/api/org/departments/{d['id']}")).status_code == 200
    gone = await client.delete(f"/api/org/employees/{e['id']}")
    assert gone.status_code == 404, "删两次应 404"


async def test_recruit_fallback_and_retire(client, monkeypatch):
    """>>> LLM 挂死→本地兜底生成 n 条（MBTI 白名单/名字唯一）+ 池退役。"""
    from loadn_webui.integrations import titlegen

    async def boom(system: str, user: str, max_tokens: int = 0) -> str:
        raise RuntimeError("LLM 不可用")

    monkeypatch.setattr(titlegen, "_chat", boom)
    r = await client.post("/api/org/recruit",
                          json={"n": 3, "brief": "懂数据合规的法务助理"})
    ws = r.json()["workers"]
    assert len(ws) == 3, "兜底必须补足 n 条"
    from loadn_webui.org import MBTI_TYPES
    names = [w["name"] for w in ws]
    assert len(set(names)) == 3, "名字必须唯一"
    assert all(w["mbti"] in MBTI_TYPES for w in ws), "MBTI 全部合法"
    assert all(w["id"] for w in ws), "入池必须有 id"
    pool = (await client.get("/api/org/pool", params={"state": "idle"})).json()["pool"]
    assert all(any(p["id"] == w["id"] for p in pool) for w in ws), "池里查得到"
    r2 = await client.delete(f"/api/org/pool/{ws[0]['id']}")
    assert r2.status_code == 200
    pool2 = (await client.get("/api/org/pool", params={"state": "idle"})).json()["pool"]
    assert not any(p["id"] == ws[0]["id"] for p in pool2), "退役后不再 idle"
