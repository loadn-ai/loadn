"""Compactor（工程详设 §4.4，分水岭 #1）：上下文压缩。

触发：最近一次调用的 input 侧 usage（last-call 口径，非累计）≥
COMPACT_THRESHOLD × 窗口（或上层强制）。

四件套（七仓深读 P0-1.3 综合）：
- **先裁剪后总结**（dsh/opencode）：_summarize 拼 history 时对超
  PRUNE_KEEP_CHARS 的旧 tool_result 渲染成骨架（头500+尾300+pruned），
  只做渲染不 mutate Message——配对不变式天然成立；摘要调用成本随之降。
- **交接式模板**（codex）：摘要 = 写给下一个 LLM 的交接文档（五段，
  含文件账本）；已有旧摘要时换 UPDATE 增量模板（pi）——不重写已定案。
- **token 预算切点**（pi）：保留轮从尾部累加至 COMPACT_KEEP_TOKENS
  （chars/1.6 粗估）且至少 2 轮；COMPACT_KEEP_TURNS 退化为上限兜底。
- **buffer + 单次重试**（codex）：摘要请求的 history clip 随窗口缩放
  （int(window*0.6)），防"压缩本身超限"；失败先重试一次（history 减半）
  再降级硬摘要。

保留 = 交接摘要块 + 最近若干轮（轮 = assistant + 其随后的 user 消息，
tool_use/tool_result 配对完整——Anthropic API 硬约束）。新 messages =
[user: 摘要 + 请继续] + 保留轮。
"""
from __future__ import annotations

from loadn.constants import (
    COMPACT_KEEP_TOKENS,
    COMPACT_KEEP_TURNS,
    COMPACT_THRESHOLD,
    PRUNE_KEEP_CHARS,
)
from loadn.types import Message, TextBlock, ToolResultBlock

SUMMARY_PROMPT = """请把以下会话历史整理成一份**交接文档**，供下一个刚接手的 LLM 继续工作。
用紧凑中文分五段（只写有内容的段）：
1. 目标与硬约束：任务目标 + 用户给过的硬约束/偏好（逐条保留原文要点）
2. 进度：Done（已完成，附产物路径）/ In Progress（进行到哪一步）/ Blocked（被什么挡住）
3. 关键决策：已定的重要技术决策与理由（一行一条）
4. 下一步：具体到可执行的待办清单
5. 文件账本：系统已预提取如下（校对补全遗漏，不要删除路径）：
{file_ledger}

会话历史（旧工具输出已裁剪为骨架）：
{history}"""

UPDATE_PROMPT = """你此前写过一份交接摘要（见【旧摘要】）。请基于【新增历史】输出**更新版交接摘要**：
- 保留旧摘要中仍然有效的内容（目标/约束/关键决策不要重写，只修正已过时的）
- 合并进度（Done 增补、In Progress 推进、Blocked 更新或解除）
- 文件账本：系统已预提取新增部分如下，与旧摘要账本合并去重：
{file_ledger}

【旧摘要】
{prev}

【新增历史】（旧工具输出已裁剪为骨架）
{history}"""


class Compactor:
    def __init__(self, provider, small_model: str | None = None) -> None:
        self.provider = provider
        # 摘要优先用小模型（per-call 覆盖）；未配置时同模型（调用很短，可接受）
        self.small_model = small_model
        self.last_summary: str = ""
        # 最近一次压缩裁掉的 token 估算（chars/4——UI 时间线标记用）
        self.last_dropped_tokens: int = 0

    async def maybe_compact(self, messages: list[Message], usage: dict,
                            context_window: int) -> tuple[list[Message], bool]:
        """超阈值才压；不超原样返回。返回 (新 messages, 是否压缩)。

        usage 应传**最近一次调用**的 input 侧 usage（loop 维护的
        last-call 口径）——累计口径会在实际上下文 20-40% 时就误触发。
        """
        used = (usage.get("input_tokens") or 0) \
            + (usage.get("cache_read_input_tokens") or 0)
        if not context_window or used < COMPACT_THRESHOLD * context_window:
            return messages, False
        return await self.compact(messages, context_window=context_window)

    async def compact(self, messages: list[Message], *,
                      context_window: int = 200_000,
                      prev_summary: str = "") -> tuple[list[Message], bool]:
        rounds = _split_rounds(messages)
        keep_idx = _keep_from(rounds)
        dropped = [m for r in rounds[:keep_idx] for m in r]
        if not dropped:
            return messages, False
        # 裁掉量估算（chars/4）：进 compact 事件——时间线「何时裁了多少」
        self.last_dropped_tokens = sum(
            len(getattr(b, "text", "")) for m in dropped
            for b in m.content) // 4
        summary = await self._summarize(dropped, context_window, prev_summary)
        self.last_summary = summary
        head = Message(role="user", content=[TextBlock(
            text=f"【上下文已压缩】之前的会话交接摘要如下，请以此接力继续，"
                 f"不要重做已完成步骤：\n\n{summary}\n\n请继续当前任务。")])
        return [head, *[m for r in rounds[keep_idx:] for m in r]], True

    # ------------------------------------------------------------ 摘要
    async def _summarize(self, dropped: list[Message], context_window: int,
                         prev_summary: str = "") -> str:
        history = _render_history(dropped)
        ledger = _file_ledger(dropped)
        clip = max(20_000, int(context_window * 0.6))
        try:
            return (await self._summarize_once(history, clip,
                                               prev_summary, ledger)).strip()
        except _SummaryFailed:
            pass
        try:   # 单次重试（codex 防循环标志）：history 减半再来一次
            return (await self._summarize_once(history, clip // 2,
                                               prev_summary, ledger)).strip()
        except _SummaryFailed:
            return self._fallback_summary(dropped)

    async def _summarize_once(self, history: str, clip: int,
                              prev_summary: str, ledger: str) -> str:
        """一次摘要调用；失败（error chunk/异常/空文本）抛 _SummaryFailed。"""
        if prev_summary:
            prompt = UPDATE_PROMPT.format(prev=prev_summary[:20_000],
                                          file_ledger=ledger or "（无）",
                                          history=history[:clip])
        else:
            prompt = SUMMARY_PROMPT.format(file_ledger=ledger or "（无）",
                                           history=history[:clip])
        req = Message(role="user", content=[TextBlock(text=prompt)])
        text = ""
        try:
            chunks = self.provider.chat(
                [req], [], "你是会话压缩器，只输出交接摘要本身，不要任何前后缀。",
                model=self.small_model, use_cache=False)
            async for c in chunks:
                if c.kind == "text_delta":
                    text += c.text
                elif c.kind == "error":
                    raise _SummaryFailed(c.error)
        except _SummaryFailed:
            raise
        except Exception as e:  # noqa: BLE001 — 网络等异常同走重试/降级链
            raise _SummaryFailed(repr(e)) from e
        if not text.strip():
            raise _SummaryFailed("empty summary")
        return text

    @staticmethod
    def _fallback_summary(dropped: list[Message]) -> str:
        """便宜模型不可用时的硬压缩：抽全部用户指令 + 文件操作索引。"""
        lines: list[str] = []
        for m in dropped:
            if m.role == "user":
                t = m.text_parts()
                if t:
                    lines.append(f"- 用户指令：{t[:200]}")
            else:
                from loadn.types import ToolUseBlock as _TUB
                for b in m.content:
                    if isinstance(b, _TUB):
                        probe = next((str(v) for k, v in b.input.items()
                                      if k in ("file_path", "command", "path", "url")
                                      and v), "")
                        lines.append(f"- {b.name}: {probe[:120]}")
        return "（自动硬摘要）\n" + "\n".join(lines[-200:])


