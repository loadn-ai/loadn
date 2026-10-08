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
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from loadn.constants import (
    GRIND_MAX_NUDGES,
    GRIND_MIN_TURNS,
    LOOP_NAME_FAIL_LIMIT,
    LOOP_REMIND_AT,
    LOOP_REPEAT_LIMIT,
    MAX_TURNS_DEFAULT,
    REFLECT_EVERY_TURNS,
    STREAM_RETRY_MAX,
    TOOL_RESULT_INLINE_MAX,
)
from loadn.core.hooks import HookRunner
from loadn.core.permissions import PermissionEngine
from loadn.core.streamsynth import StreamEventSynthesizer
from loadn.providers import Chunk
from loadn.providers.retry import StreamInterrupted, backoff_delay, classify_error
from loadn.tools.base import Tool, ToolContext, ToolError
from loadn.types import Message, TextBlock, ThinkingBlock, ToolResultBlock, ToolUseBlock
from loadn.util import get_logger

log = get_logger(__name__)

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
    """chunk 流 → content blocks + usage + model（provider 无关）。

    blocks() 按块首片到达序输出（thinking/text/tool_use 交错时与真实流
    一致，stream_event 增量流与最终 assistant 消息块序对齐）。
    """

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.signature_parts: list[str] = []        # thinking 延续性签名（anthropic 透传）
        self.tools_meta: dict[str, str] = {}        # tool_use_id → name
        self.tools_json: dict[str, list[str]] = {}  # tool_use_id → partial_json 片段
        self.usage: dict = {}
        self.model = ""
        self.stop_reason = ""
        self.message_id = ""
        self.bad_tool_raws: dict[str, str] = {}     # JSON 装配失败的 id → raw
        self._order: list[str] = []                 # 块首见序："thinking"/"text"/"tool:<id>"

    def _reg(self, key: str) -> None:
        if key not in self._order:
            self._order.append(key)

    def feed(self, c: Chunk) -> None:
        if c.kind == "text_delta":
            self.text_parts.append(c.text)
            self._reg("text")
        elif c.kind == "thinking_delta":
            self.thinking_parts.append(c.text)
            if c.signature:     # 非流式通道：签名搭 thinking_delta 便车下发
                self.signature_parts.append(c.signature)
            self._reg("thinking")
        elif c.kind == "signature_delta":
            self.signature_parts.append(c.signature)
        elif c.kind == "input_json_delta":
            if c.tool_name:
                self.tools_meta.setdefault(c.tool_use_id, c.tool_name)
            self.tools_json.setdefault(c.tool_use_id, []).append(c.partial_json)
            self._reg(f"tool:{c.tool_use_id}")
        elif c.kind in ("usage", "stop"):
            if c.usage:
                self.usage.update(c.usage)   # message_stop 全量覆盖
            if c.model:
                self.model = c.model
            if c.message_id:
                self.message_id = c.message_id
            if c.kind == "stop":
                self.stop_reason = c.stop_reason or self.stop_reason

    def blocks(self) -> list:
        out: list = []
        text = "".join(self.text_parts)
        thinking = "".join(self.thinking_parts)
        for key in self._order:
            if key == "thinking":
                if thinking:
                    out.append(ThinkingBlock(
                        thinking=thinking,
                        signature="".join(self.signature_parts)))
            elif key == "text":
                if text:
                    out.append(TextBlock(text=text))
            elif key.startswith("tool:"):
                tid = key[len("tool:"):]
                raw = "".join(self.tools_json.get(tid, []))
                try:
                    args = json.loads(raw) if raw.strip() else {}
                    if not isinstance(args, dict):
                        args = {"value": args}
                except json.JSONDecodeError:
                    args = {"_raw": raw[:2000]}   # 模型产出了坏 JSON：回显让其自救
                    self.bad_tool_raws[tid] = raw  # 截断场景下 loop 拒绝执行
                out.append(ToolUseBlock(id=tid,
                                        name=self.tools_meta.get(tid, "?"),
                                        input=args))
        return out


# ---------------------------------------------------------------- LoopGuard
class LoopGuard:
    """防循环双维度：①同指纹（name+规范化 input）两段式（轻提醒→硬打断）；
    ②同名连败（参数搅动免疫，2026-10-09 实证补）。指纹段任何一次不同
    指纹/不同结果即重置；连败段按工具名累计失败，该工具成功一次才清零。"""

    def __init__(self) -> None:
        self._last_fp: str | None = None
        self._last_result: str | None = None
        self._count = 0
        self._fail_streak: dict[str, int] = {}
        self.nudges = 0

    def record(self, name: str, args: dict, result: str, *,
               is_error: bool = False) -> tuple[str, str] | None:
        """返回 (level, text)：level ∈ remind|break；None = 无事。

        两段式（dsh repeat-tool-reminder）：连续 LOOP_REMIND_AT 次同结果先
        轻提醒（省 token 的第一道闸），LOOP_REPEAT_LIMIT 次才硬打断。

        同名连败段：同名工具无论参数怎么换，连续失败 LOOP_NAME_FAIL_LIMIT
        次 → 硬打断（生产实证：browser_click 逐像素递增 50 连败——指纹每轮
        都变使指纹段全程重置；死工具 not_installed/权限拒绝与搅动循环都在
        此形态下）。交错免疫：其他工具的成败不影响本名计数。
        """
        if is_error:
            n = self._fail_streak.get(name, 0) + 1
            self._fail_streak[name] = n
            if n >= LOOP_NAME_FAIL_LIMIT:
                self._fail_streak[name] = 0
                self.nudges += 1
                return ("break",
                        f"工具 {name} 已连续 {n} 次调用失败（参数怎么换都失败）。"
                        f"最近错误：{result[:160]}。若错误是环境缺失"
                        "（not_installed）或权限拒绝，说明该工具在本环境"
                        "不可用——继续换参数重试无意义：换正交路线，或直接"
                        "向用户汇报阻塞原因。")
        else:
            self._fail_streak.pop(name, None)
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
            return ("break",
                    "检测到你在重复同一操作（同参数同结果已连续 "
                    f"{LOOP_REPEAT_LIMIT} 次）。请改变策略、拆小步骤，"
                    "或直接向用户汇报阻塞原因，不要再重复该调用。")
        if self._count >= LOOP_REMIND_AT:
            return ("remind",
                    f"同一调用已连续 {self._count} 次同结果无进展；"
                    "确认没有收益就换策略，不要继续原样重复。")
        return None


