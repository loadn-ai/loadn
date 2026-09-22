"""会话执行引擎：turn 队列、并发信号量、SSE hub、resume 簿记、会话轮换。

所有权模型（前台/后台统一的关键）：turn 生命周期归 engine（asyncio task +
子进程），SSE 端点只是订阅者——浏览器关掉/转后台，turn 照跑，事件照写
session_events 表；重连凭 Last-Event-ID 补发追平。

每会话 turn 串行（FIFO），跨会话并行（全局信号量，订阅并发保护）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db as db_mod
from . import engines as engines_mod
from . import workspace as ws_mod
from .claude_runner import StopHandle, TurnCall, pid_alive_for_turn, run_turn
from .config import CONFIG, PATHS
from .util import get_logger, iso

log = get_logger(__name__)


def _msg_attachments(blocks_json: str | None) -> list[dict]:
    """用户消息 blocks_json 里的附件条目（历史消息无 blocks → 空）。"""
    if not blocks_json:
        return []
    try:
        return [b for b in json.loads(blocks_json) if isinstance(b, dict)
                and b.get("type") == "attachment"]
    except (json.JSONDecodeError, TypeError):
        return []


def _attachment_block(atts: list[dict]) -> str:
    """附件 → 追加进 prompt 的说明块；content 里存干净文本，组 prompt 时拼装。"""
    if not atts:
        return ""
    lines = ["", "", "【附件】用户随本消息上传了以下文件到工作区："]
    for a in atts:
        kind = "图片，可直接用 Read 查看" if a.get("is_image") \
            else "文档，先用 file-parse skill 解析后再引用"
        size = f"，约 {a['kb']}KB" if a.get("kb") is not None else ""
        lines.append(f"- {a['path']}（{kind}{size}）")
    lines.append("不要凭文件名猜测附件内容。")
    return "\n".join(lines)

# 工具卡片摘要的候选字段（承袭 papergo webapp._transcript_tail 的提取法）
_BRIEF_KEYS = ("query", "command", "file_path", "path", "pattern", "url",
               "description", "prompt", "skill")
_FILE_TOOLS = {"Write", "Edit", "NotebookEdit", "Bash"}
# 详情展示的截断上限（SSE 事件与 blocks_json 同一份，防大 payload 打爆前端）
_THINK_CAP = 20000        # 单个 thinking 块
_RESULT_CAP = 4000        # 单个 tool_result 内容（保留换行）
_INPUT_VAL_CAP = 1200     # tool input 单值
_INPUT_KEY_MAX = 24       # tool input 最多展示的字段数
# resume 快速失败判定：CLI 拒绝（会话不存在等）在秒级退出。opencode 无效
# session 走 session.error 事件而非 exit 1——error subtype 一并计入。
_FAST_FAIL_S = 15


def _parse_ts(s: str | None) -> float:
    """UTC ISO 时间串 → epoch（fromisoformat 正确处理时区；失败回退 now）。"""
    if not s:
        return time.time()
    from datetime import datetime
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return time.time()


def _is_fast_fail(res) -> bool:
    return (res.duration_s < _FAST_FAIL_S and not res.usage
            and (res.exit_code == 1 or (res.subtype or "").startswith("error")))


def _tool_brief(name: str, inp: dict) -> str:
    for k in _BRIEF_KEYS:
        v = inp.get(k)
        if v:
            return str(v)[:110]
    return ""


def _clip_input(inp: Any) -> dict:
    """tool input 的展示版：值字符串化截断（Write/Edit 的整文件内容别全量下发）。"""
    if not isinstance(inp, dict):
        return {}
    out: dict = {}
    for i, (k, v) in enumerate(inp.items()):
        if i >= _INPUT_KEY_MAX:
            break
        if v is None:
            continue
        out[str(k)] = v if isinstance(v, (int, float, bool)) else str(v)[:_INPUT_VAL_CAP]
    return out


def _content_text(content: Any) -> str:
    """tool_result 的 content 可能是 str 或 block 列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


@dataclass
class ActiveTurn:
    turn_id: int
    session_id: str
    stop: StopHandle
    started_at: float
    todos: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    tool_names: dict = field(default_factory=dict)   # tool_use_id → 工具名（tool_result 配对用）
    # stream_event 逐 delta 直播（--verbose 下 claude/hahaness 都发）：缓冲合流
    # 后按 0.5s/320 字符冲刷成 delta 事件；整块 assistant 到达时若该类块已
    # 流式发射过则跳过重复直播（数据层照常入账）
    delta_buf: dict = field(default_factory=lambda: {"think": "", "text": ""})
    delta_last_flush: dict = field(default_factory=lambda: {"think": 0.0, "text": 0.0})
    streamed_kinds: set = field(default_factory=set)
    # 收养静默重放期：状态照常重建（at.texts/blocks/delta 抑制表）但 SSE 不
    # 重发（session_events 已有停机前的事件，重发=重连客户端看到双份）
    quiet: bool = False
    # 随时插话（steering）：本 turn 期间 API 写入 .steer.jsonl 的插话；
    # hahaness 注入上下文时回 steer 事件标记 consumed——turn 结束仍未
    # 消费的（插话落在最后一轮后）由 _finish 回队列为新 turn，不丢话
    steers: list = field(default_factory=list)


