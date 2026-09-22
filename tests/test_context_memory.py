"""宪法祖先链 + @import + Skill 工具 + skills 索引（零 token）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from loadn.core.agent_defs import load_agent_defs
from loadn.core.context import ContextAssembler, _chain_files, _memory_boundary
from loadn.core.skills import discover_skills, load_skill_body
from loadn.tools.skill import SkillTool


# ---------------------------------------------------------------- 边界与链
def test_memory_boundary_git_root(tmp_path: Path):
    """git repo 内：上界 = repo 根（.git 是 dir）。"""
    repo = tmp_path / "repo"
    (repo / "a" / "b").mkdir(parents=True)
    (repo / ".git").mkdir()
    assert _memory_boundary(repo / "a" / "b", tmp_path) == repo


def test_memory_boundary_git_file_worktree(tmp_path: Path):
    """worktree 形态 .git 是 file，同样识别为根。"""
    repo = tmp_path / "wt"
    (repo / "sub").mkdir(parents=True)
    (repo / ".git").write_text("gitdir: /somewhere\n")
    assert _memory_boundary(repo / "sub", tmp_path) == repo


def test_memory_boundary_home(tmp_path: Path):
    home = tmp_path / "home"
    proj = home / "projects" / "p"
    proj.mkdir(parents=True)
    assert _memory_boundary(proj, home) == home


def test_memory_boundary_neither_cwd_only(tmp_path: Path):
    bare = tmp_path / "bare"
    bare.mkdir()
    other_home = tmp_path / "not-parent"
    other_home.mkdir()
    assert _memory_boundary(bare, other_home) == bare


def test_chain_files_order_near_last(tmp_path: Path):
    """远→近收集；CLAUDE.md 优先 AGENTS.md；每层取一。"""
    root = tmp_path / "repo"
    deep = root / "a" / "b"
    deep.mkdir(parents=True)
    (root / "CLAUDE.md").write_text("root 宪法")
    (root / "a" / "AGENTS.md").write_text("a 层")
    (deep / "CLAUDE.md").write_text("b 层")
    (deep / "AGENTS.md").write_text("b 层 agents")   # 同层被 CLAUDE.md 压掉
    files = _chain_files(root, deep)
    assert [f.parent for f in files] == [root, root / "a", deep]
    assert files[1].name == "AGENTS.md"


# ---------------------------------------------------------------- 组装
async def test_constitution_chain_and_user_level(tmp_path: Path, monkeypatch):
    root = tmp_path / "repo"
    deep = root / "pkg"
    deep.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "CLAUDE.md").write_text("根规则")
    (deep / "CLAUDE.md").write_text("包规则")
    home = tmp_path / "hh"
    home.mkdir()
    (home / "AGENT.md").write_text("用户级规则")
    monkeypatch.setattr("loadn.core.context.loadn_home", lambda: home)
    asm = ContextAssembler(deep)
    block = asm.constitution_block()
    pos_user = block.index("用户级规则")
    pos_root = block.index("根规则")
    pos_pkg = block.index("包规则")
    assert 0 < pos_user < pos_root < pos_pkg   # 用户级置顶、远→近
    assert "包规则" in block


def test_import_expansion_relative_and_missing(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text(
        "主文件\n@./rules/common.md\n@./no-such.md\n")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "common.md").write_text("公共条款")
    from loadn.core.context import _expand_imports
    out = _expand_imports((tmp_path / "CLAUDE.md").read_text(), tmp_path)
    assert "公共条款" in out
    assert "@import 未解析：./no-such.md" in out


def test_import_cycle_and_depth(tmp_path: Path):
    from loadn.core.context import _expand_imports
    (tmp_path / "A.md").write_text("A 开头\n@./B.md\n")
    (tmp_path / "B.md").write_text("B 开头\n@./A.md\n")
    out = _expand_imports((tmp_path / "A.md").read_text(), tmp_path)
    assert "A 开头" in out and "B 开头" in out   # 环不炸（seen 截断）
    # 深度链 a→b→c→d→e：MEMORY_IMPORT_DEPTH=3 → a 展开到第 3 层 import（d），
    # e 不进（留注释）、原始 @ 引用不泄漏
    for n in "abcde":
        nxt = chr(ord(n) + 1)
        (tmp_path / f"{n}.md").write_text(f"[{n}]\n@./{nxt}.md\n")
    out = _expand_imports((tmp_path / "a.md").read_text(), tmp_path)
    assert "[a]" in out and "[d]" in out
    assert "[e]" not in out
    assert "超深度未展开" in out


# ---------------------------------------------------------------- Skill 工具
def _mk_skill(base: Path, name: str, desc: str, body: str) -> None:
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n{body}")


def test_discover_skills_dedup_priority(tmp_path: Path, monkeypatch):
    """同名去重：.claude 优先 .agent 优先 home（先到先得）。

    home 指向空目录隔离——真实 ~/.claude/skills 里有用户全局 skill 会混入。
    """
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    _mk_skill(tmp_path / ".claude" / "skills", "deploy", "项目版", "项目正文")
    _mk_skill(tmp_path / ".agent" / "skills", "deploy", "agent 版", "x")
    _mk_skill(tmp_path / ".agent" / "skills", "audit", "审计", "审计正文")
    skills = discover_skills(tmp_path)
    assert set(skills) == {"deploy", "audit"}
    assert skills["deploy"].description == "项目版"


async def test_skill_tool_loads_body(tmp_path: Path):
    _mk_skill(tmp_path / ".claude" / "skills", "deploy", "部署",
              "按 $ARGUMENTS 环境执行")
    skills = discover_skills(tmp_path)
    tool = SkillTool(skills)
    out = await tool.execute({"command": "deploy", "args": "prod"}, None)
    assert "按 prod 环境执行" in out
    assert "deploy" in out
    assert "description" not in out or "部署" not in out   # frontmatter 已剥


async def test_skill_tool_unknown_rejected(tmp_path: Path):
    from loadn.tools.base import ToolError
    tool = SkillTool({})
    with pytest.raises(ToolError):
        await tool.execute({"command": "nope"}, None)


def test_load_skill_body_clips():
    from loadn.core.skills import SkillInfo
    info = SkillInfo(name="big", description="", path=Path("/dev/null"))
    # 用真实临时文件测 clip
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "SKILL.md"
        p.write_text("---\nname: big\n---\n" + "x" * 20_000)
        info.path = p
        body = load_skill_body(info, max_chars=1000)
        assert len(body) < 1100 and "截断" in body


# ---------------------------------------------------------------- Claude 资源共享
def test_user_constitution_falls_back_to_claude(tmp_path, monkeypatch):
    """无 AGENT.md 时回退读 ~/.claude/CLAUDE.md（双引擎共享用户宪法）。"""
    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    (fake_home / ".claude" / "CLAUDE.md").write_text("Claude 用户宪法内容")
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "hh"))   # 无 AGENT.md
    (tmp_path / "hh").mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    from loadn.core.context import ContextAssembler
    block = ContextAssembler(tmp_path / "ws").constitution_block()
    assert "Claude 用户宪法内容" in block


def test_claude_global_skills_discovered(tmp_path, monkeypatch):
    """~/.claude/skills 的用户全局 skill 进发现面（先到先得去重）。"""
    fake_home = tmp_path / "home"
    _mk = fake_home / ".claude" / "skills"
    _mk_skill(_mk, "global-tool", "Claude 全局技能", "全局正文")
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    skills = discover_skills(tmp_path)
    assert "global-tool" in skills
    # 项目级同名覆盖全局
    _mk_skill(tmp_path / ".claude" / "skills", "global-tool", "项目覆盖版", "x")
    assert discover_skills(tmp_path)["global-tool"].description == "项目覆盖版"


def test_claude_global_agents_discovered(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    _agent_md_dir = fake_home / ".claude" / "agents"
    _agent_md_dir.mkdir(parents=True)
    (_agent_md_dir / "global-helper.md").write_text(
        "---\nname: global-helper\n---\n全局代理。")
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    defs = load_agent_defs(tmp_path, home=tmp_path / "none")
    assert "global-helper" in defs


def test_claude_project_memory_loaded(tmp_path, monkeypatch):
    """per-project ~/.claude/projects/<slug>/memory/MEMORY.md 进记忆块。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)
    from loadn.core.context import _claude_project_memory
    ws = tmp_path / "ws" / "proj"
    ws.mkdir(parents=True)
    mem = _claude_project_memory(ws)          # 与实现同源定位（slug 全路径推导）
    mem.parent.mkdir(parents=True)
    mem.write_text("# 项目记忆\n- 偏好 A")
    monkeypatch.setattr("loadn.core.context.loadn_home",
                        lambda: tmp_path / "hh")   # 无全局记忆
    (tmp_path / "hh").mkdir()
    from loadn.core.context import ContextAssembler
    out = ContextAssembler(ws).build()
    assert "项目记忆" in out and "偏好 A" in out


def test_claude_project_memory_slug_rule():
    from loadn.core.context import _claude_project_memory
    p = _claude_project_memory(Path("/data/code/workdaddy"))
    assert p == Path.home() / ".claude" / "projects" / "-data-code-workdaddy" / "memory" / "MEMORY.md"
