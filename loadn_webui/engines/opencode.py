"""opencode 引擎（v1.18.x）：headless `opencode run --format json` 的子进程接缝。

与 claude CLI 的两点方言差由本模块吸收，webui 外层（runner/engine/SSE）零改动：

  1. 事件面：opencode 输出自有 NDJSON 事件（session.* / message.part.*），完成
     信号是 session.status(properties.status.type=="idle") 而非 result 行——
     OpencodeEventAdapter 有状态地把 NDJSON 归一化成 claude stream-json 形状；
  2. flag 面：无 -p / 无 --max-turns，权限自动批准是 --auto，续会话是
     `--session <id>`，fresh 不传任何 session flag（session.created 事件上报
     引擎自建的 ses_… id，由合成 result 事件带回 engine 侧簿记）。

配置注入走 env OPENCODE_CONFIG_CONTENT（内联 JSON，最高优先合并）。snapshot:false
必须注入——否则 opencode 会在 workspace/<sid>/ 下建内部 git 仓，污染 files 面板。

未来 `opencode serve` 常驻通道只改本 Spec（resolve_bin→serve 端点、build_argv→
HTTP 调用），runner / 适配器 / engine 外层不动。
"""
from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING

from ..config import CONFIG
from ..util import get_logger
from .base import EngineSpec, EventAdapter

if TYPE_CHECKING:
    from ..claude_runner import TurnCall

log = get_logger(__name__)

# --variant 接受的 reasoning effort 词表（白名单外的值省略 flag，防 CLI 秒拒）
_EFFORT_VARIANTS = {"high", "medium", "low", "max", "minimal"}

# 「无 --max-turns」降级只警告一次（进程级）：每个 turn 都刷会淹没日志
_no_max_turns_warned = False


# v1.18.29 实测（2026-09-16）：share 要字符串枚举（"manual"|"auto"|"disabled"），
# permission 是 工具键→规则 的 map（不是 deny 字符串数组——schema 校验秒拒）。
# 工具键与 claude 工具名不同（AskUserQuestion→question、WebSearch→websearch）。
_OC_PERMISSION_KEYS = {"read", "edit", "glob", "grep", "list", "bash", "task",
                       "external_directory", "todowrite", "webfetch", "websearch",
                       "lsp", "skill", "question", "doom_loop"}
_TOOL_TO_PERM = {"askuserquestion": "question", "todowrite": "todowrite"}


class OpencodeSpec(EngineSpec):
    name = "opencode"
    supports_transcript = False   # 无 transcript 兜底：判死只剩 stdout 静默 + 硬超时
    max_turns_flag = False        # v1.18.x 无 --max-turns：轮次收敛只靠 timeout_s
    session_id_domain = "any"     # 会话 id 形如 ses_<hex>（非 UUID，不能用 _uuid_or_new）

    def resolve_bin(self) -> str | list[str]:
        override = CONFIG.engines.opencode.bin or os.environ.get("LOADN_OPENCODE_BIN") \
            or os.environ.get("WORKDADDY_OPENCODE_BIN")
        if override:
            return override
        # 未安装返回 ""：build_argv 保留空 argv[0]，create_subprocess_exec 抛
        # OSError 由 runner 统一转 spawn_failed（组装期不抛，走既有失败面）
        return shutil.which("opencode") or ""

    def build_env(self, call: TurnCall | None = None) -> dict:
        # 内联配置最高优先合并：snapshot/autoupdate 关 + share disabled + 交互弹窗
        # 工具与 profile 黑名单映射进 permission map（未知工具键忽略——schema
        # 校验拒未知键）。"model" 不进这里（-m 已在 argv 传）。
        perm: dict[str, str] = {}
        for name in ["AskUserQuestion", *(call.disallowed_tools if call is not None else [])]:
            key = _TOOL_TO_PERM.get(name.lower(), name.lower())
            if key in _OC_PERMISSION_KEYS:
                perm[key] = "deny"
        content: dict = {"snapshot": False, "share": "disabled", "autoupdate": False}
        if perm:
            content["permission"] = perm
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(content, ensure_ascii=False)}

    def build_argv(self, call: TurnCall) -> tuple[list[str], dict]:
        global _no_max_turns_warned
        cmd = [self.resolve_bin(), "run", "--format", "json", "--auto"]
        cmd += list(CONFIG.engines.opencode.extra_args or [])
        model = call.model or CONFIG.engines.opencode.model
        if model:
            if "/" not in model:
                provider = (CONFIG.engines.opencode.provider
                            or CONFIG.engines.opencode_provider)
                model = f"{provider}/{model}" if provider else model
            cmd += ["--model", model]
        if call.effort and call.effort in _EFFORT_VARIANTS:
            cmd += ["--variant", call.effort]
        if call.max_turns:   # 无对应 flag：降级省略（只警告一次）
            if not _no_max_turns_warned:
                log.warning("opencode 无 --max-turns flag：轮次上限省略，靠 timeout_s=%ss 收敛",
                            call.timeout_s)
                _no_max_turns_warned = True
        # fresh 不传任何 session flag（引擎自建会话）；resume 携带 engine 侧登记的 id
        if call.resume and call.session_id:
            cmd += ["--session", call.session_id]
        cmd += ["--", call.prompt]   # 三轮修：-- 终结符 + argv[-1] 契约
        return cmd, self.build_env(call)

    def new_session_id(self) -> str:
        return ""   # 轮换 = 下次 fresh 不传 id，由 opencode 自建新 ses_…

    def is_in_use_error(self, err: str | None) -> bool:
        return False   # 无 claude 式 session-id 锁：秒拒不走「transcript 已存在」翻转

    def adapter(self) -> OpencodeEventAdapter:
        return OpencodeEventAdapter()


