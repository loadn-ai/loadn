"""M6a：compactor 直测——98% 行覆盖 / 24% 突变杀伤暴露的断言真空。

压缩交接（轮切分/预算切点/骨架渲染/文件账本/硬回退）是 agent 唯一的
「跨压缩记忆」，构建逻辑此前只有 dropped_tokens 估算一个断言——五个
纯函数 + 触发门 + 流式摘要全程无对赌。
"""
from __future__ import annotations

from types import SimpleNamespace

from loadn.constants import PRUNE_KEEP_CHARS
from loadn.core.compactor import (
    Compactor,
    _file_ledger,
    _keep_from,
    _render_history,
    _split_rounds,
)
from loadn.types import Message, TextBlock, ToolResultBlock, ToolUseBlock


def _u(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def _a(*blocks) -> Message:
    return Message(role="assistant", content=list(blocks))


class _Stub:
    """流式 provider 桩：按脚本吐 chunk（含非文本帧），记录 prompt。"""

    def __init__(self, texts: list[str]):
        self.texts = texts
        self.calls: list[str] = []

    def chat(self, messages, _tools, _system, model=None, use_cache=False):
        self.calls.append(messages[0].content[0].text)

        async def gen():
            for t in self.texts:
                yield SimpleNamespace(kind="text_delta", text=t)
            yield SimpleNamespace(kind="message_stop", text="")  # 非文本帧

        return gen()


# ---------------------------------------------------------------- 触发门
async def test_maybe_compact_gates():
    comp = Compactor(_Stub(["x"]), small_model="m")
    msgs = [_u("问题")]
    # usage 缺键（None or 0 回退）+ 阈值下 → 原样返回且**未压缩**
    for usage in ({}, {"input_tokens": 100},
                  {"input_tokens": 100, "cache_read_input_tokens": 200}):
        out, did = await comp.maybe_compact(msgs, usage, context_window=200_000)
        assert out is msgs and did is False
    # 超 92% 窗口 → 触发（三轮、首轮超预算 → 有可丢才真压缩）
    out, did = await comp.maybe_compact(
        [_a(TextBlock(text="旧轮" * 50_000), ToolUseBlock("t1", "Write",
                                                          {"file_path": "/f"})),
         _u("结果"), _a(TextBlock(text="中轮")), _u("果2"),
         _a(TextBlock(text="新轮")), _u("继续")],
        {"input_tokens": 199_000}, context_window=200_000)
    assert did is True and out[0].role == "user"   # 交接摘要块开头


# ---------------------------------------------------------------- 纯函数
def test_split_rounds_semantics():
    """assistant 开新轮；开头孤立 user 自成一轮；配对不跨轮拆散。"""
    m = [_u("问1"), _a(TextBlock(text="答1")), _u("果1"),
         _a(TextBlock(text="答2")), _u("问2")]
    rounds = _split_rounds(m)
    assert [[x.role for x in r] for r in rounds] == [
        ["user"], ["assistant", "user"], ["assistant", "user"]]


def test_keep_from_budget_and_floor():
    """≤2 轮全保；3 轮首轮超预算 → 从第 1 轮起保；小会话全保。"""
    assert _keep_from([[_u("a")], [_u("b")]]) == 0
    small = [[_u("x")], [_u("y")], [_u("z")]]
    assert _keep_from(small) == 0                     # 预算内全保
    big = [[_u("巨" * 60_000)], [_u("近1")], [_u("近2")]]
    assert _keep_from(big) == 1                       # 至少 2 轮保底


def test_render_history_prune_and_placeholder():
    """超 PRUNE_KEEP_CHARS 的 tool_result 骨架化（头500+尾300）；非文本
    块消息渲染占位文案（空文本不得裸奔）。"""
    long_result = ToolResultBlock(
        "t1", content="HEADMARK" + "A" * 2500 + "TAILMARK")
    assert len(long_result.content) > PRUNE_KEEP_CHARS
    out = _render_history([_a(long_result)])
    assert "…[pruned]…" in out
    assert "HEADMARK" in out and "TAILMARK" in out   # 头500/尾300 窗口保留
    assert "A" * 600 not in out                      # 中段裁掉
    short = _render_history([_a(ToolResultBlock("t2", content="ok"))])
    assert "ok" in short and "pruned" not in short
    # 消息只含 tool_use（无 TextBlock/ToolResultBlock）→ 占位文案
    ph = _render_history([_a(ToolUseBlock("t3", "Bash", {"command": "ls"}))])
    assert "（工具调用，细节见工作区与日志）" in ph


def test_file_ledger_sets_and_dedup():
    """read/modify 分账本、去重保序、未知工具不入账。"""
    dropped = [
        _a(ToolUseBlock("1", "Write", {"file_path": "/a"}),
           ToolUseBlock("2", "Edit", {"file_path": "/a"}),      # 同路径去重
           ToolUseBlock("3", "NotebookEdit", {"notebook_path": "/nb.ipynb"})),
        _a(ToolUseBlock("4", "Read", {"file_path": "/b"}),
           ToolUseBlock("5", "Read", {"file_path": "/b"})),      # 读去重
        _a(ToolUseBlock("6", "Grep", {"file_path": "/c"})),      # 未知工具
        _a(ToolUseBlock("7", "Write", {"file_path": 42})),       # 非串 path
    ]
    led = _file_ledger(dropped)
    assert "modifiedFiles: /a, /nb.ipynb" in led
    assert "readFiles: /b" in led
    assert "/c" not in led and "42" not in led
    assert _file_ledger([_u("纯文本")]) == ""


def test_fallback_summary_shape():
    """硬回退：用户指令 + 工具行（command/file_path 探针键）都在。"""
    fb = Compactor._fallback_summary([
        _u("请实现功能 X 并写测试"),
        _a(ToolUseBlock("t1", "Bash", {"command": "pytest -q"}),
           ToolUseBlock("t2", "Write", {"file_path": "/src/x.py"})),
    ])
    assert fb.startswith("（自动硬摘要）")
    assert "- 用户指令：请实现功能 X 并写测试" in fb
    assert "- Bash: pytest -q" in fb
    assert "- Write: /src/x.py" in fb


# ---------------------------------------------------------------- 摘要链
async def test_summarize_stream_and_prompt_ledger():
    """text_delta 流拼接为摘要；prompt 携带真账本（非「（无）」回退）。"""
    stub = _Stub(["交接", "摘要"])
    comp = Compactor(stub, small_model="sm")
    dropped = [_a(ToolUseBlock("t1", "Write", {"file_path": "/f.py"}))]
    out = await comp._summarize(dropped, 200_000)
    assert out == "交接摘要"
    assert "modifiedFiles: /f.py" in stub.calls[0]
    assert "（无）" not in stub.calls[0]
    # 空账本才用回退文案
    stub2 = _Stub(["s"])
    await Compactor(stub2, small_model="sm")._summarize([_u("问")], 200_000)
    assert "（无）" in stub2.calls[0]
    # UPDATE 模板：旧摘要 + 增量真账本都进 prompt（不回退「（无）」）
    stub3 = _Stub(["更新"])
    await Compactor(stub3, small_model="sm")._summarize(
        [_a(ToolUseBlock("t9", "Edit", {"file_path": "/g.py"}))], 200_000,
        prev_summary="旧交接")
    assert "旧交接" in stub3.calls[0]
    assert "modifiedFiles: /g.py" in stub3.calls[0]
    assert "（无）" not in stub3.calls[0]


async def test_compact_end_to_end_and_all_keep():
    """全保路径（无可丢）原样返回 False；真压缩头块带交接模板。"""
    comp = Compactor(_Stub(["摘要"]), small_model="m")
    two = [_a(TextBlock(text="答")), _u("果")]
    out, did = await comp.compact(two, context_window=200_000)
    assert did is False and out is two
    big = [_a(TextBlock(text="旧" * 60_000), ToolUseBlock("t", "Bash",
                                                          {"command": "ls"})),
           _u("r"), _a(TextBlock(text="中")), _u("m"),
           _a(TextBlock(text="新")), _u("go")]
    out, did = await comp.compact(big, context_window=200_000)
    assert did is True
    head = out[0].content[0].text
    assert "【上下文已压缩】" in head and "摘要" in head
    assert comp.last_summary == "摘要"
    # 裁掉量估算 = chars//4（UI 时间线口径）
    assert comp.last_dropped_tokens == sum(
        len(getattr(b, "text", "")) for m in big[:2]
        for b in m.content) // 4
