"""Compactor（工程详设 §4.4，分水岭 #1）：上下文压缩。

触发：cache_read+input ≥ COMPACT_THRESHOLD × 窗口（或上层强制）。
保留 = 摘要块（便宜模型自生成）+ 最近 K 轮消息（轮 = assistant + 其随后的
user 消息，保持 tool_use/tool_result 配对完整——Anthropic API 硬约束）。
新 messages = [user: 摘要块 + "请继续"] + 最近 K 轮。压缩后首轮回填 todos
（对抗约束丢失——跑飞的主因）由调用方（loop）在下一轮 system 重建时完成。
"""
from __future__ import annotations

from hahaness.constants import COMPACT_KEEP_TURNS, COMPACT_THRESHOLD
from hahaness.types import Message, TextBlock

SUMMARY_PROMPT = """请把以下会话历史压缩成一份接力摘要，供同一 agent 换新上下文后继续工作。
必须包含（用紧凑中文列表）：
1. 任务目标与已确认的结论（含用户给过的硬约束/偏好）
2. 已改文件清单（路径 + 每处改动意图一句话）
3. 当前任务清单状态（未完成项逐条）
4. 未决问题与下一步计划（具体到可执行）
5. 关键命令输出索引（只记"去哪找"：日志路径/文件名，不抄原文）

会话历史：
{history}
"""


class Compactor:
    def __init__(self, provider, small_model: str | None = None) -> None:
        self.provider = provider
        # small_model 预留：provider.chat 暂无 per-call 模型覆盖，摘要先用同模型
        # （摘要调用很短，成本可接受）
        self.small_model = small_model
        self.last_summary: str = ""

    async def maybe_compact(self, messages: list[Message], usage: dict,
                            context_window: int) -> tuple[list[Message], bool]:
        """超阈值才压；不超原样返回。返回 (新 messages, 是否压缩)。"""
        used = (usage.get("input_tokens") or 0) \
            + (usage.get("cache_read_input_tokens") or 0)
        if not context_window or used < COMPACT_THRESHOLD * context_window:
            return messages, False
        return await self.compact(messages)

    async def compact(self, messages: list[Message]) -> tuple[list[Message], bool]:
        rounds = _split_rounds(messages)
        keep = rounds[-COMPACT_KEEP_TURNS:] if len(rounds) > COMPACT_KEEP_TURNS else rounds
        n_drop = len(rounds) - len(keep)
        if n_drop <= 0:
            return messages, False
        dropped = [m for r in rounds[:-COMPACT_KEEP_TURNS] for m in r]
        summary = await self._summarize(dropped)
        self.last_summary = summary
        head = Message(role="user", content=[TextBlock(
            text=f"【上下文已压缩】之前的会话摘要如下，请以此接力继续，"
                 f"不要重做已完成步骤：\n\n{summary}\n\n请继续当前任务。")])
        return [head, *[m for r in keep for m in r]], True

    async def _summarize(self, dropped: list[Message]) -> str:
        history = "\n".join(
            f"[{m.role}] " + (m.text_parts()[:1500]
                              or "（工具调用/结果，细节见工作区与日志）")
            for m in dropped)
        text = ""
        try:
            req = Message(role="user", content=[TextBlock(
                text=SUMMARY_PROMPT.format(history=history[:120_000]))])
            chunks = self.provider.chat(
                [req], [], "你是会话压缩器，只输出摘要本身，不要任何前后缀。")
            async for c in chunks:
                if c.kind == "text_delta":
                    text += c.text
                elif c.kind == "error":
                    return self._fallback_summary(dropped)
        except Exception:  # noqa: BLE001 — 摘要失败降级硬摘要
            return self._fallback_summary(dropped)
        return text.strip() or self._fallback_summary(dropped)

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
                from hahaness.types import ToolUseBlock as _TUB
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
