"""自定义 subagent（.claude/agents/*.md）：加载合并 / TaskTool 动态 enum / model。"""
from __future__ import annotations

from pathlib import Path

import tests.helpers as H
from loadn.core.agent_defs import load_agent_defs
from loadn.core.subagent import GENERAL_TOOLS, SUBAGENT_TYPES, SubagentManager, TaskTool


def _agent_md(path: Path, body: str, *, name: str = "", tools: str = "",
              model: str = "", description: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = ["---"]
    if name:
        fm.append(f"name: {name}")
    if description:
        fm.append(f"description: {description}")
    if tools:
        fm.append(f"tools: {tools}")
    if model:
        fm.append(f"model: {model}")
    fm.append("---")
    if len(fm) > 2:      # 有字段才写 frontmatter 块
        path.write_text("\n".join(fm) + "\n" + body)
    else:
        path.write_text(body)


def test_load_agent_defs_frontmatter_forms(tmp_path: Path):
    _agent_md(tmp_path / ".claude" / "agents" / "reviewer.md",
              "你是代码评审员。", name="reviewer",
              tools="Read, Grep, Glob", description="评审代码")
    _agent_md(tmp_path / ".claude" / "agents" / "writer.md",
              "你是文档写手。", name="writer", tools="[Read, Write]",
              model="glm-5.3-flash")
    _agent_md(tmp_path / ".claude" / "agents" / "default-tools.md",
              "缺省工具面。")     # 无 frontmatter：name=文件名、tools 缺省
    defs = load_agent_defs(tmp_path, home=tmp_path / "no-home")
    assert set(defs) == {"reviewer", "writer", "default-tools"}
    assert defs["reviewer"]["tools"] == ["Read", "Grep", "Glob"]
    assert defs["writer"]["tools"] == ["Read", "Write"]
    assert defs["writer"]["model"] == "glm-5.3-flash"
    assert defs["default-tools"]["tools"] is None       # task() 兜底 GENERAL_TOOLS
    assert defs["default-tools"]["system_add"] == "缺省工具面。"


def test_load_agent_defs_project_overrides_home(tmp_path: Path):
    home = tmp_path / "hh"
    _agent_md(home / "agents" / "deployer.md", "全局部署员。", name="deployer")
    _agent_md(tmp_path / ".claude" / "agents" / "deployer.md",
              "项目部署员。", name="deployer", tools="Bash")
    defs = load_agent_defs(tmp_path, home=home)
    assert defs["deployer"]["system_add"] == "项目部署员。"


def test_load_agent_defs_tolerates_garbled_file(tmp_path: Path):
    """乱码文件不炸：errors=replace 读入、name 回落文件名。"""
    d = tmp_path / ".claude" / "agents"
    d.mkdir(parents=True)
    (d / "broken.md").write_bytes(b"\xff\xfe\x00bad")
    (d / "good.md").write_text("---\nname: good\n---\n好的。")
    defs = load_agent_defs(tmp_path, home=tmp_path / "none")
    assert defs["good"]["system_add"] == "好的。"
    assert "broken" in defs   # 容错加载：回落文件名，不阻断其他定义


class _Registry:
    """SubagentManager 需要的最小 registry 面（对齐 test_subagent_streamjson）。"""

    def __init__(self, tools):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)


def _mk_mgr(tmp_path: Path, extra: dict | None = None, model_factory=None,
            provider=None) -> SubagentManager:
    return SubagentManager(
        registry=_Registry({"Echo": H.EchoTool()}),
        provider_factory=lambda: provider or H.ScriptedProvider([]),
        cwd=tmp_path, session_id="sess", home=tmp_path,
        extra_types=extra, model_provider_factory=model_factory)


async def test_tasktool_enum_includes_custom(tmp_path: Path):
    _agent_md(tmp_path / ".claude" / "agents" / "reviewer.md",
              "评审员。", name="reviewer", tools="Read")
    mgr = _mk_mgr(tmp_path, extra=load_agent_defs(tmp_path, home=tmp_path / "none"))
    tool = TaskTool(mgr)
    assert "reviewer" in tool.input_schema["properties"]["subagent_type"]["enum"]
    assert "general" in tool.input_schema["properties"]["subagent_type"]["enum"]
    assert "自定义：reviewer" in \
        tool.input_schema["properties"]["subagent_type"]["description"]


async def test_custom_type_dispatch_and_tools(tmp_path: Path):
    """自定义类型生效：tools 白名单收窄 + 正常派发。"""
    _agent_md(tmp_path / ".claude" / "agents" / "peeker.md",
              "只许回声。", name="peeker", tools="Echo")
    mgr = _mk_mgr(tmp_path,
                  extra=load_agent_defs(tmp_path, home=tmp_path / "n"),
                  provider=H.ScriptedProvider([H.text_round("peek 完成")]))
    out = await mgr.task("干个活", subagent_type="peeker", description="peek")
    assert out.startswith("peek 完成")
    assert mgr.types["peeker"]["tools"] == ["Echo"]
    assert mgr.types["general"]["tools"] == GENERAL_TOOLS