class OpencodeEventAdapter(EventAdapter):
    """opencode NDJSON → claude stream-json 的有状态归一化器。

    核心不变量：**绝不发增量**。消费端（engine._consume）对 text/thinking 块逐块
    追加，而 opencode 的 message.part.updated 是同 part 的全量快照流——这里缓冲
    每 part 的最新快照，只在「同 message 出现新 part / tool part 首见 / 收步
    step-finish / 流终止」时一次性发射整块；emitted 集合保证每个 part 至多外发
    一次（发射后的新快照直接丢弃，重发必与追加语义重复）。

    完成信号 session.status(idle) 只置标记；result 事件恒由 finalize 合成（见过
    session.created 才产——fastfail 等会话未建立的情形无 result，runner 走
    exit 码路）。
    """

    def __init__(self) -> None:
        self.session_id = ""
        self.created_seen = False
        # msg_id -> part_id -> 最新快照（dict 插入序即 part 首见顺序）
        self.parts: dict[str, dict[str, dict]] = {}
        self.emitted: set[str] = set()          # 已外发整块/工具事件的 part id
        self.results_emitted: set[str] = set()  # 已外发 tool_result 的 part id
        # usage 四键累加器（与 db.NUMERIC_USAGE_KEYS 同名对齐）
        self.usage = {"input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self.cost: float | None = None
        self.model_id = ""
        self.n_steps = 0
        self.final_texts: list[str] = []        # 已外发 text 整块（result 拼接源）
        self.error: str | None = None
        self.idle_seen = False
        self._init_sent = False   # 首个内容事件前补发 system/init（扁平流无 session.created）
        # session.updated 的兜底快照（info.tokens/cost 是会话累计值，事件期不采纳
        # ——step-finish 缺失时才在 finalize 兜底，防与 step 记账双计）
        self._sess_tokens: dict | None = None
        self._sess_cost: float | None = None

    # ------------------------------------------------------------ 内部工具
    @staticmethod
    def _msg_id(ev: dict, part: dict) -> str:
        """事件所属 message id：顶层 messageID 优先，其次 part 内嵌，兜底匿名桶。"""
        for src in (ev, part):
            v = src.get("messageID")
            if v:
                return str(v)
        return "_anon"

    @staticmethod
    def _assistant(msg_id: str, blocks: list[dict]) -> dict:
        return {"type": "assistant",
                "message": {"id": msg_id, "role": "assistant", "content": blocks}}

    def _flush_message(self, msg_id: str, keep: str | None = None) -> list[dict]:
        """把 message 的未发射 text/reasoning 整块冲出（keep=仍在流式累积的 part）。

        text→text block、reasoning→thinking block，按 part 首见顺序；已发射的
        part 跳过（快照重发会与消费端追加语义重复）。
        """
        bucket = self.parts.get(msg_id) or {}
        blocks: list[dict] = []
        for pid, part in list(bucket.items()):
            if pid == keep or pid in self.emitted:
                continue
            self.emitted.add(pid)
            ptype = part.get("type")
            if ptype == "text":
                text = part.get("text") or ""
                if text:
                    blocks.append({"type": "text", "text": text})
                    self.final_texts.append(text)
            elif ptype == "reasoning":
                think = part.get("text") or ""
                if think:
                    blocks.append({"type": "thinking", "thinking": think})
        return [self._assistant(msg_id, blocks)] if blocks else []

    # ------------------------------------------------------------ feed
    def feed(self, ev: dict) -> list[dict]:
        t = ev.get("type") or ""
        sid = ev.get("sessionID")
        if sid and not self.session_id:
            self.session_id = str(sid)

        # —— v1.18 实测扁平事件（2026-09-16 真机验证）：顶层 type 即 part 类型
        # （snake_case：text/tool_use/step_start/step_finish/...），part 内嵌在
        # ev["part"]。与文档的 message.part.updated 信封并存——两种形状都归一。
        if t in ("text", "reasoning", "tool", "tool_use", "step-finish",
                 "step_finish"):
            part = ev.get("part") if isinstance(ev.get("part"), dict) else ev
            self.created_seen = True
            if t in ("tool", "tool_use"):
                out = self._feed_flat_tool(part)
            else:
                out = self._feed_part(ev, part)
            if not self._init_sent:
                self._init_sent = True
                out = [{"type": "system", "subtype": "init",
                        "session_id": self.session_id, "model": self.model_id,
                        "tools": [], "mcp_servers": []}] + out
            return out
        if t in ("step_start", "step-start"):
            self.created_seen = True
            if not self._init_sent:
                self._init_sent = True
                return [{"type": "system", "subtype": "init",
                         "session_id": self.session_id, "model": self.model_id,
                         "tools": [], "mcp_servers": []}]
            return []
        if t in ("session.error", "error"):
            err = ev.get("error") or ev.get("message")
            self.error = str(err) if err else "opencode error（无错误详情）"
            return []

        # —— 文档信封形状（serve 模式 / 前置版本）
        if t == "session.created":
            self.created_seen = True
            if sid:
                self.session_id = str(sid)
            info = ev.get("info") or {}
            if not self.model_id and info.get("modelID"):
                self.model_id = str(info["modelID"])
            return [{"type": "system", "subtype": "init", "session_id": self.session_id,
                     "model": self.model_id, "tools": [], "mcp_servers": []}]
        if t == "session.updated":
            info = ev.get("info") or {}
            if not self.model_id and info.get("modelID"):
                self.model_id = str(info["modelID"])
            if isinstance(info.get("tokens"), dict):
                self._sess_tokens = info["tokens"]
            if info.get("cost") is not None:
                self._sess_cost = info["cost"]
            return []
        if t == "session.status":
            props = ev.get("properties") or {}
            status = props.get("status")
            if isinstance(status, dict) and status.get("type") == "idle":
                self.idle_seen = True     # turn 完成信号（busy 等其余态忽略）
            return []
        if t == "message.part.updated":
            part = ev.get("part")
            if not isinstance(part, dict):
                return []
            return self._feed_part(ev, part)
        # delta/snapshot/patch/compaction/file/subtask/retry/message.updated/
        # session.diff/permission.asked 等：无对应 stream-json 映射，丢弃
        return []

    def _feed_flat_tool(self, part: dict) -> list[dict]:
        """v1.18 实测扁平 tool_use 事件：state 是对象（status/input/output/metadata），
        工具调用与结果常在同一次事件到达。文档信封的 state 字符串形态也兼容
        （pending/running 只发 tool_use，等后续事件补 result）。"""
        pid = str(part.get("id") or "")
        msg_id = self._msg_id({}, part)
        state = part.get("state")
        if isinstance(state, str):
            state = {"status": state,
                     "input": part.get("input") or {},
                     "output": part.get("output") or ""}
        state = state if isinstance(state, dict) else {}
        status = str(state.get("status") or "")
        out: list[dict] = []
        if pid and pid not in self.emitted:
            out += self._flush_message(msg_id)
            self.emitted.add(pid)
            out.append(self._assistant(msg_id, [
                {"type": "tool_use", "id": pid, "name": part.get("tool") or "?",
                 "input": state.get("input") or {}}]))
        if status in ("completed", "error") and pid and pid not in self.results_emitted:
            self.results_emitted.add(pid)
            out.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": pid,
                 "content": str(state.get("output") or ""),
                 "is_error": status == "error"}]}})
        return out

    def _feed_part(self, ev: dict, part: dict) -> list[dict]:
        ptype = str(part.get("type") or "").replace("_", "-")   # step_finish → step-finish
        msg_id = self._msg_id(ev, part)
        bucket = self.parts.setdefault(msg_id, {})
        pid = str(part.get("id") or f"{msg_id}:anon")

        if ptype in ("text", "reasoning"):
            if pid in self.emitted:
                return []      # 整块已外发：后续快照丢弃
            out: list[dict] = []
            # 同 message 出现不同 part → 前面的 part 已定稿，整块冲出（本 part 继续
            # 缓冲最新快照）
            if any(k != pid for k in bucket):
                out += self._flush_message(msg_id, keep=pid)
            bucket[pid] = part
            return out

        if ptype == "tool":
            state = part.get("state")
            out = []
            if state in ("pending", "running", "completed", "error"):
                # 首见该 tool part：先把同 message 的 text/reasoning 整块冲出。
                # 快照直接跳到 completed/error 时也补发 tool_use——保证与
                # tool_result 的 id 配对（engine 侧靠 tool_use_id 回填工具卡片）。
                if pid not in self.emitted:
                    out += self._flush_message(msg_id)
                    self.emitted.add(pid)
                    out.append(self._assistant(msg_id, [
                        {"type": "tool_use", "id": pid, "name": part.get("tool") or "?",
                         "input": part.get("input") or {}}]))
                if state in ("completed", "error") and pid not in self.results_emitted:
                    self.results_emitted.add(pid)
                    out.append({"type": "user", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": pid,
                         "content": str(part.get("output") or ""),
                         "is_error": state == "error"}]}})
            return out   # 未知 state（pending→running 之外的中间态）忽略

        if ptype == "step-finish":
            # 收步：本步 text/reasoning 已定稿，整块冲出后记账
            out = self._flush_message(msg_id)
            tok = part.get("tokens") or {}
            cache = tok.get("cache") or {}
            self.usage["input_tokens"] += int(tok.get("input") or 0)
            self.usage["output_tokens"] += (int(tok.get("output") or 0)
                                            + int(tok.get("reasoning") or 0))
            self.usage["cache_read_input_tokens"] += int(cache.get("read") or 0)
            self.usage["cache_creation_input_tokens"] += int(cache.get("write") or 0)
            cost = part.get("cost")
            if cost is not None:
                self.cost = (self.cost or 0.0) + float(cost)
            self.n_steps += 1
            if not self.model_id and part.get("model"):
                self.model_id = str(part["model"])
            return out

        # file/subtask/snapshot/patch/retry/compaction：无对应 stream-json 块，丢弃
        return []

    # ------------------------------------------------------------ finalize
    def finalize(self, rc: int | None, duration_s: float) -> list[dict]:
        # 流终止（idle 后进程退出 / EOF / 被杀）：先把所有 message 的未发射整块冲出
        out: list[dict] = []
        for msg_id in list(self.parts):
            out += self._flush_message(msg_id)
        if not self.created_seen:
            return out   # 会话从未建立（fastfail 等）：无 result 可记
        usage = dict(self.usage)
        if self.n_steps == 0 and isinstance(self._sess_tokens, dict):
            # step-finish 缺失：采纳 session.updated 的会话累计 tokens/cost 兜底
            tok, cache = self._sess_tokens, self._sess_tokens.get("cache") or {}
            usage = {"input_tokens": int(tok.get("input") or 0),
                     "output_tokens": int(tok.get("output") or 0)
                     + int(tok.get("reasoning") or 0),
                     "cache_read_input_tokens": int(cache.get("read") or 0),
                     "cache_creation_input_tokens": int(cache.get("write") or 0)}
            if self.cost is None and self._sess_cost is not None:
                self.cost = float(self._sess_cost)
        # 完成判定：文档信封有 session.status idle；v1.18 run 子进程流没有——
        # 进程正常退出（rc==0）即完成（2026-09-16 真机验证事件序列无终止事件）
        subtype = "success" if ((self.idle_seen or rc == 0) and not self.error) \
            else "error_during_execution"
        result_text = self.error if self.error else "\n\n".join(self.final_texts)
        return out + [{
            "type": "result", "subtype": subtype,
            "session_id": self.session_id,
            "result": result_text,
            "total_cost_usd": self.cost,
            "usage": usage,
            "modelUsage": {self.model_id or "opencode": {
                "inputTokens": usage["input_tokens"],
                "outputTokens": usage["output_tokens"],
                "cacheReadInputTokens": usage["cache_read_input_tokens"],
                "cacheCreationInputTokens": usage["cache_creation_input_tokens"],
                "webSearchRequests": 0,
                "costUSD": self.cost if self.cost is not None else 0.0}},
            "num_turns": self.n_steps,
            "duration_ms": int(duration_s * 1000)}]
