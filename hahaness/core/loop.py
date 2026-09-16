"""AgentCore / LoopController——循环引擎（工程详设 §2/§4.2）。

一次 run_turn：
  provider.chat(stream) → chunk 流 → 组装 assistant 消息（整块发射）
  → 无 tool_use 即 turn 终结（纯文本）
  → 有 tool_use：Permission → PreToolUse 钩子（可 veto/改写）→ Dispatcher
    （Supervisor 包裹、超时）→ PostToolUse 钩子 → Truncator → 回填
    tool_result → LoopGuard → Compactor 复查 → 下一轮
错误分类：工具错误回填 is_error=True 让模型自救（特性不是故障）；API 错误
经 retry 后仍失败 → turn error；stop 请求（SIGINT/外部）→ error_stopped
优雅收尾落盘。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from hahaness.constants import LOOP_REPEAT_LIMIT, MAX_TURNS_DEFAULT, TOOL_RESULT_INLINE_MAX
from hahaness.core.hooks import HookRunner
from hahaness.core.permissions import PermissionEngine
from hahaness.providers import Chunk
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.types import Message, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock

# 对外事件发射器：async def emit(event: dict) -> None
#   {"type": "assistant", "message": Message}          每轮 assistant 消息（整块）
#   {"type": "tool_result", "block": ToolResultBlock, "name": str}
#   {"type": "todos", "todos": [dict]}
#   {"type": "turn", "summary": TurnSummary}
Emitter = Callable[[dict], Awaitable[None]]


@dataclass
class TurnSummary:
    subtype: str = "success"    # success|error_max_turns|error_stopped|error_during_execution
    text: str = ""
    usage: dict = field(default_factory=dict)
    model_usage: dict = field(default_factory=dict)
    num_turns: int = 0
    error: str | None = None
    duration_s: float = 0.0
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return self.subtype == "success"


class StopFlag:
    """外部停止请求（SIGINT / 上层 kill 前的优雅收尾窗口）。"""

    def __init__(self) -> None:
        self.requested = False


async def _noop_emit(_ev: dict) -> None:
    pass


async def _fire(emit: Emitter, ev: dict) -> None:
    """emit 兼容同步回调（list.append 等）。"""
    r = emit(ev)
    if hasattr(r, "__await__"):
        await r


# ---------------------------------------------------------------- chunk 组装
class ChunkAssembler:
    """chunk 流 → content blocks + usage + model（provider 无关）。"""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tools_meta: dict[str, str] = {}        # tool_use_id → name
        self.tools_json: dict[str, list[str]] = {}  # tool_use_id → partial_json 片段
        self.usage: dict = {}
        self.model = ""
        self.stop_reason = ""

    def feed(self, c: Chunk) -> None:
        if c.kind == "text_delta":
            self.text_parts.append(c.text)
        elif c.kind == "thinking_delta":
            self.thinking_parts.append(c.text)
        elif c.kind == "input_json_delta":
            if c.tool_name:
                self.tools_meta.setdefault(c.tool_use_id, c.tool_name)
            self.tools_json.setdefault(c.tool_use_id, []).append(c.partial_json)
        elif c.kind == "usage":
            if c.usage:
                self.usage.update(c.usage)
            if c.model:
                self.model = c.model
        elif c.kind == "stop":
            if c.usage:
                self.usage.update(c.usage)   # message_stop 全量覆盖
            self.stop_reason = c.stop_reason or self.stop_reason
            if c.model:
                self.model = c.model

    def blocks(self) -> list:
        out: list = []
        if self.thinking_parts:
            out.append(ThinkingBlock(thinking="".join(self.thinking_parts)))
        for tid, parts in self.tools_json.items():
            raw = "".join(parts)
            try:
                args = json.loads(raw) if raw.strip() else {}
                if not isinstance(args, dict):
                    args = {"value": args}
            except json.JSONDecodeError:
                args = {"_raw": raw[:2000]}   # 模型产出了坏 JSON：回显让其自救
            out.append(ToolUseBlock(id=tid,
                                    name=self.tools_meta.get(tid, "?"), input=args))
        text = "".join(self.text_parts)
        if text:
            out.append(TextBlock(text=text))
        return out


# ---------------------------------------------------------------- LoopGuard
class LoopGuard:
    """同指纹（name+规范化 input）调用连续 LOOP_REPEAT_LIMIT 次且结果相同
    → 注入打断提示。任何一次不同指纹/不同结果即重置。"""

    def __init__(self) -> None:
        self._last_fp: str | None = None
        self._last_result: str | None = None
        self._count = 0
        self.nudges = 0

    def record(self, name: str, args: dict, result: str) -> str | None:
        fp = hashlib.sha1(
            (name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False))
            .encode()).hexdigest()[:16]
        rh = hashlib.sha1(result.encode()).hexdigest()[:16]
        if fp == self._last_fp and rh == self._last_result:
            self._count += 1
        else:
            self._last_fp, self._last_result, self._count = fp, rh, 1
        if self._count >= LOOP_REPEAT_LIMIT:
            self._count = 0
            self.nudges += 1
            return ("检测到你在重复同一操作（同参数同结果已连续 "
                    f"{LOOP_REPEAT_LIMIT} 次）。请改变策略、拆小步骤，"
                    "或直接向用户汇报阻塞原因，不要再重复该调用。")
        return None


# ---------------------------------------------------------------- AgentCore
@dataclass
class LoopSettings:
    max_turns: int | None = MAX_TURNS_DEFAULT   # None/0 = 不限
    permission_mode: str = "bypassPermissions"
    no_compact: bool = False
    context_window: int = 200_000              # token 窗口（compactor 触发基数）


class AgentCore:
    """一个会话的循环引擎实例（子代理=另起一个实例，上下文完全隔离）。"""

    def __init__(self, *, provider, tools: dict[str, Tool], session,
                 cwd: Path, settings: LoopSettings | None = None,
                 permissions: PermissionEngine | None = None,
                 hooks: HookRunner | None = None, compactor=None,
                 ctx: ToolContext | None = None) -> None:
        from hahaness.core.context import ContextAssembler
        self.provider = provider
        self.tools = tools
        self.session = session            # SessionManager（transcript/state 落盘）
        self.cwd = Path(cwd)
        self.settings = settings or LoopSettings()
        self.permissions = permissions or PermissionEngine.load(
            self.cwd, mode=self.settings.permission_mode)
        self.hooks = hooks or HookRunner.load(self.cwd)
        self.compactor = compactor
        self.ctx = ctx or ToolContext(cwd=self.cwd)
        self.ctx.extras.setdefault("state", self.session.state)
        self.assembler = ContextAssembler(self.cwd, tools=list(tools))
        self.loop_guard = LoopGuard()

    # ------------------------------------------------------------ turn 入口
    async def run_turn(self, user_msg: str, *, emit: Emitter | None = None,
                       stop: StopFlag | None = None) -> TurnSummary:
        t0 = time.time()
        summary = TurnSummary()
        emit = emit or _noop_emit

        self.session.append_user(user_msg)
        messages = self.session.messages_for_turn()

        system = self.assembler.build()
        max_turns = self.settings.max_turns or 0
        final_text = ""
        truncation_nudged = False

        try:
            while True:
                if stop is not None and stop.requested:
                    summary.subtype = "error_stopped"
                    summary.stopped = True
                    break
                # ① provider 流式调用（retry 在 provider 内部）
                asm = ChunkAssembler()
                try:
                    async for c in self.provider.chat(messages,
                                                      [t.def_() for t in self.tools.values()],
                                                      system):
                        asm.feed(c)
                        if c.kind == "error":
                            raise ProviderError(c.error, c.retriable)
                except ProviderError as e:
                    summary.subtype = "error_during_execution"
                    summary.error = f"provider error: {e}"
                    break
                blocks = asm.blocks()
                summary.num_turns += 1
                self._merge_usage(summary, asm)

                msg = Message(role="assistant", content=blocks)
                messages.append(msg)
                self.session.append_event("assistant", msg.to_dict())
                await _fire(emit, {"type": "assistant", "message": msg})

                tool_uses = [b for b in blocks if isinstance(b, ToolUseBlock)]
                texts = [b.text for b in blocks if isinstance(b, TextBlock)]
                if texts:
                    final_text = texts[-1]
                if not tool_uses:
                    # 截断空转兜底：stop_reason=max_tokens 且零文本零工具
                    # （长 thinking 吃满输出上限——Terminal-Bench aimo 实测：
                    # 16k 全耗在推理、result 为空串空转一轮）→ 注入一次收敛
                    # 续轮；再截断就按空文本终结
                    if (not texts and asm.stop_reason == "max_tokens"
                            and not truncation_nudged):
                        truncation_nudged = True
                        nudge = ("你的上一条回复被输出长度上限截断，且没有产出任何"
                                 "正文或工具调用。请立即收敛：直接给出最终答案"
                                 "或下一步行动，不要再展开长推理。")
                        messages.append(Message(role="user", content=[
                            TextBlock(text=nudge)]))
                        self.session.append_event("user", {
                            "role": "user",
                            "content": [{"type": "text", "text": nudge}]})
                        continue
                    summary.subtype = "success"   # 纯文本=turn 终结
                    break
                if stop is not None and stop.requested:
                    summary.subtype = "error_stopped"
                    summary.stopped = True
                    break

                # ④ 工具批执行
                result_blocks: list[ToolResultBlock] = []
                for tu in tool_uses:
                    if stop is not None and stop.requested:
                        break
                    blk, name = await self._exec_tool(tu, emit)
                    result_blocks.append(blk)
                    nudge = self.loop_guard.record(name, tu.input,
                                                   _inline(blk.content))
                    if nudge:
                        messages.append(Message(role="user", content=[
                            TextBlock(text=nudge)]))
                        self.session.append_event("user", {
                            "role": "user", "content": [{"type": "text", "text": nudge}]})
                if result_blocks:
                    # transcript 已逐条落 tool_result 事件（replay 自动归并成
                    # user 批消息）；这里只维护内存上下文
                    messages.append(Message(role="user", content=result_blocks))

                # ⑥ 压缩复查（工具批后上下文增长点）
                if self.compactor is not None and not self.settings.no_compact:
                    messages, did = await self.compactor.maybe_compact(
                        messages, summary.usage, self.settings.context_window)
                    if did:
                        self.session.mark_compact(self.compactor.last_summary)

                # 轮次闸
                if max_turns and summary.num_turns >= max_turns:
                    summary.subtype = "error_max_turns"
                    summary.error = f"达到轮次上限 {max_turns}"
                    break
        finally:
            summary.text = final_text
            summary.duration_s = time.time() - t0
            # Stop hook + result 落盘（异常路径也走）
            try:
                await self.hooks.fire("Stop", {"subtype": summary.subtype,
                                               "text": summary.text[:2000]})
            except Exception:  # noqa: BLE001
                pass
            self.session.append_event("result", {
                "subtype": summary.subtype, "result": summary.text,
                "usage": summary.usage, "modelUsage": summary.model_usage,
                "num_turns": summary.num_turns,
                "duration_ms": int(summary.duration_s * 1000)}, fsync=True)
            self.session.record_usage(summary)
            await _fire(emit, {"type": "turn", "summary": summary})
        return summary

    # ------------------------------------------------------------ 工具执行
    async def _exec_tool(self, tu: ToolUseBlock, emit: Emitter) -> tuple[ToolResultBlock, str]:
        name = tu.name
        tool = self.tools.get(name)
        content: str | list = ""
        is_error = False

        # 权限（deny 回填拒绝，模型自救改道）
        decision = self.permissions.check(name, tu.input)
        if not decision.allowed:
            content = f"权限拒绝：{decision.reason}"
            is_error = True
        else:
            # PreToolUse 钩子（exit 2 veto / stdout JSON 改写 input）
            pre = await self.hooks.fire("PreToolUse",
                                        {"tool": name, "input": tu.input})
            if pre.blocked:
                content = f"钩子阻止：{pre.block_reason}"
                is_error = True
            elif tool is None:
                content = f"未知工具：{name}（检查拼写或改用现有工具）"
                is_error = True
            else:
                args = pre.input_override or tu.input
                try:
                    timeout = tool.timeout_s or 300
                    raw = await asyncio.wait_for(tool.execute(args, self.ctx),
                                                 timeout=timeout)
                    post = await self.hooks.fire("PostToolUse",
                                                 {"tool": name, "input": args,
                                                  "output": _inline(raw)})
                    if post.blocked:
                        content = f"PostToolUse 钩子阻止：{post.block_reason}"
                        is_error = True
                    else:
                        content = post.output_override or raw
                except ToolError as e:
                    content, is_error = str(e), True
                except asyncio.TimeoutError:
                    content = f"工具 {name} 执行超时（>{tool.timeout_s or 300}s）"
                    is_error = True
                except Exception as e:  # noqa: BLE001 — 工具异常回填不炸循环
                    content, is_error = f"工具内部异常: {e!r}", True
        if isinstance(content, str):
            content = content[:100_000]   # Truncator 最终防线（工具内已有细粒度纪律）
        blk = ToolResultBlock(tool_use_id=tu.id, content=content, is_error=is_error)
        self.session.append_event("tool_result", blk.to_dict())
        await _fire(emit, {"type": "tool_result", "block": blk, "name": name})
        return blk, name

    # ------------------------------------------------------------ 记账
    @staticmethod
    def _merge_usage(summary: TurnSummary, asm: ChunkAssembler) -> None:
        u = asm.usage or {}
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                  "cache_creation_input_tokens"):
            summary.usage[k] = (summary.usage.get(k) or 0) + (u.get(k) or 0)
        model = asm.model or "unknown"
        mu = summary.model_usage.setdefault(model, {
            "inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.0})
        mu["inputTokens"] += u.get("input_tokens") or 0
        mu["outputTokens"] += u.get("output_tokens") or 0
        mu["cacheReadInputTokens"] += u.get("cache_read_input_tokens") or 0
        mu["cacheCreationInputTokens"] += u.get("cache_creation_input_tokens") or 0


class ProviderError(RuntimeError):
    def __init__(self, msg: str, retriable: bool = False) -> None:
        super().__init__(msg)
        self.retriable = retriable


def _inline(content) -> str:
    if isinstance(content, str):
        return content[:TOOL_RESULT_INLINE_MAX]
    return json.dumps(content, ensure_ascii=False)[:TOOL_RESULT_INLINE_MAX]
