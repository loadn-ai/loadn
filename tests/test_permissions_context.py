"""PermissionEngine / ContextAssembler / Compactor / SubagentManager 单测。"""
from __future__ import annotations

import json

import tests.helpers as H
from loadn.core.compactor import Compactor, _split_rounds
from loadn.core.context import ContextAssembler
from loadn.core.permissions import PermissionEngine
from loadn.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


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
    from loadn.providers import Chunk

    class _AlwaysError:
        model_name = "err"

        async def chat(self, messages, tools, system, *, stream=True,
                       model=None, use_cache=True):
            yield Chunk(kind="error", error="down")

    comp = Compactor(_AlwaysError())
    msgs = _rounds_messages(30)
    out, did = await comp.compact(msgs)
    assert did and "自动硬摘要" in out[0].text_parts()   # 重试一次仍败 → 降级


async def test_compact_retry_once_then_success():
    """首次摘要 error chunk → 减半重试成功（不再直接降级硬摘要）。"""
    from loadn.providers import Chunk
    prov = H.ScriptedProvider([[Chunk(kind="error", error="down")]])
    comp = Compactor(prov)
    msgs = _rounds_messages(30)
    out, did = await comp.compact(msgs)
    assert did and "scripted done" in out[0].text_parts()
    assert prov.i == 2                       # 恰两次 chat：失败 + 重试


def test_prompt_has_anti_reconnoitering_discipline():
    """blind-maze 案回归：system 提示含「不侦查测试」纪律。"""
    from loadn.core.context import CORE_PROMPT
    assert "不侦查测试" in CORE_PROMPT
    assert "monkeypatch" in CORE_PROMPT


async def test_compactor_passes_small_model():
    """small_model 配置 → 摘要调用的 per-call model 覆盖透传到 provider。"""
    from loadn.providers import Chunk
    from loadn.providers.fake import FakeProvider
    prov = FakeProvider(script=[[Chunk(kind="text_delta", text="小模型摘要")]])
    comp = Compactor(prov, small_model="glm-5.3-flash")
    out, did = await comp.compact(_rounds_messages(30))
    assert did and "小模型摘要" in out[0].text_parts()
    assert prov.last_model == "glm-5.3-flash"
    assert prov.last_use_cache is False    # 摘要走 no-cache 通道


async def test_compactor_default_model_when_unset():
    from loadn.providers import Chunk
    from loadn.providers.fake import FakeProvider
    prov = FakeProvider(script=[[Chunk(kind="text_delta", text="同模型摘要")]])
    comp = Compactor(prov)
    await comp.compact(_rounds_messages(30))
    assert prov.last_model is None


def test_prompt_has_timeout_death_disciplines():
    """Terminal-Bench 死法①③的提示词纪律回归：auto-bg 语义 + make -j。"""
    from loadn.core.context import CORE_PROMPT
    assert "自动转后台" in CORE_PROMPT          # 死法①：60s auto-bg 语义
    assert "先继续干" in CORE_PROMPT            # 转后台后不空转轮询
    assert "-j$(nproc)" in CORE_PROMPT          # 死法③：编译必并行


# ---------------------------------------------------------------- 压缩四件套
def _big_round_messages(n: int, big_chars: int = 5000) -> list[Message]:
    msgs = [Message(role="user", content=[TextBlock(text="开始")])]
    for i in range(n):
        msgs.append(Message(role="assistant", content=[
            ToolUseBlock(id=f"tu_{i}", name="Read",
                         input={"file_path": f"/tmp/f{i}.py"})]))
        msgs.append(Message(role="user", content=[
            ToolResultBlock(tool_use_id=f"tu_{i}",
                            content="x" * big_chars)]))
        msgs.append(Message(role="assistant", content=[TextBlock(text=f"轮{i}完")]))
    return msgs


async def test_prune_renders_without_mutating():
    """先裁剪后总结：渲染层骨架化，Message 原对象深比较不变。"""
    import copy

    from loadn.core.compactor import _render_history
    msgs = _big_round_messages(3)
    snapshot = copy.deepcopy([m.to_dict() for m in msgs])
    rendered = _render_history(msgs)
    assert "…[pruned]…" in rendered          # 5000 字符结果被骨架化
    assert [m.to_dict() for m in msgs] == snapshot   # 未 mutate


async def test_file_ledger_extraction():
    from loadn.core.compactor import _file_ledger
    msgs = _big_round_messages(2)
    msgs.append(Message(role="assistant", content=[
        ToolUseBlock(id="tu_w", name="Edit", input={"file_path": "/tmp/f0.py"})]))
    ledger = _file_ledger(msgs)
    assert "readFiles: /tmp/f0.py, /tmp/f1.py" in ledger
    assert "modifiedFiles: /tmp/f0.py" in ledger


async def test_compact_update_mode_uses_prev_summary():
    """有旧摘要 → UPDATE 模板（请求体含旧摘要文本）。"""
    prov = H.ScriptedProvider([H.text_round("更新版摘要")])
    comp = Compactor(prov)
    out, did = await comp.compact(
        _rounds_messages(30), prev_summary="旧摘要：目标是 X")
    assert did and "更新版摘要" in out[0].text_parts()
    req_text = prov.calls[0][0].text_parts()
    assert "旧摘要：目标是 X" in req_text
    assert "更新版交接摘要" in req_text


async def test_compact_token_budget_cutpoint():
    """token 预算切点：3 个巨型轮 + 若干小轮 → 按体积保留而非固定 20 轮。"""
    prov = H.ScriptedProvider([H.text_round("摘要")])
    comp = Compactor(prov)
    msgs = _big_round_messages(8, big_chars=30_000)   # 每轮 ~30k 字符
    out, did = await comp.compact(msgs)
    assert did
    # 保留轮的总体积 ≤ COMPACT_KEEP_TOKENS*1.6 + 单轮余量；丢掉的大结果不进保留窗
    kept = [m for m in out[1:]]
    assert len(kept) < len(msgs)
    assert prov.i == 1


async def test_compact_clip_scales_with_window():
    """小窗口 → 摘要 history clip 随之缩小（防压缩本身超限）。"""
    prov = H.ScriptedProvider([H.text_round("短摘要")])
    comp = Compactor(prov)
    await comp.compact(_big_round_messages(4, big_chars=60_000),
                       context_window=30_000)
    req_text = prov.calls[0][0].text_parts()
    # clip = max(20000, 30000*0.6)=20000 → pruned 骨架后历史被截到 ~20k 内
    assert len(req_text) < 40_000
