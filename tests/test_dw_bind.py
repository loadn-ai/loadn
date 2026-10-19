"""DW-7 数字员工能力三件套对赌：agent def 绑定键解析 / _bind_blocks
渲染（技能面·资源面·岗位记忆）/ 员工记忆域（e: 键）/ 召唤闭环（职责进
TaskTool 枚举描述）。"""
from __future__ import annotations

from pathlib import Path

from loadn import memorystore as ms
from loadn.core.agent_defs import load_agent_defs
from loadn.core.subagent import SubagentManager, TaskTool


def _def_md(path: Path, body: str, **fm) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path.write_text(f"---\n{head}\n---\n{body}", encoding="utf-8")


async def test_agent_def_bind_keys(tmp_path: Path):
    """frontmatter 三新键：skills/resources（逗号串）/memory（员工名）。"""
    _def_md(tmp_path / ".claude" / "agents" / "emp.md", "你是方维正。",
            name="方维正", skills="web-hands-on, file-parse",
            resources="foo:LOADN_SVC_FOO_URL+FOO_KEY, bar:BAR_TOKEN",
            memory="方维正")
    out = load_agent_defs(tmp_path, home=tmp_path / "none")
    d = out["方维正"]
    assert d["skills"] == ["web-hands-on", "file-parse"]
    assert d["resources"] == ["foo:LOADN_SVC_FOO_URL+FOO_KEY", "bar:BAR_TOKEN"]
    assert d["memory"] == "方维正"