def _split_rounds(messages: list[Message]) -> list[list[Message]]:
    """切轮：assistant 消息开新轮，其后的 user（tool_result/text）归入该轮。
    开头的孤立 user 消息自成一轮。保证 tool_use/tool_result 永不跨轮拆散。"""
    rounds: list[list[Message]] = []
    cur: list[Message] = []
    for m in messages:
        if m.role == "assistant":
            if cur:
                rounds.append(cur)
            cur = [m]
        else:
            cur = cur or []
            cur.append(m)
    if cur:
        rounds.append(cur)
    return rounds


def _keep_from(rounds: list[list[Message]]) -> int:
    """token 预算切点：从尾部逐轮累加至 COMPACT_KEEP_TOKENS（chars/1.6 估）。

    至少保留 2 轮；COMPACT_KEEP_TURNS 作上限兜底（防预算内塞几百轮）。
    返回保留的起始轮 index（0 = 全保 → 无可丢）。
    """
    if len(rounds) <= 2:
        return 0
    budget = COMPACT_KEEP_TOKENS * 1.6
    acc = 0
    keep = 0
    for i in range(len(rounds) - 1, -1, -1):
        size = sum(len(m.to_dict().__str__()) for m in rounds[i])
        if acc + size > budget and (len(rounds) - i) >= 2:
            break
        acc += size
        keep = i
        if len(rounds) - keep > COMPACT_KEEP_TURNS:
            keep = max(0, len(rounds) - COMPACT_KEEP_TURNS)
            break
    return keep


def _render_history(dropped: list[Message]) -> str:
    """渲染摘要输入：旧 tool_result 超 PRUNE_KEEP_CHARS 裁成骨架（只渲染
    不 mutate）。"""
    parts: list[str] = []
    for m in dropped:
        segs = []
        for b in m.content:
            if isinstance(b, ToolResultBlock) and isinstance(b.content, str):
                if len(b.content) > PRUNE_KEEP_CHARS:
                    segs.append(b.content[:500] + "\n…[pruned]…\n"
                                + b.content[-300:])
                else:
                    segs.append(b.content[:1500])
            elif isinstance(b, TextBlock):
                segs.append(b.text[:1500])
        text = "\n".join(x for x in segs if x)
        parts.append(f"[{m.role}] " + (text or "（工具调用，细节见工作区与日志）"))
    return "\n".join(parts)


def _file_ledger(dropped: list[Message]) -> str:
    """文件账本：dropped 里 tool_use 提取 readFiles/modifiedFiles（去重保序）。"""
    reads: list[str] = []
    writes: list[str] = []
    from loadn.types import ToolUseBlock
    for m in dropped:
        for b in m.content:
            if not isinstance(b, ToolUseBlock):
                continue
            fp = b.input.get("file_path") or b.input.get("notebook_path")
            if not isinstance(fp, str) or not fp:
                continue
            if b.name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                if fp not in writes:
                    writes.append(fp)
            elif b.name == "Read" and fp not in reads:
                reads.append(fp)
    lines = []
    if reads:
        lines.append("readFiles: " + ", ".join(reads[:50]))
    if writes:
        lines.append("modifiedFiles: " + ", ".join(writes[:50]))
    return "\n".join(lines)


class _SummaryFailed(Exception):
    """摘要调用失败（error chunk/异常/空文本）——触发重试或降级。"""