async def test_custom_type_model_factory(tmp_path: Path):
    """frontmatter 带 model → model_provider_factory 收到该模型。"""
    _agent_md(tmp_path / ".claude" / "agents" / "fast.md",
              "快枪手。", name="fast", model="glm-5.3-flash")
    seen: list[str] = []
    mgr = _mk_mgr(tmp_path,
                  extra=load_agent_defs(tmp_path, home=tmp_path / "n"),
                  model_factory=lambda m: (seen.append(m)
                                           or H.ScriptedProvider([])))
    await mgr.task("快跑", subagent_type="fast")
    assert seen == ["glm-5.3-flash"]


def test_builtin_types_still_default(tmp_path: Path):
    mgr = _mk_mgr(tmp_path)
    assert set(SUBAGENT_TYPES) <= set(mgr.types)


async def test_build_agent_wires_skill_and_agents(tmp_path, monkeypatch):
    """build_agent 集成：skills 非空注册 Skill 工具；.claude/agents 进 Task enum。"""
    from loadn.core.build import build_agent
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "hh"))
    (tmp_path / "hh").mkdir()
    _agent_md(tmp_path / ".claude" / "agents" / "reviewer.md",
              "评审员。", name="reviewer", tools="Read")
    skill = tmp_path / ".claude" / "skills" / "deploy"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: 部署\n---\n部署正文")
    bundle = await build_agent(tmp_path, cfg={"provider": "fake"})
    assert "Skill" in bundle.core.tools
    assert "deploy" in bundle.core.tools["Skill"].input_schema[
        "properties"]["command"]["enum"]
    assert "reviewer" in bundle.core.tools["Task"].input_schema[
        "properties"]["subagent_type"]["enum"]
    # system 索引改述：指引调 Skill 工具
    assert "调 Skill 工具" in bundle.core.assembler.build()


async def test_build_agent_no_skills_no_skill_tool(tmp_path, monkeypatch):
    from loadn.core.build import build_agent
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "hh2"))
    (tmp_path / "hh2").mkdir()
    # 隔离真实 ~/.claude/skills（用户全局 skill 共享特性会把它发现进来）
    fake_home = tmp_path / "fakehome2"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    bundle = await build_agent(tmp_path, cfg={"provider": "fake"})
    assert "Skill" not in bundle.core.tools


async def test_planner_gather_emits_task_cards(tmp_path):
    """并行扇出外发 Task 卡片事件（扇出期 UI 不再黑屏）。"""
    events = []
    mgr = _mk_mgr(tmp_path, provider=H.ScriptedProvider([H.text_round("子任务1结果")]))
    out = await mgr.gather(
        [type("ST", (), {"prompt": "调研比赛A", "subagent_type": "general"})()],
        emit=events.append)
    assert out and "子任务1结果" in out[0]
    kinds = [e["type"] for e in events]
    assert kinds == ["assistant", "tool_result"]  # 开始卡 + 结果卡（emitter 支持的形状）
    msg = events[0]["message"]
    blocks = msg.content if hasattr(msg, "content") else msg["content"]
    card = blocks[0].to_dict() if hasattr(blocks[0], "to_dict") else blocks[0]
    assert card["type"] == "tool_use" and card["name"] == "Task"
    assert card["id"] == "sub_1" and "调研比赛A" in card["input"]["prompt"]
    res = events[1]["block"]
    assert res.tool_use_id == "sub_1" and events[1]["name"] == "Task"
    assert "子任务1结果" in res.content and not res.is_error


async def test_subagent_activity_forwarded_with_tag(tmp_path):
    """子代理工具活动带「子N·」标签转发（归属可见）。"""
    events = []
    # 自定义 peeker 类型限定工具面 Echo（general 面没有测试专用 Echo 工具）
    _agent_md(tmp_path / ".claude" / "agents" / "peeker.md",
              "只许回声。", name="peeker", tools="Echo")
    prov = H.ScriptedProvider([H.tool_round("tu_x", "Echo", {"msg": "hi"}),
                               H.text_round("调研完成")])
    mgr = _mk_mgr(tmp_path, extra=load_agent_defs(tmp_path, home=tmp_path / "n"),
                  provider=prov)
    out = await mgr.task("查一下", subagent_type="peeker", emit=events.append)
    assert "调研完成" in out
    def _first_name(e):
        if e["type"] != "assistant":
            return e.get("name")
        msg = e["message"]
        blocks = msg.content if hasattr(msg, "content") else msg["content"]
        b0 = blocks[0]
        return b0.name if hasattr(b0, "name") else b0.get("name")

    names = [(e["type"], _first_name(e)) for e in events]
    assert ("assistant", "Task") in names                    # 开始卡
    assert ("assistant", "子1·Echo") in names                # 子代理工具卡（带标签）
    assert ("tool_result", "子1·Echo") in names              # 子代理工具结果（带标签）
    # task_result 块原样转发（tool_use_id 配对不变）
    tr = next(e for e in events if e["type"] == "tool_result" and e.get("name") == "子1·Echo")
    assert tr["block"].tool_use_id == "tu_x" and not tr["block"].is_error
    # 结束卡
    assert names[-1] == ("tool_result", "Task")   # 结束卡（emitter 支持形状）
