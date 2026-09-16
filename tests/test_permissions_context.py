"""PermissionEngine / ContextAssembler / Compactor / SubagentManager 单测。"""
from __future__ import annotations

import json

import tests.helpers as H
from hahaness.core.compactor import Compactor, _split_rounds
from hahaness.core.context import ContextAssembler
from hahaness.core.permissions import PermissionEngine
from hahaness.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------- 权限
def test_bypass_allows_but_deny_still_enforced():
    pe = PermissionEngine(mode="bypassPermissions", deny=["WebFetch"])
    # deny 是显式策略：bypass 也拦（宿主 profile 禁用纪律）
    assert not pe.check("WebFetch", {"url": "https://x"}).allowed
    # 未列入 deny 的在 bypass 下全放行
    assert pe.check("Bash", {"command": "rm -rf /"}).allowed


def test_deny_short_circuits():
    pe = PermissionEngine(mode="default", deny=["Bash:git push*"],
                          allow=["Bash:git status"])
    d = pe.check("Bash", {"command": "git push origin main"})
    assert not d.allowed and "权限规则拒绝" in d.reason
    # 非命中 deny 的走 allow 规则放行
    assert pe.check("Bash", {"command": "git status"}).allowed


def test_allow_rule_then_ask_denies_headless():
    pe = PermissionEngine(mode="default", allow=["Read"])
    assert pe.check("Read", {"file_path": "/x"}).allowed
    d = pe.check("Write", {"file_path": "/x", "content": "y"})
    assert not d.allowed and "已阻止" in d.reason


def test_accept_edits_allows_write():
    pe = PermissionEngine(mode="acceptEdits")
    assert pe.check("Write", {"file_path": "/x", "content": "y"}).allowed
    assert not pe.check("Bash", {"command": "ls"}).allowed   # 仍 ask→deny


def test_plan_mode_readonly():
    pe = PermissionEngine(mode="plan")
    assert pe.check("Read", {"file_path": "/x"}).allowed
    assert not pe.check("Write", {"file_path": "/x", "content": "y"}).allowed
    assert pe.check("Bash", {"command": "ls -la"}).allowed
    assert not pe.check("Bash", {"command": "rm x"}).allowed


def test_rules_from_settings_file(tmp_path):
    agent = tmp_path / ".agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps(
        {"permissions": {"deny": ["WebFetch"]}}))
    pe = PermissionEngine.load(tmp_path, mode="default")
    assert not pe.check("WebFetch", {"url": "https://x"}).allowed


# ---------------------------------------------------------------- 上下文
def test_context_sections_order(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# 宪法\n这是工作区规则")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.py").write_text("1")
    asm = ContextAssembler(tmp_path, tools=["Bash", "Read", "Echo"])
    out = asm.build()
    i_core = out.index("工程 agent")
    i_tools = out.index("## 工具使用要点")
    i_const = out.index("## 项目宪法")
    i_env = out.index("## 环境")
    assert i_core < i_tools < i_const < i_env
    assert "这是工作区规则" in out
    assert "cwd:" in out and "sub/" in out
    assert "- Echo：Bash：" not in out   # 未知工具无要点行，不炸


def test_context_hidden_dirs_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "real.txt").write_text("x")
    asm = ContextAssembler(tmp_path)
    out = asm.build()
    assert "node_modules" not in out.split("## 环境")[1]
    assert "real.txt" in out


def test_skills_index_frontmatter(tmp_path):
    sk = tmp_path / ".claude" / "skills" / "demo"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示技能\n---\nBODY_MARKER_XYZ")
    out = ContextAssembler(tmp_path).build()
    assert "- demo：演示技能" in out
    assert "BODY_MARKER_XYZ" not in out   # 只索引不进正文（渐进披露）


# ---------------------------------------------------------------- 压缩
def _rounds_messages(n: int) -> list[Message]:
    msgs: list[Message] = [Message(role="user", content=[TextBlock(text="开始")])]
    for i in range(n):
        msgs.append(Message(role="assistant", content=[
            ToolUseBlock(id=f"tu_{i}", name="Echo", input={"msg": str(i)})]))
        msgs.append(Message(role="user", content=[
            ToolResultBlock(tool_use_id=f"tu_{i}", content=f"echo: {i}")]))
        msgs.append(Message(role="assistant", content=[TextBlock(text=f"轮{i}完")]))
    return msgs


def test_split_rounds_pairs_tool_use_with_result():
    msgs = _rounds_messages(3)
    rounds = _split_rounds(msgs)
    # 轮 = 一个 assistant 起点到下一个 assistant 之前；开头孤立 user 自成一轮
    # n=3 → [U0] + [A(tool) R] + [A(text)] ×3 = 7 轮
    assert len(rounds) == 2 * 3 + 1
    # 关键性质：含 tool_use 的轮必含其 tool_result（配对不拆散）
    for r in rounds:
        tus = {b.id for m in r for b in m.content if isinstance(b, ToolUseBlock)}
        if tus:
            trs = {b.tool_use_id for m in r for b in m.content
                   if isinstance(b, ToolResultBlock)}
            assert tus <= trs


async def test_compact_keeps_recent_and_summarizes():
    prov = H.ScriptedProvider([H.text_round("这是接力摘要")])
    comp = Compactor(prov)
    msgs = _rounds_messages(30)
    out, did = await comp.compact(msgs)
    assert did
    head = out[0].text_parts()
    assert "上下文已压缩" in head and "接力摘要" in head
    assert len(out) < len(msgs)
    # 保留尾部若干轮
    assert any(m.text_parts() == "轮29完" for m in out)


async def test_compact_noop_below_threshold():
    comp = Compactor(H.ScriptedProvider([]))
    msgs = _rounds_messages(3)
    out, did = await comp.maybe_compact(msgs, {"input_tokens": 100}, 200_000)
    assert not did and out is msgs


async def test_compact_fallback_on_provider_error():
    from hahaness.providers import Chunk
    prov = H.ScriptedProvider([[Chunk(kind="error", error="down")]])
    comp = Compactor(prov)
    msgs = _rounds_messages(30)
    out, did = await comp.compact(msgs)
    assert did and "自动硬摘要" in out[0].text_parts()


def test_prompt_has_anti_reconnoitering_discipline():
    """blind-maze 案回归：system 提示含「不侦查测试」纪律。"""
    from hahaness.core.context import CORE_PROMPT
    assert "不侦查测试" in CORE_PROMPT
    assert "monkeypatch" in CORE_PROMPT