async def test_bind_blocks_render(tmp_path: Path, monkeypatch):
    """绑定块：技能面（按绑定过滤+排除未绑定）/资源面（env 映射）/
    岗位记忆（e: 域 summary 注入）。"""
    # 技能目录：两个技能，只绑定其一
    for name, desc in (("web-hands-on", "真浏览器动手包"), ("file-parse", "文档解析")):
        sk = tmp_path / ".claude" / "skills" / name
        sk.mkdir(parents=True, exist_ok=True)
        (sk / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n正文", encoding="utf-8")
    # 员工记忆域：一条岗位记忆
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    d = ms.domain_dir_by_key("e:方维正", create=True)
    e = ms.create_entry(d, "合同审查走三步流程", "先查黑名单再过条款",
                        actor="test")
    assert e and e["id"]

    mgr = SubagentManager(registry=type("R", (), {"get": staticmethod(lambda n: None)})(),
                          provider_factory=lambda: None, cwd=tmp_path,
                          session_id="s", home=tmp_path / "home",
                          extra_types={"方维正": {
                              "tools": None, "system_add": "你是方维正。",
                              "model": None, "description": "",
                              "skills": ["web-hands-on"],
                              "resources": ["foo:LOADN_SVC_FOO_URL"],
                              "memory": "方维正"}})
    bind = mgr._bind_blocks(mgr.types["方维正"])
    assert "## 你的技能面（只可用下列技能）" in bind
    assert "web-hands-on：真浏览器动手包" in bind
    assert "file-parse" not in bind          # 未绑定技能不进面
    assert "## 你的资源面" in bind
    assert "foo：环境变量 LOADN_SVC_FOO_URL" in bind
    assert "## 岗位长期记忆" in bind
    assert "合同审查走三步流程" in bind

    # 反例：无绑定 conf → 零块
    plain = SubagentManager(registry=type("R", (), {"get": staticmethod(lambda n: None)})(),
                            provider_factory=lambda: None, cwd=tmp_path,
                            session_id="s", home=tmp_path / "home",
                            extra_types={"普通": {"tools": None, "system_add": "x",
                                               "model": None, "description": "",
                                               "skills": None, "resources": None,
                                               "memory": None}})
    assert plain._bind_blocks(plain.types["普通"]) == ""


async def test_employee_domain_store(tmp_path: Path, monkeypatch):
    """员工域 e: 键：create/entries/枚举（有 manifest 才入列）。"""
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    assert ms.domain_dir_by_key("e:无名氏") is None      # 先有写入才有域
    d = ms.domain_dir_by_key("e:方维正", create=True)
    ms.create_entry(d, "职责一", "内容一", actor="test")
    es = ms.entries_of(d)
    assert len(es) == 1 and es[0]["summary"] == "职责一"
    keys = ms.list_domain_keys()
    assert "e:方维正" in keys and "e:无名氏" not in keys


async def test_type_help_duty(tmp_path: Path):
    """召唤闭环：员工 def 的 description（含职责）进 TaskTool 枚举描述。"""
    _def_md(tmp_path / ".claude" / "agents" / "emp.md", "你是方维正。",
            name="方维正", description="法务部·法务顾问｜职责：合规审查、风险条款")
    from tests.test_subagent_custom import _mk_mgr
    mgr = _mk_mgr(tmp_path, extra=load_agent_defs(tmp_path, home=tmp_path / "none"))
    tool = TaskTool(mgr)
    desc = tool.input_schema["properties"]["subagent_type"]["description"]
    assert "方维正=法务部·法务顾问｜职责：合规审查、风险条款" in desc
    assert "按职责选人" in desc


async def test_org_upsert_bindings_write_def(tmp_path: Path, monkeypatch):
    """DW-7 webui 链：绑定落库（JSON 列）→ sync_agent_def 写三新键
    frontmatter；未知名校验 400；空数组=清绑定。"""
    import sqlite3
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    from loadn_webui import org as org_mod
    from loadn_webui.db import SCHEMA
    c = sqlite3.connect(tmp_path / "org.db")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    org_mod.init_org(c)
    # 校验拒绝：不存在的技能名
    try:
        org_mod.upsert_employee(c, {"name": "测试员", "skills": ["no-such"]})
        raise AssertionError("未知名应被拒")
    except ValueError as e:
        assert "技能不存在" in str(e)
    # 正常建员带绑定
    emp = org_mod.upsert_employee(c, {
        "name": "测试员", "title": "专员", "duty": "专管绑定",
        "skills": ["web-hands-on"], "resources": []})
    eid = emp["id"]
    deffile = (tmp_path / "home" / "agents" / org_mod._def_filename("测试员"))
    text1 = deffile.read_text(encoding="utf-8")
    assert "skills: web-hands-on" in text1
    assert "memory: 测试员" in text1
    assert "resources:" not in text1          # 空绑定不写键
    assert "职责：专管绑定" in text1
    # 清绑定 → 键消失
    org_mod.upsert_employee(c, {"skills": []}, emp_id=eid)
    text2 = deffile.read_text(encoding="utf-8")
    assert "skills:" not in text2
    row = c.execute("SELECT skills FROM org_employees WHERE id=?",
                    (eid,)).fetchone()
    assert row["skills"] is None


async def test_employee_memory_routes(client):
    """DW-7 路由面：员工域经 /api/memory/* 全 CRUD（admin 双头放行）+
    domains 枚举带 e: 键与 label。"""
    r = await client.post("/api/memory/file", json={
        "domain": "e:方维正", "summary": "合同审查三步流程",
        "content": "先查黑名单，再过条款，最后留痕"})
    assert r.status_code == 200, r.text
    eid = r.json()["entry"]["id"]
    r = await client.get("/api/memory/entries", params={"domain": "e:方维正"})
    assert r.status_code == 200
    assert any(e["id"] == eid for e in r.json()["entries"])
    r = await client.get("/api/memory/domains")
    assert r.status_code == 200
    keys = r.json()["domains"]
    assert "e:方维正" in keys
    label = r.json()["info"]["e:方维正"]["label"]
    assert "方维正" in label              # 种子在库=员工·名；无种子=员工域 名
    # 删除即清（域可重建）
    r = await client.delete("/api/memory/entry",
                             params={"domain": "e:方维正", "id": eid})
    assert r.status_code == 200