class Engine:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[int]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self.active: dict[int, ActiveTurn] = {}
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._sem = asyncio.Semaphore(CONFIG.run.max_concurrent_turns)
        self._global_lock = asyncio.Lock()
        # 收养闸：sid → Future——收养期间挡住该 session 的 requeue turn 与新
        # submit（否则同 session 双跑破坏串行语义）；adopt 任务 finally 里放行
        self._adopt_gates: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------ SSE hub
    def subscribe(self, sid: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(sid, set()).add(q)
        return q

    def unsubscribe(self, sid: str, q: asyncio.Queue) -> None:
        self._subs.get(sid, set()).discard(q)
        if not self._subs.get(sid):
            self._subs.pop(sid, None)

    def publish(self, sid: str, type_: str, data: dict,
                turn_id: int | None = None) -> int:
        """事件落库（拿自增 id 作为 SSE event id）→ 推给所有订阅者。"""
        with db_mod.conn() as c:
            eid = db_mod.add_event(c, sid, turn_id, type_, data)
        for q in list(self._subs.get(sid, ())):
            q.put_nowait((eid, type_, data))
        return eid

    def _emit(self, at: ActiveTurn | None, sid: str, type_: str, data: dict) -> None:
        """turn 生命周期事件的 quiet 收口：收养静默重放期不落库不推流。"""
        if at is not None and at.quiet:
            return
        self.publish(sid, type_, data, at.turn_id if at is not None else None)

    def steer_if_running(self, sid: str, text: str) -> int | None:
        """该 session 有 running 的 hahaness turn → 写 .steer.jsonl + at.steers
        记账 + SSE "steer"，返回 turn_id；否则 None（调用方回落 submit 排队）。

        调用方：API /steer（用户插话，route 层截断 4000）；scheduler.fire
        （定时唤醒撞上运行中任务实时注入——比赛开赛不能排队等，_WRAP 包装
        不截断）。不截断 text——长度策略归调用方。
        """
        for tid, at in self.active.items():
            if at.session_id != sid:
                continue
            with db_mod.conn() as c:
                row = db_mod.get_turn(c, tid)
                eng = row["engine"] if row is not None else None
            if eng in ("hahaness", "loadn"):
                path = ws_mod.ws_of(sid) / f".steer.{sid}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"ts": iso(), "text": text},
                                       ensure_ascii=False) + "\n")
                at.steers.append({"text": text, "consumed": False})
                self.publish(sid, "steer", {"turn_id": tid, "text": text}, tid)
                return tid
            break      # 每 session 串行，至多一个 running turn
        return None

    # ------------------------------------------------------------ 提交
    async def submit(self, sid: str, text: str, mode: str = "foreground",
                     attachments: list[dict] | None = None) -> int:
        text = (text or "").strip()
        if not text:
            raise ValueError("消息不能为空")
        # W6.4 熔断检查（canary 命中/kill switch 锁定的会话拒绝新消息）
        try:
            from . import canary as _canary
            why = _canary.is_locked(sid)
            if why:
                raise PermissionError(f"会话已熔断（{why}）——kill switch/canary 命中")
        except PermissionError:
            raise
        blocks_json = None
        if attachments:
            blocks_json = json.dumps(
                [{"type": "attachment", "path": a["path"], "name": a.get("name"),
                  "kb": a.get("kb"), "is_image": bool(a.get("is_image"))}
                 for a in attachments], ensure_ascii=False)
        with db_mod.conn() as c:
            if not db_mod.get_session(c, sid):
                raise KeyError(f"session not found: {sid}")
            tid = db_mod.create_turn(c, session_id=sid, status="queued", mode=mode)
            mid = db_mod.add_message(c, session_id=sid, turn_id=tid, role="user",
                                     content=text, blocks_json=blocks_json)
            db_mod.update_turn(c, tid, message_id=mid)
            db_mod.update_session(c, sid)
            # 自动标题：标记在（未显式命名/未手动改名/尚未成功过）→ 后台生成
            want_title = CONFIG.titlegen.enabled and CONFIG.titlegen.api_key \
                and db_mod.kv_get(c, f"title_auto:{sid}") is not None
        self.publish(sid, "turn_queued", {"turn_id": tid, "mode": mode}, tid)
        if want_title:
            from . import titlegen
            asyncio.create_task(titlegen.maybe_auto_title(sid, text))
        q = self._queues.get(sid)
        if q is None:
            q = asyncio.Queue()
            self._queues[sid] = q
        q.put_nowait(tid)
        self._ensure_worker(sid)
        return tid

    def _ensure_worker(self, sid: str) -> None:
        t = self._workers.get(sid)
        if t is None or t.done():
            self._workers[sid] = asyncio.create_task(self._session_worker(sid))

    async def _session_worker(self, sid: str) -> None:
        q = self._queues[sid]
        while True:
            tid = await q.get()
            async with self._sem:
                try:
                    await self._run_turn(sid, tid)
                except Exception:
                    log.exception("turn %s 执行异常", tid)
                    with db_mod.conn() as c:
                        db_mod.update_turn(c, tid, status="error",
                                           error="engine_internal_error", finished_at=iso())
                    self.publish(sid, "turn_error",
                                 {"turn_id": tid, "error": "engine_internal_error"}, tid)

    # ------------------------------------------------------------ 停止
    def _drain_hook_audit(self, sid: str, tid: int) -> None:
        """回收沙箱内 hook 判定侧车 → 审计账本（宿主侧唯一写库点）。"""
        import json as _json
        from . import audit as _audit
        from . import workspace as _ws
        f = _ws.ws_of(sid) / ".loadn-hook-audit.jsonl"
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for ln in lines[-50:]:
            try:
                e = _json.loads(ln)
            except _json.JSONDecodeError:
                continue
            _audit.audit("permission_decision",
                         {"action": e.get("action"), "reason": e.get("reason"),
                          "rule": "hook", "source": "hook-sandbox",
                          "tool": e.get("tool"), "subject": e.get("subject")},
                         sid=sid, turn_id=tid)
        try:
            f.unlink()
        except OSError:
            pass

    def _spotcheck_claims(self, sid: str, tid: int, res) -> None:
        """W6.3 谎报抽查：agent 自称的 artifacts 路径核对（存在+mtime 窗）。"""
        import re as _re
        from datetime import datetime as _dt
        from . import audit as _audit
        from . import workspace as _ws_mod
        text = (getattr(res, "result_text", "") or "")
        ws = _ws_mod.ws_of(sid)
        t_start = time.time() - max(2 * 3600, 3600)
        paths = list(
            _re.findall(r"(?:artifacts|notes|work)/[\w./-]+\.[\w]+", text))[:8]
        for rel in paths:
            f = ws / rel
            if not f.exists():
                _audit.audit("anomaly", {"kind": "claimed_missing", "sid": sid,
                                      "turn": tid, "path": rel}, sid=sid, turn_id=tid)
            else:
                try:
                    if f.stat().st_mtime < t_start:
                        _audit.audit("anomaly", {"kind": "claimed_stale", "sid": sid,
                                        "turn": tid, "path": rel},
                               sid=sid, turn_id=tid)
                except OSError:
                    pass

    async def stop_turn(self, tid: int) -> bool:
        with db_mod.conn() as c:
            turn = db_mod.get_turn(c, tid)
        if turn is None:
            return False
        at = self.active.get(tid)
        if at is not None:
            at.stop.stop()
            return True
        if turn["status"] == "queued":
            with db_mod.conn() as c:
                db_mod.update_turn(c, tid, status="stopped", finished_at=iso())
            self.publish(turn["session_id"], "turn_stopped", {"turn_id": tid}, tid)
            return True
        return False

    # ------------------------------------------------------------ 执行
    @staticmethod
    def _align_engine(sid: str, sess, spec) -> dict:
        """引擎切换时迁移会话 id（session id 域不同，绝不能跨引擎 resume）。

        旧引擎的 id 存档进 engine_session_ids map；新引擎取回自己的存档（fresh
        位按存档有无翻转）。返回更新后的 sess（dict；DB 已写）。
        """
        cur = sess["engine"] or "claude"
        if cur == spec.name:
            return sess
        try:
            archive = json.loads(sess["engine_session_ids"] or "{}")
            if not isinstance(archive, dict):
                archive = {}
        except (TypeError, json.JSONDecodeError):
            archive = {}
        log.info("会话 %s 引擎切换 %s → %s（旧 id 存档）", sid, cur, spec.name)
        if sess["claude_session_id"]:
            archive[cur] = sess["claude_session_id"]
        prev = archive.pop(spec.name, None)
        out = {k: sess[k] for k in sess.keys()}
        out["engine"] = spec.name
        out["engine_session_ids"] = json.dumps(archive, ensure_ascii=False) if archive else None
        out["claude_session_id"] = prev or ""
        out["session_fresh"] = 0 if prev else 1
        out["resume_failures"] = 0
        with db_mod.conn() as c:
            db_mod.update_session(
                c, sid, engine=spec.name,
                engine_session_ids=out["engine_session_ids"],
                claude_session_id=prev or "", session_fresh=out["session_fresh"],
                resume_failures=0)
        return out

    async def _run_turn(self, sid: str, tid: int, _anchor: str = "") -> None:
        # 收养闸：该 session 有正在收养的幸存 turn 时等它放行（串行语义——
        # 否则收养 turn 与 requeue/新 submit turn 同 session 双跑）
        gate = self._adopt_gates.get(sid)
        if gate is not None:
            await gate
        with db_mod.conn() as c:
            sess = db_mod.get_session(c, sid)
            turn = db_mod.get_turn(c, tid)
            # turn 可能已被撤回删行（队列里残留的 tid 轮到执行）——判空走干净跳过
            msg = c.execute("SELECT content, blocks_json FROM messages WHERE id=?",
                            (turn["message_id"],)).fetchone() if turn is not None else None
        if sess is None or turn is None or msg is None:
            log.error("turn %s 上下文缺失，跳过", tid)
            return
        from . import profile as profile_mod
        prof = profile_mod.get(sess["profile"])
        # 引擎优先级：聊天框的会话级覆盖 > profile.engine（_align_engine 的
        # id 迁移对两者同一处理）
        spec = engines_mod.resolve(sess["engine_override"] or prof.engine)
        sess = self._align_engine(sid, sess, spec)

        prompt = msg["content"] + _attachment_block(_msg_attachments(msg["blocks_json"]))
        if _anchor:
            prompt = f"{_anchor}\n\n{prompt}"

        resume = bool(sess["session_fresh"] == 0)
        claude_sid = sess["claude_session_id"]

        # run_turn/_finish 任何一路抛异常都必须摘除 active（否则泄漏的句柄会让
        # 后续 stop 打在死 turn 上、UI 永远显示运行中）
        try:
            # 原子占位 queued→running：stop/撤回已把排队 turn 置终态或删行时
            # rowcount=0，队列里残留的 tid 在这里被跳过（asyncio.Queue 无法
            # 按值移除，靠状态闸保证「停止排队消息」真的不执行）
            with db_mod.conn() as c:
                cur = c.execute(
                    "UPDATE turns SET status='running', started_at=?, claude_session_id=?,"
                    " resume=?, engine=? WHERE id=? AND status='queued'",
                    (iso(), claude_sid, int(resume), spec.name, tid))
                if cur.rowcount != 1:
                    log.info("turn %s 已停止/撤回（非 queued），跳过执行", tid)
                    return
            at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(),
                            started_at=time.time())
            self.active[tid] = at
            self.publish(sid, "turn_started", {"turn_id": tid, "mode": turn["mode"],
                                               "resume": resume}, tid)

            def _on_spawned(pid: int, out_log: Path, err_log: Path) -> None:
                # daemon 中途死也能凭 turns 行收养：pid + 输出日志路径必落
                try:
                    with db_mod.conn() as c:
                        db_mod.update_turn(c, tid, pid=pid, log_out=str(out_log),
                                           log_err=str(err_log))
                except Exception:
                    log.exception("turn %s 落 pid/log 失败", tid)

            async def on_event(ev: dict) -> None:
                await self._consume(sid, tid, at, ev)

            # 恒注入会话 env（spawn 进程级）：共享工作区的 settings.json 不能
            # 再背 per-session 的 WORKDADDY_SESSION_ID（那是项目的），claude 引擎
            # 也改双通道——子进程 env 与 settings env 同值不漂移
            extra_env = ws_mod.session_env(ws_mod.ws_of(sid), sid)
            call = TurnCall(
                prompt=prompt, cwd=Path(sess["workspace"]), session_id=claude_sid,
                resume=resume, effort=prof.effort, model=prof.model,
                timeout_s=prof.timeout_s, stall_timeout_s=prof.stall_timeout_s,
                max_turns=prof.max_turns,
                engine=spec.name, rotate_input_tokens=prof.rotate_input_tokens,
                disallowed_tools=list(prof.disallowed_tools),
                turn_id=tid, sid=sid, env_extra=extra_env, on_event=on_event,
                on_spawned=_on_spawned)
            res = await run_turn(call, at.stop)
            await self._finish(sid, tid, at, sess, res, anchor=_anchor)
        finally:
            self.active.pop(tid, None)

    # ------------------------------------------------------------ 事件消费
    _DELTA_FLUSH_S = 0.5       # 合流冲刷间隔（秒）
    _DELTA_FLUSH_CHARS = 320   # 合流冲刷体积（字符）

    def _push_delta(self, sid: str, tid: int, at: ActiveTurn,
                    kind: str, chunk: str) -> None:
        """stream_event 增量入缓冲；到时间/体积阈值冲刷成一条 delta 事件。"""
        at.streamed_kinds.add(kind)
        at.delta_buf[kind] = at.delta_buf.get(kind, "") + chunk
        now = time.time()
        if len(at.delta_buf[kind]) >= self._DELTA_FLUSH_CHARS \
                or now - at.delta_last_flush.get(kind, 0.0) >= self._DELTA_FLUSH_S:
            self._flush_delta(sid, tid, at, kind, now=now)

    def _flush_delta(self, sid: str, tid: int, at: ActiveTurn,
                     kind: str, now: float | None = None) -> None:
        buf = at.delta_buf.get(kind, "")
        if not buf:
            return
        at.delta_buf[kind] = ""
        at.delta_last_flush[kind] = now if now is not None else time.time()
        # 纯直播通道：数据层（at.texts/at.blocks → 持久化）始终由整块 assistant
        # 事件入账——delta 分块若入 at.texts 会在 "\n\n".join 时插入假空行
        self._emit(at, sid, "thinking" if kind == "think" else "text",
                   {"turn_id": tid, "text": buf, "delta": True})
    async def _consume(self, sid: str, tid: int, at: ActiveTurn, ev: dict) -> None:
        t = ev.get("type")
        if t == "stream_event":
            # 逐 delta 直播（--verbose 下 claude CLI / hahaness 同形）：缓冲合流
            # 后冲刷成带 delta 标记的 thinking/text 事件，前端追加到末条同
            # 类项——生成中也能看到思考滚屏，而不是整轮沉默到块完成。
            # **类切换即块边界**：GLM interleaved-thinking 会思考/正文交错，
            # 先冲净另一类缓冲再入本类——否则 thinking 尾巴压在缓冲里、text
            # 先冲出去，后到的尾巴插到正文后面，前端严格按序渲染就乱了
            ev2 = ev.get("event") or {}
            if ev2.get("type") == "content_block_delta":
                d = ev2.get("delta") or {}
                dtype = d.get("type")
                if dtype == "thinking_delta" and d.get("thinking"):
                    if at.delta_buf.get("text"):
                        self._flush_delta(sid, tid, at, "text")
                    self._push_delta(sid, tid, at, "think", d["thinking"])
                elif dtype == "text_delta" and d.get("text"):
                    if at.delta_buf.get("think"):
                        self._flush_delta(sid, tid, at, "think")
                    self._push_delta(sid, tid, at, "text", d["text"])
            return
        # 其他任何事件到达前先冲净残留缓冲（块边界/工具结果/终态不吞尾巴）
        if at.delta_buf.get("think") or at.delta_buf.get("text"):
            self._flush_delta(sid, tid, at, "think")
            self._flush_delta(sid, tid, at, "text")
        if t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and (b.get("text") or "").strip():
                    # 数据层恒走整块；已逐 delta 直播过则只跳过重复直播。
                    # text 段同时按时间序进 blocks（与 thinking/tool 穿插）——
                    # 历史回放按 blocks 还原真实顺序，content 仍为纯文本拼接
                    at.texts.append(b["text"])
                    at.blocks.append({"type": "text", "text": b["text"]})
                    if "text" not in at.streamed_kinds:
                        self._emit(at, sid, "text",
                                   {"turn_id": tid, "text": b["text"]})
                elif bt == "thinking" and (b.get("thinking") or "").strip():
                    think = b["thinking"][:_THINK_CAP]
                    at.blocks.append({"type": "thinking", "text": think})
                    if "think" not in at.streamed_kinds:
                        self._emit(at, sid, "thinking",
                                   {"turn_id": tid, "text": think})
                elif bt == "tool_use":
                    name = b.get("name", "?")
                    inp = b.get("input") or {}
                    brief = _tool_brief(name, inp)
                    detail = _clip_input(inp)
                    at.blocks.append({"type": "tool", "id": b.get("id"), "name": name,
                                      "brief": brief, "input": detail})
                    if b.get("id"):
                        at.tool_names[b["id"]] = name
                    if name == "TaskCreate":
                        at.todos.append({"subject": inp.get("subject") or "任务",
                                         "status": "pending"})
                        self._emit(at, sid, "todos", {"turn_id": tid, "todos": at.todos})
                    elif name == "TaskUpdate":
                        try:
                            tno = int(inp.get("taskId"))
                            if 1 <= tno <= len(at.todos) and inp.get("status"):
                                at.todos[tno - 1]["status"] = inp["status"]
                                self._emit(at, sid, "todos", {"turn_id": tid, "todos": at.todos})
                        except (TypeError, ValueError):
                            pass
                    elif name == "TodoWrite" and isinstance(inp.get("todos"), list):
                        # hahaness 的全量覆盖写语义（transcript.latest_todos 同构兜底）
                        at.todos = [{"subject": t.get("content") or "任务",
                                     "status": t.get("status", "pending")}
                                    for t in inp["todos"] if isinstance(t, dict)]
                        self._emit(at, sid, "todos", {"turn_id": tid, "todos": at.todos})
                    self.publish(sid, "tool_use",
                                 {"turn_id": tid, "id": b.get("id"), "name": name,
                                  "brief": brief, "input": detail}, tid)
        elif t == "user":
            for b in (ev.get("message") or {}).get("content") or []:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                result = _content_text(b.get("content"))[:_RESULT_CAP]
                brief = result.strip().replace("\n", " ")[:110]
                is_err = bool(b.get("is_error"))
                tuid = b.get("tool_use_id")
                name = at.tool_names.get(tuid, "?")
                self._emit(at, sid, "tool_result",
                           {"turn_id": tid, "id": tuid, "name": name,
                            "is_error": is_err, "brief": brief, "result": result})
                # 结果回填到对应 tool 块（历史回放可见完整输入+输出）
                hit = False
                if tuid:
                    for blk in reversed(at.blocks):
                        if blk.get("type") == "tool" and blk.get("id") == tuid:
                            blk["result"] = result
                            blk["is_error"] = is_err
                            hit = True
                            break
                if not hit and is_err and brief:
                    at.blocks.append({"type": "tool", "id": tuid, "name": name,
                                      "brief": brief, "is_error": True, "result": result})
                # 文件类工具完成后推工作区新文件（右侧面板实时刷新）
                if name in _FILE_TOOLS:
                    await self._publish_files(sid, tid, at)
        elif t == "steer":
            # hahaness 插话消费回执：该条已注入运行中上下文，从待回队列摘除
            text = (ev.get("text") or "").strip()
            for s in at.steers:
                if not s.get("consumed") and s.get("text") == text:
                    s["consumed"] = True
                    break

    @staticmethod
    def _collect_recent(ws: Path, since: float) -> list[dict]:
        """workspace 里 mtime ≥ since 的文件（给 files 面板）。同步纯函数：
        供 asyncio.to_thread 调用——rglob 在事件循环里同步跑曾把 API 卡死数分钟
        （agent 工作区里的 chrome 登录态 profile 有几十万小文件）。"""
        files: list[dict] = []
        walked = 0
        try:
            for p in ws.rglob("*"):
                walked += 1
                if walked > 200_000:      # 病态大目录止损
                    break
                rel = p.relative_to(ws).parts
                # 隐藏目录之外同样跳过重目录：node_modules 依赖树、chrome profile
                # （既拖慢遍历也不该出现在用户文件面板里）
                if any(s.startswith(".") or s == "node_modules" or s.startswith("chrome")
                       for s in rel):
                    continue
                try:
                    if p.is_file() and p.stat().st_mtime >= since:
                        files.append({"path": str(p.relative_to(ws)),
                                      "kb": round(p.stat().st_size / 1024, 1)})
                except OSError:
                    continue
        except OSError:
            return files
        return files

    async def _publish_files(self, sid: str, tid: int, at: ActiveTurn) -> None:
        ws = ws_mod.ws_of(sid)
        files = await asyncio.to_thread(self._collect_recent, ws, at.started_at - 1)
        files = sorted(files, key=lambda x: x["path"])[:50]
        if files:
            self._emit(at, sid, "files", {"turn_id": tid, "files": files})

    # ------------------------------------------------------------ 收尾
    async def _finish(self, sid: str, tid: int, at: ActiveTurn, sess, res, anchor: str) -> None:
        status = "done"
        if res.stopped:
            status = "stopped"
        elif not res.ok:
            status = "error"

        # ---- resume 快速失败：CLI 秒拒（会话不存在）→ 计数；连败 ≥2 轮换新会话重试
        was_resume = bool(sess["session_fresh"] == 0)
        engine_name = sess["engine"] or "claude"
        spec = engines_mod.resolve(engine_name)
        if status == "error" and _is_fast_fail(res):
            if was_resume:
                fails = (sess["resume_failures"] or 0) + 1
                if fails < 2:
                    log.warning("turn %s resume 秒拒（%s），原样重试", tid, res.error)
                    with db_mod.conn() as c:
                        db_mod.update_session(c, sid, resume_failures=fails)
                        db_mod.update_turn(c, tid, status="queued", error=res.error)
                    await asyncio.sleep(1)
                    await self._run_turn(sid, tid)
                    return
                log.warning("turn %s resume 连败 %d 次，轮换新 %s 会话重试",
                            tid, fails, spec.name)
                with db_mod.conn() as c:
                    db_mod.update_session(c, sid, claude_session_id=spec.new_session_id(),
                                          session_fresh=1, resume_failures=0)
                    db_mod.update_turn(c, tid, status="queued", error=res.error)
                self.publish(sid, "session_rotated",
                             {"turn_id": tid, "reason": "resume_rejected"}, tid)
                await asyncio.sleep(1)
                await self._run_turn(
                    sid, tid,
                    _anchor=f"（上一 {spec.name} 会话已轮换：请先读 PROGRESS.md、state.json 与 notes/，"
                            "凭磁盘状态无损续作，不要重做已完成步骤。）")
                return
            # fresh（--session-id）也秒拒。特例「already in use」：transcript 已存在
            # （上轮 interrupted/被杀，未及翻 resume 位）→ 原 id 转 resume 重试，上下文
            # 无损；若 resume 仍秒拒则走上面 was_resume 分支的计数/轮换，天然有界。
            # 其余秒拒（CLI/环境问题）不轮换直接报错
            if res.error and spec.is_in_use_error(res.error):
                log.warning("turn %s fresh 秒拒但 transcript 已存在，转 resume 重试", tid)
                with db_mod.conn() as c:
                    db_mod.update_session(c, sid, session_fresh=0)
                    db_mod.update_turn(c, tid, status="queued", error=res.error)
                await asyncio.sleep(1)
                await self._run_turn(sid, tid)
                return
            log.error("turn %s fresh 会话秒拒：%s", tid, res.error)
        # fast-fail（秒拒）时引擎会话从未真正建立：fresh 保持原值，不刷成 0
        fast_failed = _is_fast_fail(res)

        # ---- assistant 消息落库（幂等护栏：_finish 中途死 + 收养重放会双跑，
        # 已有该 turn 的 assistant 消息则跳过——重放路径 at 已重建全部内容）
        with db_mod.conn() as c:
            exists = c.execute(
                "SELECT id FROM messages WHERE session_id=? AND turn_id=? AND role='assistant'",
                (sid, tid)).fetchone()
        content = "\n\n".join(t for t in at.texts if t.strip())
        # 报错 turn 兜底回显：引擎零输出即死（429 秒拒/引擎内部错）时若无占位
        # 消息，消息流会停在 user 消息上，UI 重拉与巡检 agent 都看不到失败原因
        if status == "error":
            detail = (res.error or "").strip()
            err = (f"⚠️ **turn 报错退出**（exit={res.exit_code}）"
                   + (f"：\n\n```\n{detail[:1500]}\n```" if detail else "：无错误详情。"))
            content = f"{content}\n\n---\n{err}" if content else err
        if (content or at.blocks) and not exists:
            with db_mod.conn() as c:
                db_mod.add_message(c, session_id=sid, turn_id=tid, role="assistant",
                                   content=content or "（无文本输出）",
                                   blocks_json=json.dumps(at.blocks, ensure_ascii=False))

        # ---- turn / session 记账
        usage = res.usage or {}
        with db_mod.conn() as c:
            db_mod.update_turn(
                c, tid, status=status, exit_code=res.exit_code,
                duration_s=round(res.duration_s, 1), cost_usd=res.cost_usd,
                usage_json=json.dumps(usage, ensure_ascii=False) if usage else None,
                models_json=json.dumps(res.model_usage, ensure_ascii=False) if res.model_usage else None,
                num_turns=res.num_turns, error=res.error,
                log_out=res.log_out, log_err=res.log_err, finished_at=iso())
            # session 累计（四类数值 token 键；非数值键不 merge）
            try:
                prev = json.loads(sess["usage_json"]) if sess["usage_json"] else {}
            except (TypeError, json.JSONDecodeError):
                prev = {}
            merged = {k: (prev.get(k) or 0) + (usage.get(k) or 0)
                      for k in db_mod.NUMERIC_USAGE_KEYS}
            db_mod.update_session(
                c, sid, cost_usd=(sess["cost_usd"] or 0) + (res.cost_usd or 0),
                usage_json=json.dumps(merged, ensure_ascii=False), resume_failures=0,
                **({} if fast_failed else {"session_fresh": 0,
                                           "claude_session_id": res.session_id or sess["claude_session_id"]}))

        # ---- 轮换启发式（token 基；订阅计量可能为 0）
        rotate_reason = ""
        if status == "done":
            from . import profile as profile_mod
            prof = profile_mod.get(sess["profile"])
            thr = prof.rotate_input_tokens
            if thr and (usage.get("input_tokens") or 0) > thr:
                with db_mod.conn() as c:
                    db_mod.update_session(c, sid,
                                          claude_session_id=spec.new_session_id(),
                                          session_fresh=1)
                rotate_reason = "context_inflation"

        # ---- SSE 终态事件
        payload = {"turn_id": tid, "status": status,
                   "duration_s": round(res.duration_s, 1),
                   "cost_usd": res.cost_usd, "usage": usage,
                   "num_turns": res.num_turns}
        if status == "stopped":
            self._emit(at, sid, "turn_stopped", payload)
        elif status == "error":
            payload["error"] = res.error
            self._emit(at, sid, "turn_error", payload)
        else:
            self._spotcheck_claims(sid, tid, res)
            self._drain_hook_audit(sid, tid)
            self._emit(at, sid, "turn_done", payload)
        if rotate_reason:
            self._emit(at, sid, "session_rotated", {"turn_id": tid, "reason": rotate_reason})

        # ---- 未消费插话回队列：steer 落在最后一轮 LLM 调用之后（来不及注入）
        # 的，按用户原话转为正常排队消息——插话不丢话，等同「更快进队列」
        if status == "done":
            missed = [s["text"] for s in at.steers if not s.get("consumed")]
            for text in missed:
                log.info("turn %s 插话未消费，回队列: %s", tid, text[:60])
                try:
                    await self.submit(sid, f"（运行中插话，转发处理）{text}")
                except Exception:
                    log.exception("插话回队列失败 sid=%s", sid)

        # ---- 运维通知（fire-and-forget，永不拖垮主流程）
        try:
            from . import notify
            if status == "error":
                notify.fire(f"❌ turn 报错 · {sess['title'][:40]}",
                            f"{(res.error or '')[:200]}", event="on_error")
            elif status == "done":
                notify.fire(f"✅ turn 完成 · {sess['title'][:40]}",
                            f"{round(res.duration_s, 1)}s · ${(res.cost_usd or 0):.3f}",
                            event="on_turn_done")
        except Exception:
            log.exception("通知钩子异常")

        # ---- 产物扫描 + 事件窗口收缩
        try:
            from . import artifacts as art
            art.scan_session(sid)
        except Exception:
            log.exception("产物扫描失败 sid=%s", sid)
        await self._publish_files(sid, tid, at)
        with db_mod.conn() as c:
            removed = db_mod.prune_events(c, sid, CONFIG.run.events_retain_days)
        if removed:
            log.info("事件清理 sid=%s 删除 %d 行（保留 %.1f 天）",
                     sid, removed, CONFIG.run.events_retain_days)

    # ------------------------------------------------------------ 生命周期（daemon 独立重启）
    def requeue(self, sid: str, tid: int) -> None:
        """重启后把遗留 queued turn 重新入队（turn 行已存在，不重建不发事件）。"""
        q = self._queues.get(sid)
        if q is None:
            q = asyncio.Queue()
            self._queues[sid] = q
        q.put_nowait(tid)
        self._ensure_worker(sid)

    def recover_after_restart(self) -> dict:
        """Docker 式重启恢复：幸存 worker 收养续跑，自然跑完的补记账。

        分派（按 session 分组，每 session 只收养最老 running——历史双跑脏
        数据修复，次老直接 interrupted）：
        - running + pid 活（environ 强匹配）→ _adopt_turn 收养
        - running + pid 死 + 输出日志有 result 行 → 重放补记账（零丢失）
        - running + pid 死 + 无 result → interrupted（旧行为，可 --resume 续作）
        - queued → requeue（闸保证不与收养并发）
        返回 {"adopted", "finished", "interrupted", "requeued", "claimed_pids"}。
        """
        out = {"adopted": [], "finished": [], "interrupted": 0, "requeued": [],
               "claimed_pids": set()}
        with db_mod.conn() as c:
            turns = [dict(t) for t in db_mod.active_turns(c)]
        by_sid: dict[str, list[dict]] = {}
        for t in turns:
            by_sid.setdefault(t["session_id"], []).append(t)
        for sid, ts in by_sid.items():
            runnings = sorted((t for t in ts if t["status"] == "running"),
                              key=lambda t: t["id"])
            for i, t in enumerate(runnings):
                if i > 0:   # 同 session 多 running 是历史脏数据：只收养最老的
                    with db_mod.conn() as c:
                        db_mod.update_turn(c, t["id"], status="interrupted",
                                           error="server_restart_dup", finished_at=iso())
                    out["interrupted"] += 1
                    continue
                pid = t["pid"]
                log_out = t["log_out"]
                if pid and log_out and Path(log_out).exists() \
                        and pid_alive_for_turn(pid, t["id"]):
                    out["adopted"].append(t["id"])
                    out["claimed_pids"].add(pid)
                    asyncio.create_task(self._adopt_turn(sid, t["id"]))
                else:
                    # pid 已死：日志里有 result 行 = 停机期间自然跑完 → 补记账
                    if self._has_result_line(log_out):
                        out["finished"].append(t["id"])
                        asyncio.create_task(self._replay_and_finish_async(sid, t))
                    else:
                        with db_mod.conn() as c:
                            db_mod.update_turn(c, t["id"], status="interrupted",
                                               error="server_restart", finished_at=iso())
                        out["interrupted"] += 1
            for t in ts:
                if t["status"] == "queued":
                    self.requeue(sid, t["id"])
                    out["requeued"].append(t["id"])
        if out["adopted"] or out["finished"]:
            log.info("重启恢复：收养 %s、补记账 %s、interrupted %d、requeue %s",
                     out["adopted"], out["finished"], out["interrupted"], out["requeued"])
        elif out["interrupted"] or out["requeued"]:
            log.info("重启恢复：interrupted %d、requeue %s",
                     out["interrupted"], out["requeued"])
        return out

    @staticmethod
    def _has_result_line(path: str | None) -> bool:
        """输出日志尾部是否有 result 行（result 是最后一个事件，尾扫可靠）。"""
        if not path or not Path(path).exists():
            return False
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 262144))
                tail_txt = f.read().decode("utf-8", errors="replace")
        except OSError:
            return False
        return "\"type\": \"result\"" in tail_txt or "\"type\":\"result\"" in tail_txt

    async def _replay_and_finish_async(self, sid: str, turn: dict) -> bool:
        """pid 已死但日志完整的 turn：静默重放重建状态 → 正常 _finish 补记账
        （daemon 停机期间自然跑完的，记账零丢失）。"""
        from .claude_runner import LogTail, _Sink
        log_out = turn["log_out"]
        if not log_out or not Path(log_out).exists():
            return False
        spec = engines_mod.resolve(turn["engine"] or "claude")
        tid = turn["id"]
        at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(),
                        started_at=time.time(), quiet=True)
        self.active[tid] = at
        try:
            with db_mod.conn() as c:
                sess = db_mod.get_session(c, sid)
            if sess is None:
                return False

            async def on_event(ev: dict) -> None:
                await self._consume(sid, tid, at, ev)

            sink = _Sink(spec.adapter(), on_event)
            tail = LogTail(Path(log_out))
            try:
                while True:
                    lines = tail.poll()
                    if not lines:
                        break
                    for raw in lines:
                        await sink.feed_raw(raw)
                if not sink.has_result:
                    return False
                started = turn["started_at"]
                t0 = _parse_ts(started)
                res = sink.build_result(
                    ok=True, exit_code=None, duration_s=max(0.0, time.time() - t0),
                    session_id=sink.meta.get("session_id") or "",
                    log_out=log_out, log_err=turn["log_err"] or "")
            finally:
                tail.close()
            await self._finish(sid, tid, at, sess, res, anchor="")
            return True
        finally:
            at.quiet = False
            self.active.pop(tid, None)

    async def _adopt_turn(self, sid: str, tid: int) -> None:
        """收养幸存 turn：闸住同 session 新 turn → 静默重放 → 直播监督 → 正常收尾。"""
        from .claude_runner import AdoptCall, supervise_adopted
        loop = asyncio.get_running_loop()
        gate = loop.create_future()
        self._adopt_gates[sid] = gate
        at: ActiveTurn | None = None
        try:
            with db_mod.conn() as c:
                sess = db_mod.get_session(c, sid)
                turn = db_mod.get_turn(c, tid)
            if sess is None or turn is None or not turn["pid"]:
                raise RuntimeError(f"adopt 上下文缺失 turn={tid}")
            from . import profile as profile_mod
            prof = profile_mod.get(sess["profile"])
            t0 = _parse_ts(turn["started_at"])
            at = ActiveTurn(turn_id=tid, session_id=sid, stop=StopHandle(),
                            started_at=t0, quiet=True)
            self.active[tid] = at

            async def on_event(ev: dict) -> None:
                await self._consume(sid, tid, at, ev)

            def on_replayed() -> None:
                at.quiet = False       # 静默重放完成：后续 tail 增量恢复直播

            acall = AdoptCall(
                turn_id=tid, pid=turn["pid"], engine=turn["engine"] or "claude",
                session_id=turn["claude_session_id"] or "",
                out_log=Path(turn["log_out"]), err_log=Path(turn["log_err"] or "/dev/null"),
                started_at=t0, timeout_s=prof.timeout_s,
                stall_timeout_s=prof.stall_timeout_s,
                on_event=on_event, on_replayed=on_replayed)
            log.info("收养幸存 turn=%s pid=%s engine=%s", tid, turn["pid"], acall.engine)
            res = await supervise_adopted(acall, at.stop)
            await self._finish(sid, tid, at, sess, res, anchor="")
        except Exception as e:
            log.exception("收养 turn=%s 失败", tid)
            try:
                with db_mod.conn() as c:
                    db_mod.update_turn(c, tid, status="error",
                                       error=f"adopt_failed: {e!r}", finished_at=iso())
                if sid:
                    self.publish(sid, "turn_error",
                                 {"turn_id": tid, "status": "error",
                                  "error": f"adopt_failed: {e!r}"}, tid)
            except Exception:  # noqa: BLE001
                pass
        finally:
            if at is not None:
                at.quiet = False
            self.active.pop(tid, None)
            self._adopt_gates.pop(sid, None)
            if not gate.done():
                gate.set_result(None)


ENGINE = Engine()
