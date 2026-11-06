"""数字员工（DW-2）：引擎联动对赌——agent def 同步落盘 + 池优先取名。

对赌焦点：①init/CRUD 同步生成 $LOADN_HOME/agents/*.md（frontmatter
name/description/tools + persona 模板正文；disabled/删除即摘除文件）
②SubagentManager._pick_name 池优先（temp_pool.json 随机取、不撞名），
池空回落 AGENT_NAME_POOL 确定性轮转（行为兼容）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from loadn.constants import AGENT_NAME_POOL
from loadn.core.subagent import SubagentManager


def _db_with_org(tmp_path: Path) -> sqlite3.Connection:
    from loadn_webui import org as org_mod
    from loadn_webui.db import SCHEMA
    c = sqlite3.connect(tmp_path / "org.db")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    org_mod.init_org(c)
    return c


def test_agent_def_sync_lifecycle(tmp_path, monkeypatch):
    """>>> init 落 18 份 def；disabled/删除摘文件；persona 模板入正文。"""
    home = tmp_path / "home"
    monkeypatch.setenv("LOADN_HOME", str(home))
    c = _db_with_org(tmp_path)
    defs = sorted((home / "agents").glob("*.md"))
    assert len(defs) == 18, f"agent def 应 18 份，实为 {len(defs)}"
    fang = home / "agents" / "方维正.md"
    text = fang.read_text(encoding="utf-8")
    assert text.startswith("---"), "frontmatter 必须在首位"
    assert "name: 方维正" in text, "frontmatter name=员工真名（Task 点名键）"
    assert "法务部·法务顾问" in text, "description 带部门·头衔"
    assert "你是方维正，法务部法务顾问" in text, "persona 模板头部"
    assert "ESTJ" in text and "条框守护者" in text, "MBTI+traits 入正文"
    assert "先把丑话说在前面" in text, "人设原样保留"
    # 停用 → 摘除；删除 → 摘除
    from loadn_webui import org as org_mod
    emp = c.execute("SELECT id FROM org_employees WHERE name='方维正'").fetchone()
    org_mod.upsert_employee(c, {"status": "disabled"}, emp["id"])
    assert not fang.exists(), "disabled 员工 def 必须摘除（不注入 types）"
    org_mod.upsert_employee(c, {"status": "active"}, emp["id"])
    assert fang.exists(), "恢复在岗 def 回写"
    org_mod.delete_employee(c, emp["id"])
    assert not fang.exists(), "删除员工 def 同步摘除"


def test_agent_def_tools_and_model(tmp_path, monkeypatch):
    """>>> tools 白名单/model 进 frontmatter（引擎侧 tools/model 生效载荷）。"""
    home = tmp_path / "home"
    monkeypatch.setenv("LOADN_HOME", str(home))
    c = _db_with_org(tmp_path)
    from loadn_webui import org as org_mod
    dept = c.execute("SELECT id FROM org_departments WHERE name='研发部'").fetchone()
    e = org_mod.upsert_employee(c, {
        "dept_id": dept["id"], "name": "临时试验员", "title": "试验",
        "tools": ["Read", "Grep", "Bash"], "model": "glm-5.3-flash"})
    text = (home / "agents" / "临时试验员.md").read_text(encoding="utf-8")
    assert "tools: [Read, Grep, Bash]" in text
    assert "model: glm-5.3-flash" in text


def test_pick_name_pool_first(tmp_path):
    """>>> 池优先随机取（不撞名）；池空回落 AGENT_NAME_POOL 序。"""
    home = tmp_path / "ehome"
    home.mkdir()
    (home / "temp_pool.json").write_text(
        json.dumps({"names": ["林拾意", "江眠舟"]}, ensure_ascii=False),
        encoding="utf-8")
    mgr = SubagentManager(registry=None, provider_factory=None,
                          cwd=tmp_path, session_id="s", home=home)
    mgr._n = 1
    got = {mgr._pick_name() for _ in range(2)}
    assert got == {"林拾意", "江眠舟"}, f"两名池工应各取一次：{got}"
    mgr2 = SubagentManager(registry=None, provider_factory=None,
                           cwd=tmp_path, session_id="s", home=tmp_path / "none")
    mgr2._n = 1
    assert mgr2._pick_name() == AGENT_NAME_POOL[0], "池空回落序号轮转首名"
    mgr2._n = 2
    assert mgr2._pick_name() == AGENT_NAME_POOL[1], "回落序连续"
    # 坏 JSON 静默回落
    (home / "temp_pool.json").write_text("{bad json", encoding="utf-8")
    mgr3 = SubagentManager(registry=None, provider_factory=None,
                           cwd=tmp_path, session_id="s", home=home)
    mgr3._n = 1
    assert mgr3._pick_name() == AGENT_NAME_POOL[0], "坏池文件=空池回落"


def test_pick_name_forced_employee_identity(tmp_path):
    """>>> 人卡合一（DW 点名派发修复）：subagent_type 命中自定义员工类型时
    agent_name 用员工名（生产实证 9b71：点名郑明衡，卡片显示池工陈沐阳
    ——persona 生效但名字人格分裂）；内建类型不 forced 仍走池；同名
    防撞追加 ·N。"""
    import json as _json
    home = tmp_path / "h"
    home.mkdir()
    (home / "temp_pool.json").write_text(
        _json.dumps({"names": ["陈沐阳", "林晓悦"]}, ensure_ascii=False),
        encoding="utf-8")
    mgr = SubagentManager(
        registry=None, provider_factory=None, cwd=tmp_path, session_id="s",
        home=home,
        extra_types={"郑明衡": {"tools": None, "system_add": "产品分析师"},
                     "林知夏": {"tools": None, "system_add": "产品经理"}})
    mgr._n = 1
    # 点名员工：名字=员工名（不吃池）
    assert mgr._pick_name(forced="郑明衡") == "郑明衡"
    assert mgr._pick_name(forced="林知夏") == "林知夏"
    # 同名并发：防撞 ·N
    mgr._n = 3
    assert mgr._pick_name(forced="郑明衡") == "郑明衡·3"
    # 未 forced：池取名不变（回归）
    assert mgr._pick_name() in {"陈沐阳", "林晓悦"}
    # task() 派发链：custom_types 判据——点名员工的 Task 卡 agent_name=员工名
    assert mgr.custom_types == {"郑明衡", "林知夏"}
    assert "郑明衡" in mgr.types and "general" in mgr.types
