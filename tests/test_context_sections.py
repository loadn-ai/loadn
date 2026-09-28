"""P2-6 Context section 化验收（卡面 4 项）。

①节开关：context_sections.json 关 skills 节 → system 无 skills 段
②压缩优先级：伪造超限上下文 → 宪法/CLAUDE.md 存活、skills 索引被裁
  （预算按实测节宽设定——只够裁一节，验证次序而非裁光）
③组装失败降级：单节构建抛异常只跳过该节 + 不崩整个组装
④默认行为不破：无配置时节序=旧行为（core<tools<constitution<…<env<skills）
外加：节内 [section truncated] 预算标注；order 覆盖；last_sections
元数据（P2-3 打样面）；项目级 .loadn 覆盖全局。

隔离：LOADN_HOME/Path.home 全打假——真 ~/.claude 记忆与全局 MEMORY.md
不泄入断言（P3-5b 同款教训）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadn.core.context import SECTION_SPECS, ContextAssembler


@pytest.fixture(autouse=True)
def _isolation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("LOADN_HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))


def _skill(tmp_path: Path, name: str = "demo") -> None:
    sk = tmp_path / ".claude" / "skills" / name
    sk.mkdir(parents=True, exist_ok=True)
    (sk / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} 描述\n---\n正文")


def _cfg(tmp_path: Path, data: dict, *, project: bool = False) -> Path:
    if project:
        p = tmp_path / ".loadn" / "context_sections.json"
    else:
        from loadn import loadn_home
        p = loadn_home() / "context_sections.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------- ④ 默认
def test_default_order_unchanged(tmp_path):
    """默认节序=旧行为（P2-6 前的追加顺序逐位一致）。"""
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n规则")
    _skill(tmp_path)
    out = ContextAssembler(tmp_path, tools=["Bash"]).build()
    idx = [out.find(m) for m in
           ("工程 agent", "## 工具使用要点", "## 宪法", "## 环境", "## 可用 Skills")]
    assert all(i >= 0 for i in idx)
    assert idx == sorted(idx)                    # 顺序不变


def test_spec_table_priorities():
    """宪法/核心永不裁；skills 先裁；env 次之。"""
    by = {s.id: s for s in SECTION_SPECS}
    assert by["core"].prune_priority == 0
    assert by["constitution"].prune_priority == 0
    assert by["skills"].prune_priority > by["env"].prune_priority \
        > by["memory"].prune_priority > by["tools"].prune_priority


# ---------------------------------------------------------------- ① 节开关
def test_section_toggle_off(tmp_path):
    _skill(tmp_path)
    _cfg(tmp_path, {"skills": {"enabled": False}})
    out = ContextAssembler(tmp_path).build()
    assert "可用 Skills" not in out and "demo" not in out
    # 开回来（项目级覆盖全局）
    _cfg(tmp_path, {"skills": {"enabled": True}}, project=True)
    out2 = ContextAssembler(tmp_path).build()
    assert "可用 Skills" in out2


def test_unknown_section_id_ignored(tmp_path):
    _cfg(tmp_path, {"no-such-section": {"enabled": False},
                    "skills": {"budget_tokens": "bad"}})
    out = ContextAssembler(tmp_path, tools=["Bash"]).build()
    assert "工具使用要点" in out            # 笔误节/坏值不炸组装


# ---------------------------------------------------------------- ② 压缩优先级
def _budget_after_dropping(asm: ContextAssembler, *ids: str) -> int:
    """实测组装后返回「只丢掉 ids 各节即回预算内」的 token 预算值。

    代码比较口径是 total_chars <= budget*2——取 ceil 保证丢完即达标
    （floor 会差 1 字符继续连坐下一节）。须在 env 节定长前提下用
    （目录树跨 build 可变——prune 测试先 monkeypatch env_block 定长）。
    """
    asm.build()
    total = sum(m["chars"] for m in asm.last_sections)
    drop = sum(m["chars"] for m in asm.last_sections if m["id"] in ids)
    return (total - drop + 1) // 2


_FIXED_ENV = "## 环境\n- cwd: /fixed"


def test_prune_priority_constitution_survives(tmp_path, monkeypatch):
    """超限 → skills 索引被裁；宪法/CLAUDE.md 存活。"""
    monkeypatch.setattr(ContextAssembler, "env_block",
                        lambda self: _FIXED_ENV)
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n重要规则不许裁")
    _skill(tmp_path, "alpha")
    _skill(tmp_path, "beta")
    budget = _budget_after_dropping(ContextAssembler(tmp_path), "skills")
    monkeypatch.setattr("loadn.core.context.CONTEXT_BUDGET_TOKENS", budget)
    asm = ContextAssembler(tmp_path, tools=["Bash"])
    out = asm.build()
    assert "重要规则不许裁" in out                    # 宪法存活
    assert "工程 agent" in out                       # 核心身份存活
    assert "可用 Skills" not in out                  # skills 索引被裁
    by = {m["id"]: m for m in asm.last_sections}
    assert by["skills"]["dropped"] is True
    assert by["constitution"]["dropped"] is False


def test_prune_order_skills_then_env(tmp_path, monkeypatch):
    """skills 先裁；再超才轮到 env。"""
    monkeypatch.setattr(ContextAssembler, "env_block",
                        lambda self: _FIXED_ENV)
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n规则")
    _skill(tmp_path)
    budget = _budget_after_dropping(ContextAssembler(tmp_path), "skills")
    monkeypatch.setattr("loadn.core.context.CONTEXT_BUDGET_TOKENS", budget)
    asm = ContextAssembler(tmp_path, tools=[])
    out = asm.build()
    assert "可用 Skills" not in out
    assert "cwd:" in out                             # 还没轮到 env
    by = {m["id"]: m for m in asm.last_sections}
    assert by["skills"]["dropped"] and not by["env"]["dropped"]


# ---------------------------------------------------------------- ③ 组装降级
def test_section_build_failure_degrades(tmp_path, monkeypatch):
    """单节构建抛异常：只跳过该节，其余组装照常。"""
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n规则")

    def boom(self):
        raise RuntimeError("环境节炸了")

    monkeypatch.setattr(ContextAssembler, "env_block", boom)
    out = ContextAssembler(tmp_path, tools=["Bash"]).build()
    assert "## 环境" not in out                      # 炸的节缺席
    assert "工程 agent" in out and "宪法" in out     # 其余存活


# ---------------------------------------------------------------- 节内预算
def test_section_budget_truncation_marker(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("宪法内容" * 500)   # ~2000 字
    _cfg(tmp_path, {"constitution": {"budget_tokens": 100}})  # 200 chars
    asm = ContextAssembler(tmp_path)
    out = asm.build()
    assert "[section truncated]" in out
    by = {m["id"]: m for m in asm.last_sections}
    assert by["constitution"]["truncated"] is True
    # 宪法节内截断 ≠ 被裁（dropped 仍 False——永不整节裁）
    assert by["constitution"]["dropped"] is False


def test_order_override(tmp_path):
    """order 覆盖：env 提到 tools 前；未列出的节保持默认序殿后。"""
    _cfg(tmp_path, {"order": ["core", "env", "tools"]})
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n规则")
    out = ContextAssembler(tmp_path, tools=["Bash"]).build()
    assert out.index("## 环境") < out.index("## 工具使用要点")
    assert out.index("工程 agent") < out.index("## 环境") < out.index("## 宪法")


# ---------------------------------------------------------------- 打样面
def test_last_sections_metadata(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n规则")
    _skill(tmp_path)
    asm = ContextAssembler(tmp_path, tools=["Bash"])
    out = asm.build()
    ids = [m["id"] for m in asm.last_sections]
    assert ids == ["core", "tools", "constitution", "env", "skills"]
    assert all(set(m) == {"id", "chars", "truncated", "dropped"}
               for m in asm.last_sections)
    # 无裁剪时：join 语义可复原（chars 和 + 分隔符 = 总长）
    assert sum(m["chars"] for m in asm.last_sections) + 2 * (len(ids) - 1) \
        == len(out)