# ---------------------------------------------------------------- AgentCore
@dataclass
class LoopSettings:
    max_turns: int | None = MAX_TURNS_DEFAULT   # None/0 = 不限
    permission_mode: str = "bypassPermissions"
    no_compact: bool = False
    no_plan: bool = False       # True=禁用并行拆分调度（直跑）
    context_window: int = 200_000              # token 窗口（compactor 触发基数）
    # v0.7 死磕模式（Terminal-Bench 实测「4 分钟投降」对症：纯文本收工前过
    # 完工自检关卡——产物核对+预算告知，未过则注入续战提示继续磨）
    grind: bool = False
    budget_s: float | None = None              # 总预算（用于「剩余时间」告知）
    reflect_every: int = REFLECT_EVERY_TURNS   # 反思检查点间隔；0=关


class AgentCore:
    """一个会话的循环引擎实例（子代理=另起一个实例，上下文完全隔离）。"""

    def __init__(self, *, provider, tools: dict[str, Tool], session,
                 cwd: Path, settings: LoopSettings | None = None,
                 permissions: PermissionEngine | None = None,
                 hooks: HookRunner | None = None, compactor=None,
                 ctx: ToolContext | None = None, subagents=None,
                 planner=None, mcp_deferred: dict | None = None) -> None:
        from loadn.core.context import ContextAssembler
        self.provider = provider
        self.tools = tools
        self.mcp_deferred = mcp_deferred or {}   # P2 懒加载索引（名→MCPTool；
        #   会话内 schema 缓存，物化经 ToolSearchTool 原地插入 self.tools）
        self.session = session            # SessionManager（transcript/state 落盘）
        self.cwd = Path(cwd)
        self.settings = settings or LoopSettings()
        self.permissions = permissions or PermissionEngine.load(
            self.cwd, mode=self.settings.permission_mode)
        self.hooks = hooks or HookRunner.load(self.cwd)
        self.compactor = compactor
        self.subagents = subagents        # SubagentManager（并行扇出入口）
        self.planner = planner            # TaskPlanner（v0.2 并行拆分调度）
        self.ctx = ctx or ToolContext(cwd=self.cwd)
        self.ctx.extras.setdefault("state", self.session.state)
        # P2-4：Runtime Task 注册表（bg/子代理/定时器统一观测与取消）
        from loadn.core.tasks import TaskRegistry
        self.task_registry = TaskRegistry()
        self.ctx.extras.setdefault("task_registry", self.task_registry)
        if self.ctx.supervisor is not None:      # bg 命令统一登记
            self.ctx.supervisor.task_registry = self.task_registry
        if self.subagents is not None:           # 并行子代理统一登记
            self.subagents.task_registry = self.task_registry
        self.assembler = ContextAssembler(
            self.cwd, tools=list(tools),
            model=getattr(self.provider, "model_name", None))  # P1-7 变体
        self.loop_guard = LoopGuard()
        self._grind_nudges = 0        # 完工自检已续战次数（GRIND_MAX_NUDGES 封顶）
        self._tool_execs = 0          # 累计工具执行数（口头交付检测）
        # P1-8 repomap：冷启动前 3 轮带仓库地图，之后让位上下文预算
        self._repomap_turns = 0
        # 二轮修#3：turn 收尾 fire-and-forget 任务登记（不存引用会被 GC，
        # 更要命的是 -p 模式 asyncio.run 收尾直接取消它们——平台 webui 主
        # 路径上记忆抽取/P12 检测从未写完过。main._shutdown_bundle 排空）
        self._bg_tasks: set = set()
        self._emit_hook = None             # P2-3 v2：工具区事件源（run_turn 注入）
        # P1-6 cache-warm：空闲期保温（长命进程语义；详见 core/cache_warmer）
        self._warmer = None
        self._warmer_task = None
        self._last_replay = None      # 上一轮精确请求的闭包（字节一致重放）
        self._last_input_tokens = 0
        self._gate_tool_execs = -1    # 上次关卡评估时的工具数（-1=尚未关卡过）
        # 随时插话（steering）：宿主把用户插话追加到 LOADN_STEER_FILE，
        # 主循环每轮 LLM 调用前轮询——运行中消息不等排队、下一轮即生效。
        # 边界取「构造时刻的文件大小」：启动前已有的是历史插话（已在当时
        # 上下文消化过，不重放）；启动时文件尚不存在则从 0 起——之后写入
        # 的全是本 turn 插话。不能等首次成功 open 再定界：文件往往是宿主
        # 收到第一条插话才创建的，那样启动到创建之间写入的会被当历史跳过
        self._steer_path = (os.environ.get("LOADN_STEER_FILE")
                            or os.environ.get("HAHANESS_STEER_FILE", ""))
        try:
            self._steer_offset = os.path.getsize(self._steer_path)
        except OSError:
            self._steer_offset = 0

    # ------------------------------------------------------------ 插话轮询
    def _poll_steer(self) -> list[str]:
        """读上次轮询后新增的插话（.steer.jsonl 每行 {ts,text}）。

        二进制读（字节 offset 精确）；尾部半行（宿主并发 append 中）不越
        过，下次连剩余半段一起读；文件不存在/读失败静默跳过（插话是增强
        不是依赖）。
        """
        if not self._steer_path:
            return []
        try:
            with open(self._steer_path, "rb") as f:
                f.seek(self._steer_offset)
                new = f.read()
        except OSError:
            return []
        if not new or not new.endswith(b"\n"):
            return []
        self._steer_offset += len(new)
        out = []
        for ln in new.decode("utf-8", errors="replace").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                data = json.loads(ln)
                if isinstance(data, dict) and data.get("text"):
                    out.append(str(data["text"]))
            except json.JSONDecodeError:
                continue
        return out

    async def _inject_steer(self, texts: list[str], messages: list,
                            emit: Emitter | None = None) -> int:
        """插话注入上下文 + transcript，并向宿主发 steer 事件（宿主据此
        知道该条插话已被消费——turn 抢先结束时未消费的插话由宿主回队列）。"""
        n = 0
        for t in texts:
            t = t[:4000]
            injected = (f"【用户插话（随时指令，优先级高于原任务）】{t}\n"
                        "请判断与本任务的关系：要求放弃/调整方向的，立即执行转向"
                        "（停止相关子工作，改做用户要的）；要求补充信息的，并入"
                        "当前工作；与本任务无关的，记下不打断。")
            messages.append(Message(role="user", content=[TextBlock(text=injected)]))
            self.session.append_event("user", {
                "role": "user", "engine": True, "content": [{"type": "text", "text": injected}]})
            if emit is not None:
                await _fire(emit, {"type": "steer", "text": t})
            n += 1
        return n

    # ------------------------------------------------------------ turn 入口
    async def run_turn(self, user_msg: str, *, emit: Emitter | None = None,
                       stop: StopFlag | None = None,
                       stream_events: bool = False) -> TurnSummary:
        t0 = time.time()
        self._emit_hook = emit              # P2-3：工具区事件源（v2）
        first_user_text = user_msg         # P1-7：标题语料（首 turn）
        self._warmer_bump()               # P1-6：新 turn 即失效在途保温
        summary = TurnSummary()
        emit = emit or _noop_emit

        self.session.append_user(user_msg)
        messages = self.session.messages_for_turn()
        # P3-9：上轮编辑的 auto-lint 失败回喂（system-reminder 语义——
        # 机制化「改完要检查」：失败摘要在下一 turn 开头可见）
        from . import autolint as _al
        pending = _al.pending_reminders(self.ctx)
        if pending:
            joined = "\n".join(
                f"[{p_['path']}]\n{p_['output']}" for p_ in pending)
            injected_reminder = (
                "<system-reminder>上轮编辑后的检查发现问题（自动 lint/test）：\n"
                + joined + "\n请修复上述问题后继续。</system-reminder>")
            messages = [Message(role="user", content=[TextBlock(
                text=injected_reminder)]), *messages]
            self.session.append_event("user", {
                "role": "user", "engine": True,
                "content": [{"type": "text", "text": injected_reminder}]})

        # ---- v0.2 并行拆分调度：可拆任务先扇出子代理，结果注入主循环收敛
        if (self.planner is not None and not self.settings.no_plan
                and self.subagents is not None):
            plan = await self.planner.plan(user_msg)
            # 判定可观测：无论拆不拆都落 transcript + plan 事件（debug 依据）
            plan_rec = {"parallelizable": plan.parallelizable,
                        "reason": plan.reason,
                        "subtasks": [st.prompt[:80] for st in plan.subtasks]}
            self.session.append_event("system", {"subtype": "plan", **plan_rec})
            await _fire(emit, {"type": "plan", "plan": plan_rec})
            if plan.parallelizable and len(plan.subtasks) >= 2:
                results = await self.subagents.gather(plan.subtasks, emit=emit)
                if stop is not None and stop.requested:
                    summary.subtype = "error_stopped"
                    summary.stopped = True
                    return summary
                combined = "\n\n".join(
                    f"### 并行子任务 {i + 1}/{len(plan.subtasks)}\n"
                    f"任务：{st.prompt[:300]}\n结果：\n{r}"
                    for i, (st, r) in enumerate(zip(plan.subtasks, results, strict=False)))
                converge = (
                    f"【并行子任务已全部完成】原任务：{user_msg[:2000]}\n\n"
                    f"{combined}\n\n请整合以上子任务结果，验证其一致性，补做必要"
                    "的收尾与修正（子代理可能有个别错误），然后给出最终交付。"
                    "若子结果之间有冲突，以可验证的证据为准。")
                self.session.append_event("user", {
                    "role": "user", "engine": True, "content": [{"type": "text", "text": converge}]})
                messages.append(Message(role="user", content=[
                    TextBlock(text=converge)]))

        if self._repomap_due():
            system = self._build_system_with_map()
        else:
            self.assembler.with_repomap = False
            system = self.assembler.build()
        # P7：本轮实际注入的记忆清单（assembler 真源：节关/被裁=空）——
        # 挂到本 turn 的 assistant 消息扩展字段 memory_hits
        self._turn_memory_hits = list(getattr(self.assembler,
                                              "last_memory_hits", []))
        max_turns = self.settings.max_turns or 0
        final_text = ""
        truncation_nudged = False
        # 压缩触发的用量口径：最近一次调用的 input 侧（非累计——累计会在
        # 实际上下文 20-40% 时误触发过度压缩）
        last_input_usage: dict = {}
        compress_used = False   # context_overflow 压缩自救（每 turn 至多一次）

        try:
            while True:
                if stop is not None and stop.requested:
                    summary.subtype = "error_stopped"
                    summary.stopped = True
                    break
                # ⓪ 插话轮询：上一轮工具执行期间用户可能发了新消息——
                # 下一轮 LLM 调用前注入，运行中转向不等排队
                steer_texts = self._poll_steer()
                if steer_texts:
                    await self._inject_steer(steer_texts, messages, emit=emit)
                # ① provider 流式调用：首 chunk 前失败由 provider 内部重试；
                #    流中断（StreamInterrupted）与可重试 error chunk 在本层
                #    重试——半成品 assistant 从未 append 进 messages，整轮
                #    重放语义安全（已外发的 stream_event 增量以最终 assistant
                #    整块事件为准，见 streamsynth）
                asm = ChunkAssembler()
                synth = (StreamEventSynthesizer(
                    model=getattr(self.provider, "model_name", "") or "")
                    if stream_events else None)
                stream_done = False
                stream_err = ""
                attempt = 0
                overflowed = False
                while True:
                    try:
                        _tools_def = [t.def_() for t in self.tools.values()]
                        _msgs_snap = list(messages)      # P1-6 重放快照
                        _sys_snap = system
                        async for c in self.provider.chat(
                                messages, _tools_def, system):
                            asm.feed(c)
                            if synth is not None:
                                for ev in synth.feed(c):
                                    await _fire(emit, {"type": "stream_event",
                                                       "event": ev})
                            if c.kind == "error":
                                raise ProviderError(c.error, c.retriable)
                        stream_done = True
                        break
                    except ProviderError as e:
                        if e.reason == "context_overflow" \
                                and self.compactor is not None \
                                and not self.settings.no_compact \
                                and not compress_used:
                            # 溢出自救（hermes FailoverReason）：强制压缩一次
                            # 再重发；不计 STREAM_RETRY attempt（一次压缩
                            # 不该挤掉两次流重试额度）
                            compress_used = True
                            try:
                                self._warmer_bump()    # 压缩改写 messages：旧断点失效
                                messages, did = await self.compactor.compact(
                                    messages,
                                    context_window=self.settings.context_window,
                                    prev_summary=self.session.last_compact_summary())
                            except Exception as ce:  # noqa: BLE001
                                summary.subtype = "error_during_execution"
                                summary.error = f"provider error: {e}｜压缩自救失败: {ce!r}"
                                break
                            if did:
                                self.session.mark_compact(
                                    self.compactor.last_summary,
                                    tokens_cropped=getattr(
                                        self.compactor,
                                        "last_dropped_tokens", None))
                                await self._reflect_if_due(
                                    self.compactor.last_summary)
                                overflowed = True
                                break        # 出内层，回 while True 重发
                            summary.subtype = "error_during_execution"
                            summary.error = f"provider error: {e}（无可压缩轮）"
                            break
                        if not e.retriable:
                            summary.subtype = "error_during_execution"
                            summary.error = f"provider error: {e}"
                            break
                        stream_err = f"provider error: {e}"
                    except StreamInterrupted as e:
                        stream_err = f"stream interrupted: {e}"
                    if stop is not None and stop.requested:
                        summary.subtype = "error_stopped"
                        summary.stopped = True
                        break
                    if attempt >= STREAM_RETRY_MAX:
                        summary.subtype = "error_during_execution"
                        summary.error = f"{stream_err}（重试 {STREAM_RETRY_MAX} 次后仍失败）"
                        break
                    await asyncio.sleep(backoff_delay(attempt + 1))
                    attempt += 1
                    asm = ChunkAssembler()   # 丢弃半成品，整轮重装
                    if synth is not None:
                        synth.reset()         # 新 message id 重发增量
                if not stream_done:
                    if overflowed:
                        continue     # 溢出已压缩：回 while True 用新 messages 重发
                    break
                blocks = asm.blocks()
                summary.num_turns += 1
                self._merge_usage(summary, asm)
                last_input_usage = {k: (asm.usage or {}).get(k) or 0
                                    for k in ("input_tokens",
                                              "cache_read_input_tokens",
                                              "cache_creation_input_tokens")}
                # P1-6：捕获本轮精确请求（messages 快照在 chat 前），供空闲重放
                self._last_replay = (
                    lambda m=list(_msgs_snap), tl=list(_tools_def), sy=_sys_snap:
                    self.provider.chat(m, tl, sy, max_output_override=1))
                self._last_input_tokens = (
                    last_input_usage.get("input_tokens", 0)
                    + last_input_usage.get("cache_read_input_tokens", 0)
                    + last_input_usage.get("cache_creation_input_tokens", 0))

                # P1-2 tool-call-repair：无结构化 tool_use 且正文是纯调用块
                # 序列 → promote 为真调用（走与真实调用完全相同的权限+钩子
                # 执行路径，不得绕过——卡面红线）
                if blocks and not any(isinstance(b, ToolUseBlock) for b in blocks):
                    blocks = self._repair_tool_calls(blocks)
                msg = Message(role="assistant", content=blocks)
                messages.append(msg)
                payload = msg.to_dict()
                hits = getattr(self, "_turn_memory_hits", None)
                if hits:
                    payload["memory_hits"] = hits   # P7：本 turn 注入清单
                self.session.append_event("assistant", payload)
                await _fire(emit, {"type": "assistant", "message": msg,
                                   "message_id": asm.message_id,
                                   **({"memory_hits": hits} if hits else {})})

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
                            "role": "user", "engine": True,
                            "content": [{"type": "text", "text": nudge}]})
                        continue
                    # v0.7 完工自检关卡（grind 模式）：模型说完成 ≠ 任务完成。
                    # Terminal-Bench 实测病灶：cad 4 分钟/cargo 13 分钟对 8 小时
                    # 题投降——把「停不停」从模型手里拿到引擎手里。
                    gate = self._completion_gate(user_msg, texts, summary)
                    if gate is not None:
                        messages.append(Message(role="user", content=[
                            TextBlock(text=gate)]))
                        self.session.append_event("user", {
                            "role": "user", "engine": True,
                            "content": [{"type": "text", "text": gate}]})
                        continue
                    summary.subtype = "success"   # 纯文本=turn 终结（自检放行）
                    break
                if stop is not None and stop.requested:
                    summary.subtype = "error_stopped"
                    summary.stopped = True
                    break

                # ④ 工具批执行（残缺参数拒绝先行——pi 纪律：截断产出的
                # 半截 toolCall 绝不带病执行，直接回填错误让模型重发）
                result_blocks: list[ToolResultBlock] = []
                for tu in tool_uses:
                    if stop is not None and stop.requested:
                        break
                    raw = asm.bad_tool_raws.get(tu.id)
                    if raw is not None and asm.stop_reason == "max_tokens":
                        blk = ToolResultBlock(
                            tool_use_id=tu.id, is_error=True,
                            content=f"该工具参数因输出截断不完整（原文前 200 "
                                    f"字符：{raw[:200]}），未执行；请重新发起"
                                    "完整调用（必要时精简参数避免再截断）。")
                        self.session.append_event("tool_result", blk.to_dict())
                        await _fire(emit, {"type": "tool_result", "block": blk,
                                           "name": tu.name})
                        result_blocks.append(blk)
                        continue
                    blk, name = await self._exec_tool(tu, emit)
                    result_blocks.append(blk)
                    nudge = self.loop_guard.record(
                        name, tu.input, _inline(blk.content),
                        is_error=bool(blk.is_error))
                    if nudge is not None:
                        level, nudge_text = nudge
                        if level == "break":
                            # v0.7 策略轮换：硬打断时给正交换向清单，而不是
                            # 只说「别重复」——模型需要被告知可以往哪换
                            nudge_text += (
                                "\n换思路清单（选一条与已试路线正交的）："
                                "①换一类工具/数学方法 ②自顶向下↔自底向上 "
                                "③先写朴素解锚定再优化 ④拆成可单独验证的小块 "
                                "⑤检索本地文档/示例找现成轮子。"
                                "先一句话说清已试方法为何失败，再动手。")
                        messages.append(Message(role="user", content=[
                            TextBlock(text=nudge_text)]))
                        self.session.append_event("user", {
                            "role": "user", "engine": True,
                            "content": [{"type": "text", "text": nudge_text}]})
                if result_blocks:
                    # transcript 已逐条落 tool_result 事件（replay 自动归并成
                    # user 批消息）；这里只维护内存上下文
                    messages.append(Message(role="user", content=result_blocks))

                # ⑥ 压缩复查（工具批后上下文增长点；last-call 口径防误触发）
                if self.compactor is not None and not self.settings.no_compact:
                    self._warmer_bump()        # 压缩改写 messages：旧断点失效
                    messages, did = await self.compactor.maybe_compact(
                        messages, last_input_usage,
                        self.settings.context_window)
                    if did:
                        self.session.mark_compact(
                            self.compactor.last_summary,
                            tokens_cropped=getattr(
                                self.compactor, "last_dropped_tokens", None))
                        # 二轮修#6：常规压缩点也反思（原只挂 overflow 自救
                        # 路径——正常压缩的摘要从不进反思=特性半残）
                        await self._reflect_if_due(
                            self.compactor.last_summary)

                # v0.7 反思检查点：长磨不迷路（coq 6h 那场靠运气做到的事
                # 变成机制）——周期性强制总结已确立/已废/下一步
                if (self.settings.reflect_every
                        and summary.num_turns % self.settings.reflect_every == 0):
                    reflect = (
                        f"【反思检查点·第 {summary.num_turns} 轮】用三行总结："
                        "①已确立的事实/已产出的成果 ②已证明走不通的路线"
                        "（别再走）③下一步最小可行动作。然后继续执行③。")
                    messages.append(Message(role="user", content=[
                        TextBlock(text=reflect)]))
                    self.session.append_event("user", {
                        "role": "user", "engine": True,
                        "content": [{"type": "text", "text": reflect}]})

                # 轮次闸
                if max_turns and summary.num_turns >= max_turns:
                    summary.subtype = "error_max_turns"
                    summary.error = f"达到轮次上限 {max_turns}"
                    # grace call（hermes）：无工具收尾一次，让模型基于已有
                    # 信息写结论——自包异常：外层 catch-all 会改写 subtype
                    if stop is None or not stop.requested:
                        try:
                            grace = await self._grace_call(
                                messages, system, emit, summary,
                                stream_events=stream_events, stop=stop)
                            if grace:
                                final_text = grace
                        except Exception:  # noqa: BLE001 — grace 失败沿用旧 text
                            log.warning("grace call 失败（沿用已有 final text）")
                    break
        except Exception as e:  # noqa: BLE001 — 意外异常转 turn error 落盘（不落假 success）
            log.exception("run_turn 意外异常")
            summary.subtype = "error_during_execution"
            summary.error = f"internal error: {e!r}"
        finally:
            summary.text = final_text
            summary.duration_s = time.time() - t0
            # Stop hook + result 落盘（异常路径也走）
            try:
                await self.hooks.fire("Stop", {"subtype": summary.subtype,
                                               "text": summary.text[:2000]})
            except Exception:  # noqa: BLE001
                pass
            # P3-1：本 turn 的 diff 摘要（可选字段——v1 消费方忽略未知键）
            diffs = self.ctx.extras.pop("turn_diff", []) or []
            from loadn.core.turn_diff import summary_line
            self.session.append_event("result", {
                "subtype": summary.subtype, "result": summary.text,
                "usage": summary.usage, "modelUsage": summary.model_usage,
                "num_turns": summary.num_turns,
                "duration_ms": int(summary.duration_s * 1000),
                **({"diffs": [{"path": d["path"], "hash": d["hash"],
                               "lines": summary_line(d["diff"])}
                              for d in diffs]} if diffs else {}),
                # P2：MCP 懒加载规模（可选字段——实际节省已自动计入
                # usage.input_tokens；此处仅记延迟面体量便于观测）
                **({"mcp_deferred": {
                        "tools": len(md),
                        "est_tokens_deferred": int(sum(
                            len(t.description)
                            + len(json.dumps(t.input_schema))
                            for t in md.values()) / 4)}}
                   if (md := self.mcp_deferred) else {})},
                fsync=True)
            self.session.record_usage(summary)
            # P3-10：auto-commit 影子分支（off 默认零副作用）
            from . import autocommit as ac
            ac.commit_turn(self.cwd, self.session.session_id,
                           summary.num_turns)
            await _fire(emit, {"type": "turn", "summary": summary})
            self._warmer_schedule()          # P1-6：空闲保温（长命进程）
            self._memory_extract()          # P1-4b：后台记忆抽取（同步快路径）
            self._consolidate_check()       # P12：纠错→技能建议（确定性词表）
            self._titlegen(first_user_text)  # P1-7：引擎侧标题（一次性）
        return summary

    # ------------------------------------------------------------ 完工自检
    def _completion_gate(self, user_msg: str, texts: list[str],
                         summary: TurnSummary) -> str | None:
        """grind 模式完工关卡：纯文本收工前的最后一道闸。

        返回 None=放行结束；返回字符串=注入续战提示继续磨。
        两条腿：①从指令/末段文本里提取产物路径核对存在性；②预算告知
        （模型常不知道任务有 8 小时预算，倾向于「差不多就交」）。
        GRIND_MAX_NUDGES 封顶防不可能题无限空转。
        """
        if not self.settings.grind:
            return None
        if self._grind_nudges >= GRIND_MAX_NUDGES:
            return None
        if summary.num_turns < GRIND_MIN_TURNS:
            return None
        self._grind_nudges += 1
        elapsed = int(summary.duration_s // 60)
        if self.settings.budget_s:
            remain = int((self.settings.budget_s - summary.duration_s) // 60)
            budget_line = (f"任务总预算 {int(self.settings.budget_s // 60)} 分钟，"
                           f"已用 {elapsed} 分钟，剩余约 {remain} 分钟")
        else:
            budget_line = f"本任务预算以小时计，你才用了 {elapsed} 分钟"
        # 产物路径核对：绝对路径 + 相对路径/文件名（相对 cwd 检查）。
        # v0.7.1：只认绝对路径漏掉了「修改 src/foo.v」式交付（coq 15 分钟
        # 假交付的病灶）。
        import re as _re
        exts = ("v|py|txt|json|step|npz|cpp|cc|rs|js|ts|toml|yaml|yml|xml|"
                "csv|md|sh|go|java|hs|r|nb|stp|glb|stl")
        abs_pat = _re.compile(rf"[\/][\w./\-]+\.(?:{exts})")
        rel_pat = _re.compile(rf"(?<![\w./\-])[\w][\w./\-]*\.(?:{exts})")
        corpus = user_msg + "\n" + (texts[-1] if texts else "")
        cands = set(abs_pat.findall(corpus))
        for m in rel_pat.findall(corpus):
            cands.add(m)
        missing = []
        for p in sorted(cands)[:12]:
            if Path(p).exists():
                continue
            if (self.cwd / p).exists():
                continue
            if (self.cwd / p.lstrip("/")).exists():
                continue   # /app/x.py → cwd/app/x.py（容器内绝对路径映射）
            # 指令里提到的才算交付物；模型自述里的在 cands 里混入也无妨，
            # 不存在同样说明没产出。
            missing.append(p)
        tag = (f"（完工自检第 {self._grind_nudges}/{GRIND_MAX_NUDGES} 次，"
               f"之后将接受你的结论）")
        # 口头交付硬拦截：上次关卡评估以来零工具执行 = 只是改口重申完成，
        # 没有任何新证据（实测病灶：模型收到续战提示后直接换措辞再交一遍）。
        if (self._gate_tool_execs >= 0
                and self._tool_execs == self._gate_tool_execs):
            self._gate_tool_execs = self._tool_execs
            return ("【完工自检-口头交付拦截】自上次自检以来你没有执行任何工具，"
                    "纯文字重申不构成交付证据。必须实际执行验证动作（跑测试/"
                    "编译/运行程序入口/核对文件），把命令与输出亮出来；产物"
                    "缺失就继续产出。" + tag)
        self._gate_tool_execs = self._tool_execs
        if missing:
            return ("【完工自检未通过】你声称已完成，但以下产物路径实际不存在：\n  "
                    + "\n  ".join(missing[:5])
                    + f"\n{budget_line}，远未到收工的时候。"
                      "请继续实际产出这些文件并使其满足指令的验收要求——"
                      "只描述方案不算完成。" + tag)
        # v0.7.1 递进强度：第 1 次自查式；第 ≥2 次必须动手验证并贴输出；
        # 第 ≥4 次逐条对验收标准给证据。空口确认不再直接放行。
        if self._grind_nudges >= 4:
            return ("【完工自检·证据清单】" + budget_line + "。把原始指令中的"
                    "验收标准逐条列出，每条给出可复核证据（文件路径 + 你刚"
                    "运行过的验证命令及其真实输出）。任何一条给不出证据就"
                    "回到工作继续完成它；全部给出后才允许最终结论。" + tag)
        if self._grind_nudges >= 2:
            return ("【完工自检·动手验证】" + budget_line + "。空口确认不算"
                    "数：立即实际运行你自建的验证（编译/测试/运行入口），把"
                    "命令与输出展示出来。有失败就继续修，直到通过再重试"
                    "收尾。" + tag)
        return ("【完工自检】" + budget_line + "。请重读原始任务指令，列出全部"
                "验收标准逐条自查（文件存在性、可运行性、边界情形）；有任何"
                "一条未实际验证通过就继续工作。若逐条确认全部满足，请再给出"
                "最终结论。" + tag)

    # ------------------------------------------------------------ 工具执行
    async def _grace_call(self, messages: list[Message], system: str,
                          emit: Emitter, summary: TurnSummary, *,
                          stream_events: bool = False,
                          stop: StopFlag | None = None) -> str:
        """轮次耗尽后的无工具收尾调用（hermes grace call）。

        tools=[] 从 API 层保证零 tool_use；usage 经 ChunkAssembler 并入
        summary（记账口径与主轮一致）；num_turns 不增；no-cache 通道。
        返回收尾正文（空串 = 放弃）。
        """
        if stop is not None and stop.requested:
            return ""
        gsys = (system + "\n\n（轮次已到上限。请立即基于已有信息写最终结论"
                "与未竟事项，不要再调用工具，不要展开新工作。）")
        asm = ChunkAssembler()
        synth = (StreamEventSynthesizer(
            model=getattr(self.provider, "model_name", "") or "")
            if stream_events else None)
        async for c in self.provider.chat(messages, [], gsys, use_cache=False):
            asm.feed(c)
            if synth is not None:
                for ev in synth.feed(c):
                    await _fire(emit, {"type": "stream_event", "event": ev})
            if c.kind == "error":
                return ""
        self._merge_usage(summary, asm)
        msg = Message(role="assistant", content=asm.blocks())
        messages.append(msg)
        payload = msg.to_dict()
        hits = getattr(self, "_turn_memory_hits", None)
        if hits:
            payload["memory_hits"] = hits       # P7：本 turn 注入清单
        self.session.append_event("assistant", payload)
        await _fire(emit, {"type": "assistant", "message": msg,
                           "message_id": asm.message_id,
                           **({"memory_hits": hits} if hits else {})})
        return "".join(asm.text_parts)

    async def _exec_tool(self, tu: ToolUseBlock, emit: Emitter) -> tuple[ToolResultBlock, str]:
        self._tool_execs += 1
        name = tu.name
        tool = self.tools.get(name)
        if tool is None:
            # P2：延迟索引内的工具被直接调用——当场物化并执行（模型从
            # ToolSearch 索引知道名字，直接点名不该吃一次「未知工具」错误；
            # 索引在 build 侧已过 disallow 预过滤，这里无需再判）
            t = self.mcp_deferred.get(name)
            if t is not None:
                self.tools[name] = tool = t
        content: str | list = ""
        is_error = False

        # 权限（deny 回填拒绝，模型自救改道）
        decision = self.permissions.check(name, tu.input)
        if not decision.allowed:
            content = f"权限拒绝：{decision.reason}"
            is_error = True
            # P2-3 v2：permission_request 上抛（宿主审批面/webui approve
            # 消费；params_hash 供规则化回写 P0-4 关联）——emit 为 None 时
            # （内部调用/测试）零开销跳过
            if self._emit_hook is not None:
                import hashlib as _hl
                import json as _jn
                await _fire(self._emit_hook, {"type": "permission_request",
                                              "payload": {
                                                  "tool": name,
                                                  "input": tu.input,
                                                  "reason": decision.reason,
                                                  "params_hash": _hl.sha256(
                                                      _jn.dumps(tu.input,
                                                                ensure_ascii=False,
                                                                sort_keys=True)
                                                      .encode()).hexdigest()[:16]}})
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
        # P3-3：LSP 诊断回注——写路径成功后查 mcp__lsp__diagnostics
        # （平台侧 lsp server 经 MCP 黑盒提供；缺席/失败静默——诊断是
        # 补充信息，不伤工具主结果）
        if not is_error and name in ("Edit", "Write", "MultiEdit"):
            fp = str(tu.input.get("file_path") or "").strip()
            diag = self.tools.get("mcp__lsp__diagnostics")
            if fp and diag is not None:
                try:
                    d = await asyncio.wait_for(
                        diag.execute({"file_path": fp}, self.ctx), timeout=20)
                    if d and d.strip():
                        content = f"{content}\n[lsp diagnostics]\n{d.strip()}"
                except Exception:  # noqa: BLE001 — LSP 是旁挂能力
                    pass
        if isinstance(content, str):
            content = content[:100_000]   # Truncator 最终防线（工具内已有细粒度纪律）
        # P2-3 v2：tool_use_failure 原生终态事件（T3 补发——manifest 声明
        # 久矣，引擎侧此前从未发出）。任何失败源（权限拒/钩子拦/未知工具/
        # ToolError/超时/内部异常）都发；v1 桥=user（tool_result is_error
        # 既有回填不变，stream_json v1 不外发原生行）
        if is_error and self._emit_hook is not None:
            await _fire(self._emit_hook, {
                "type": "tool_use_failure",
                "payload": {"tool": name, "tool_use_id": tu.id,
                            "reason": str(content)[:500]}})
        blk = ToolResultBlock(tool_use_id=tu.id, content=content, is_error=is_error)
        self.session.append_event("tool_result", blk.to_dict())
        await _fire(emit, {"type": "tool_result", "block": blk, "name": name})
        if name == "TodoWrite" and not is_error:
            # todos 事件（Emitter 契约已声明）：清单变更即外发，供 REPL/宿主 UI
            state = self.ctx.extras.get("state")
            if state is not None:
                await _fire(emit, {"type": "todos",
                                   "todos": [t.to_dict() for t in state.todos]})
        return blk, name

    # ------------------------------------------------------------ P1-8 repomap
    def _repomap_due(self) -> bool:
        from loadn.core import repomap as rm
        return rm.budget_tokens() > 0 and self._repomap_turns < 3

    def _build_system_with_map(self) -> str:
        """带仓库地图的 system（冷启动首 3 轮；mentioned=ctx 摸过的文件）。"""
        self._repomap_turns += 1
        self.assembler.with_repomap = True
        self.assembler.mentioned_files = {
            str(k) for k in self.ctx.files_touched}
        return self.assembler.build()

    # ------------------------------------------------------------ P1-7 titlegen
    def _titlegen(self, first_user_text: str) -> None:
        """引擎侧自动标题（平台形态由 webui titlegen 负责，LOADN_SESSION_ID
        在=平台托管 → 跳过防双写）。一次性：已有非空标题/已生成即跳过。"""
        import os as _os
        if _os.environ.get("LOADN_SESSION_ID"):
            return                          # 平台托管（webui titlegen 通道）
        if not first_user_text.strip() or len(first_user_text) < 6:
            return
        from loadn.persistence import db as db_mod
        try:
            with db_mod.conn(self.session.transcript.dir.parent.parent) as c:
                row = c.execute("SELECT title FROM sessions WHERE id=?",
                                (self.session.session_id,)).fetchone()
                if row and (row["title"] or "").strip():
                    return
        except Exception:                                  # noqa: BLE001
            return
        small = getattr(self.compactor, "small_model", None) \
            if self.compactor else None
        import asyncio as _aio

        async def _run():
            from pathlib import Path as _P
            tmpl = (_P(__file__).resolve().parent.parent / "prompts"
                    / "title.txt").read_text(encoding="utf-8")
            prompt = tmpl.format(first_message=first_user_text[:2000])
            from loadn.types import Message as _Msg
            from loadn.types import TextBlock as _TB
            text = ""
            try:
                async for ch in self.provider.chat(
                        [_Msg(role="user", content=[_TB(text=prompt)])], [],
                        "你是标题生成器，只输出标题本身。",
                        model=small, use_cache=False):
                    if ch.kind == "text_delta":
                        text += ch.text
            except Exception:                              # noqa: BLE001
                return
            title = text.strip().strip('"「』').splitlines()[0][:40]
            if not title:
                return
            try:
                with db_mod.conn(self.session.transcript.dir.parent.parent) as c:
                    db_mod.set_title(c, self.session.session_id, title)
            except Exception:                              # noqa: BLE001
                pass
        try:
            _aio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass

    # ------------------------------------------------------------ P1-4b memory
    def _spawn_bg(self, coro) -> None:
        """登记式后台任务（存引用防 GC + 供收尾排空）。无运行 loop 时静默弃。"""
        import asyncio as _aio
        try:
            t = _aio.get_running_loop().create_task(coro)
        except RuntimeError:
            return
        self._bg_tasks.add(t)
        t.add_done_callback(self._bg_tasks.discard)

    async def drain_bg(self, timeout_s: float = 30.0) -> None:
        """排空后台任务（-p 进程退出前调用——不排空则 asyncio.run 取消
        pending task，小模型调用写一半即灭）。超时兜底：宁可丢尾部也不挂死。"""
        import asyncio as _aio
        pending = [t for t in list(self._bg_tasks) if not t.done()]
        if not pending:
            return
        try:
            await _aio.wait_for(
                _aio.gather(*pending, return_exceptions=True), timeout_s)
        except _aio.TimeoutError:
            log.warning("后台任务排空超时 %ss（丢弃尾部）", timeout_s)
        self._bg_tasks.clear()

    def _memory_extract(self) -> None:
        """turn 成功后的记忆抽取（后台语义；失败静默不炸主循环）。"""
        from loadn.core import memory as mem_mod
        small = getattr(self.compactor, "small_model", None) \
            if self.compactor else None

        async def _run():
            return await mem_mod.extract_and_store(
                self.provider, self.cwd, self.session, small_model=small)
        self._spawn_bg(_run())

    # ------------------------------------------------------------ P12 经验固化
    def _consolidate_check(self) -> None:
        """纠错检测（turn 成功后；确定性词表禁模型猜）。失败静默。"""
        self._spawn_bg(self._consolidate_async())

    async def _consolidate_async(self) -> None:
        from loadn import consolidate as _c
        _c.maybe_suggest(self.cwd, self.session)

    async def _reflect_if_due(self, summary_text: str) -> None:
        """压缩后反思（默认 off；cheap 通道 → 记忆域 draft 待确认）。"""
        from loadn import consolidate as _c
        if not _c.reflection_enabled() or not summary_text:
            return
        small = (getattr(self.compactor, "small_model", None)
                 if self.compactor else None)
        await _c.reflect_after_compact(self.provider, self.cwd, self.session,
                                       summary_text, small_model=small)

    # ------------------------------------------------------------ P1-2 tool-repair
    def _repair_tool_calls(self, blocks: list) -> list:
        """纯文本工具调用块 → 真 ToolUseBlock（openclaw promote 同构）。

        standalone 语义天然保护代码块/引用内文本（见 core/tool_repair.py）。
        产物进 blocks 后由既有 tool_use 执行路径处理——权限引擎、PreToolUse
        钩子、审批门一个不少。transcript 记 tool_call_repaired 事件。
        """
        from loadn.core import tool_repair as tr
        from loadn.types import ToolUseBlock as TUB
        texts = [getattr(b, "text", "") for b in blocks]
        merged = "\n".join(t for t in texts if t)
        calls = tr.try_repair(merged, set(self.tools.keys())
                              | set(self.mcp_deferred))
        if not calls:
            return blocks
        try:
            self.session.append_event("tool_call_repaired", {
                "calls": [{"name": c.name, "syntax": c.syntax}
                          for c in calls],
                "residual_text": tr.strip_blocks(merged, calls)[:200]})
        except Exception:                                  # noqa: BLE001
            pass
        log.info("tool-call-repair：提升 %d 个纯文本调用 %s",
                 len(calls), [c.name for c in calls])
        return [TUB(id=f"repair_{i}", name=c.name, input=c.args)
                for i, c in enumerate(calls)]

    # ------------------------------------------------------------ P1-6 cache-warm
    def _warmer_bump(self) -> None:
        """失效在途保温（新 turn / 压缩改写 messages——旧断点已无意义）。"""
        if self._warmer is not None:
            self._warmer.bump()
        if self._warmer_task is not None:
            self._warmer_task.cancel()
            self._warmer_task = None

    def _warmer_schedule(self) -> None:
        """turn 成功后挂起空闲保温任务（长命进程：REPL / P2-1 daemon）。"""
        from loadn.core.cache_warmer import CacheWarmer, warm_enabled
        self._warmer_bump()
        if not (warm_enabled() and self._last_replay
                and self._last_input_tokens > 0):
            return
        model = getattr(self.provider, "model_name", "") or ""
        if not getattr(self.provider, "replayable_for_cache", True):
            return                      # thinking-budget 模型：重放键变，不保温
        self._warmer = CacheWarmer(
            replay_fn=self._last_replay, model=model,
            record_usage=self._record_warm_usage)
        self._warmer_task = asyncio.get_event_loop().create_task(
            self._warmer.run_idle(self._last_input_tokens))

    def _record_warm_usage(self, usage: dict) -> None:
        """保温用量入账（cache_warm 分类标记；索引库 json 原样落）。"""
        model = getattr(self.provider, "model_name", "") or ""
        summary = TurnSummary()
        summary.usage = dict(usage)
        summary.model_usage = {model: dict(usage)}
        try:
            self.session.record_usage(summary)
        except Exception:                                  # noqa: BLE001
            pass

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
    """provider 错误（loop 消费）：retriable 走重试；reason 来自
    retry.classify_error——context_overflow 走压缩自救路径。"""

    def __init__(self, msg: str, retriable: bool = False) -> None:
        super().__init__(msg)
        self.retriable = retriable
        self.reason = classify_error(str(msg))["reason"]


def _inline(content) -> str:
    if isinstance(content, str):
        return content[:TOOL_RESULT_INLINE_MAX]
    return json.dumps(content, ensure_ascii=False)[:TOOL_RESULT_INLINE_MAX]

