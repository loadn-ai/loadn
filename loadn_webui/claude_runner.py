"""无头 agent 引擎调用封装（async stream-json 版）——loadn webui 的执行 runner。

承袭 papergo/claude_runner.py（同源 kaggo）的进程治理骨架，把「--output-format
json + 尾行记账」改造成「stream-json 逐行增量消费 + result 事件记账」：

  1. cwd 即上下文：cwd 是 workspace/<sid>/，自动读会话宪法 CLAUDE.md；
  2. 事件流即结果：stdout 每行一个 JSON 事件（system/assistant/user/result），
     通过 on_event 回调实时消费；result 事件带 total_cost_usd/usage/num_turns；
  3. 双信号判活：输出文件新字节（含高频心跳行）静默超阈值时，兜底查 session
     transcript mtime——两路都静默才杀（不误杀长 bash）；
  4. 会话续连：首次 --session-id <uuid>，后续 --resume <uuid>；
  5. 进程组治理：start_new_session=True，killpg SIGTERM→30s→SIGKILL；
     PID 登记 var/run/live_claude_pids 供孤儿回收。

**daemon 独立重启（Docker 式，2026-09-17）**：子进程 stdout 直写 call_logs
文件（不再走管道——daemon 死后 CLI 无反压继续写）；CancelledError（serve
退场）不杀子进程，留给下一个 daemon `supervise_adopted` 收养：静默重放整个
输出日志重建状态 → tail 增量续读 → /proc 判活轮询 → 进程退出后按 result
行正常记账。配 systemd KillMode=process，serve 重启不再杀正在跑的任务。

测试用 LOADN_CLAUDE_BIN 指向假实现（tests/fake_claude.py）。

引擎方言（argv/事件归一化/能力位）拆到 loadn_webui/engines/（承袭 papergo
providers.py 范式）：claude（默认，行为恒等）| loadn | opencode。本文件
只保留引擎无关的 runner 骨架。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import engines as engines_mod
from .config import PATHS
from .engines.claude import resolve_claude_bin  # noqa: F401 — routes.py 兼容 re-export
from .util import get_logger, iso

log = get_logger(__name__)

# stream-json 单行上限（历史常量，保留供外部引用）：文件 tail 模式下由
# LogTail 的字节缓冲天然承载超长行（base64 图片内联），不再有 StreamReader 限制
_STREAM_LINE_LIMIT = 64 * 1024 * 1024

# 停止请求由 engine 通过 StopHandle 触发，runner 只负责执行
_PID_DIR = PATHS["pid_dir"]


def _register_pid(pid: int) -> None:
    try:
        _PID_DIR.mkdir(parents=True, exist_ok=True)
        (_PID_DIR / str(pid)).touch()
    except OSError:
        pass


def _unregister_pid(pid: int) -> None:
    try:
        (_PID_DIR / str(pid)).unlink(missing_ok=True)
    except OSError:
        pass


def _parent_is_server(pid: int) -> bool:
    """claude 的父进程是否还是活着的 serve 实例（另一实例在管它，不能动）。"""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return False
    return ("loadn" in cmd or "loadn_webui" in cmd) and "serve" in cmd


def pid_alive_for_turn(pid: int, tid: int) -> bool:
    """收养判活：pid 存在、非僵尸、且 environ 带 WORKDADDY_TURN_ID=<tid>
    （spawn 时注入的强匹配，天然防 pid 复用误判）。environ 不可读时回落
    cmdline 引擎标记启发式。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return False
    except (OSError, IndexError):
        return False
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
        except OSError:
            return False
        return any(m in cmd for m in ("fake_claude", "claude", "loadn", "opencode"))
    return (f"LOADN_TURN_ID={tid}\0".encode() in env
            or f"WORKDADDY_TURN_ID={tid}\0".encode() in env)


def reap_orphans(skip: set[int] = frozenset()) -> int:
    """启动时清理遗留的孤儿 claude 进程。

    判据：登记过 + 进程活着 + 父进程不是任何存活的 loadn serve 实例。
    不能只认 ppid==1——本机 systemd --user 等 subreaper 会把孤儿挂到自己名下
    （2026-09-15 实测 ppid=systemd --user，reap 报 0 放行，claude 的 session-id
    锁把新 turn 全部「Session ID already in use」秒拒）。

    skip：已被启动收养（recover_after_restart claimed）的 pid——幸存 worker
    归新实例管，绝不能清。
    """
    n = 0
    if not _PID_DIR.exists():
        return 0
    import os as _os
    for f in _PID_DIR.iterdir():
        if not f.name.isdigit():
            continue
        pid = int(f.name)
        if pid in skip:
            continue
        try:
            _os.kill(pid, 0)
        except ProcessLookupError:
            _unregister_pid(pid)   # 进程已死，清掉登记（老代码误写 _unregister 会炸启动）
            continue
        except PermissionError:
            continue
        try:
            ppid = int(Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            ppid = -1
        if ppid > 0 and _parent_is_server(ppid):
            continue   # bind 失败的竞态实例跑过本函数：claude 归另一个活实例管
        log.warning("清理孤儿 claude 进程 pid=%s ppid=%s", pid, ppid)
        try:
            _os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                _os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        n += 1
        _unregister_pid(pid)
    return n


@dataclass
class TurnCall:
    prompt: str
    cwd: Path
    session_id: str            # 引擎会话 id（claude/loadn=UUID；opencode=ses_…；空=引擎自建）
    resume: bool = False
    effort: str = "high"
    model: str | None = None
    timeout_s: int = 3600      # 硬超时
    stall_timeout_s: int = 1800   # 双信号判死阈值
    max_turns: int | None = None  # 工具调用轮次上限（None=不限制）→ claude/loadn --max-turns
    turn_id: int = 0
    sid: str = ""           # 会话 id（steer 文件按会话分段等）
    engine: str = "claude"     # 执行引擎（engines/ 注册名）
    rotate_input_tokens: int | None = None   # 外层上下文轮换阈值（loadn --no-compact 联动）
    disallowed_tools: list[str] = field(default_factory=list)   # opencode env 注入用
    env_extra: dict = field(default_factory=dict)
    on_event: Callable[[dict], Awaitable[None]] | None = None   # 每个 stream-json 事件
    # spawn 后立刻回调（engine 落 turns.pid/log_out/log_err——daemon 中途死也
    # 能凭这份数据收养）；回调故障不阻断 turn
    on_spawned: Callable[[int, Path, Path], None] | None = None


@dataclass
class TurnProcResult:
    ok: bool
    exit_code: int | None = None
    duration_s: float = 0.0
    session_id: str = ""
    cost_usd: float | None = None
    usage: dict | None = None
    model_usage: dict | None = None   # result.modelUsage：按模型拆分的 token/成本
    num_turns: int | None = None
    subtype: str = ""          # result 事件 subtype：success|error_max_turns|...
    result_text: str = ""
    error: str | None = None
    stopped: bool = False      # 外部请求停止
    log_out: str = ""
    log_err: str = ""


class StopHandle:
    """外部停止把手：engine 持有，runner 轮询。"""

    def __init__(self) -> None:
        self.requested = False

    def stop(self) -> None:
        self.requested = True


def _kill_pg(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    time.sleep(0.2)
    for sig, wait in ((signal.SIGKILL, 5.0),):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)


# ---------------------------------------------------------------- 文件 tail
class LogTail:
    """输出文件 tailer（daemon 独立重启的读端）：单只读 fd + pread 轮询。

    半行跨 poll 缓冲（daemon 死在 CLI 写半行中间，收养后能拼回完整行）；
    任何新字节都算活跃（stall 判定按字节不按完整 JSON 行——大行分多次 write）。
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd = os.open(self.path, os.O_RDONLY)
        self._pos = 0
        self._buf = b""

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass

    def poll(self) -> list[bytes]:
        """非阻塞：返回新增的完整行（不含行尾 \\n）。"""
        chunk = os.pread(self._fd, 1 << 20, self._pos)
        if not chunk:
            return []
        self._pos += len(chunk)
        self._buf += chunk
        out: list[bytes] = []
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            out.append(line)
        return out

    def flush_pending(self) -> bytes:
        """终局残片（进程退出后尾部无换行的内容），取走后清空。"""
        rest, self._buf = self._buf, b""
        return rest


class _Sink:
    """run_turn / supervise_adopted 共用的事件汇：解析 + adapter 归一 + 记账。"""

    def __init__(self, adapter, on_event) -> None:
        self.adapter = adapter
        self.on_event = on_event
        self.meta: dict[str, Any] = {}

    async def feed_raw(self, raw: bytes) -> None:
        """原始日志行 → json → adapter 归一化 → 逐事件分发。"""
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return   # 进程被杀时的半行
        for nev in self.adapter.feed(ev):
            await self.feed_event(nev)

    async def feed_event(self, nev: dict) -> None:
        """已归一化事件的直接分发（finalize 合成产物走这里——**不再过 adapter**，
        二次归一化会吃掉事件：opencode adapter 不认已归一化的 result）。"""
        if nev.get("type") == "result":
            self.meta = {
                "session_id": nev.get("session_id"),
                "total_cost_usd": nev.get("total_cost_usd"),
                "usage": nev.get("usage"),
                "modelUsage": nev.get("modelUsage"),
                "num_turns": nev.get("num_turns"),
                "subtype": nev.get("subtype") or "",
                "result": nev.get("result") or "",
            }
        if self.on_event is not None:
            try:
                await self.on_event(nev)
            except Exception:
                log.exception("on_event 消费异常（忽略）")

    @property
    def has_result(self) -> bool:
        return bool(self.meta)

    def build_result(self, **kw) -> TurnProcResult:
        m = self.meta
        kw.setdefault("session_id", m.get("session_id") or "")
        return TurnProcResult(
            cost_usd=m.get("total_cost_usd"), usage=m.get("usage"),
            model_usage=m.get("modelUsage"), num_turns=m.get("num_turns"),
            subtype=m.get("subtype") or "", result_text=m.get("result") or "",
            **kw)


# W3.3 spawn env 白名单：宿主 env 历史泄漏不再继承（clearenv 等效）。
# M1 过渡期：ANTHROPIC_*/OPENCODE_*（引擎 LLM 凭证）仍在名单（known-gap，
# M2 代理接管后收回）；LC_* 语言族保留（CLI 输出编码稳定）。
_ENV_ALLOW_EXACT = {"PATH", "HOME", "LANG", "TERM", "TZ", "SHELL", "PWD",
                    "TMPDIR", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
                    "NODE_OPTIONS", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
_ENV_ALLOW_PREFIXES = ("LOADN_", "WORKDADDY_", "HAHANESS_", "ANTHROPIC_",
                       "OPENCODE_", "LC_")


def _spawn_env(spec_env: dict, env_extra: dict) -> dict:
    out = {k: v for k, v in os.environ.items()
           if k in _ENV_ALLOW_EXACT or k.startswith(_ENV_ALLOW_PREFIXES)}
    out.update(spec_env)
    out.update(env_extra)
    return out


async def run_turn(call: TurnCall, stop: StopHandle | None = None) -> TurnProcResult:
    """跑一次无头会话 turn：子进程 stdout 直写文件，tail 消费 stream-json。

    CancelledError（serve 退场的优雅停）= 抛弃监视但不杀子进程：pid/log 落在
    turns 行，下一个 daemon 的 supervise_adopted 收养续跑（Docker 式重启）。
    """
    call_id = f"turn{call.turn_id}_{int(time.time() * 1000)}"
    out_log = PATHS["call_logs"] / f"{call_id}.out"
    err_log = PATHS["call_logs"] / f"{call_id}.err"
    PATHS["call_logs"].mkdir(parents=True, exist_ok=True)

    spec = engines_mod.resolve(call.engine)
    cmd, spec_env = spec.build_argv(call)
    env = _spawn_env(
        {"LOADN_TURN_ID": str(call.turn_id),
         "WORKDADDY_TURN_ID": str(call.turn_id), **spec_env},
        call.env_extra)
    # P5 凭证收回：LLM 引擎只知虚拟网关域+dummy token（真凭证在代理控制域）
    from .config import CONFIG as _CFG
    if _CFG.security.egress_mode in ("warn", "enforce") \
            and _CFG.security.egress_proxy_port \
            and spec.name in ("loadn", "hahaness", "claude"):  # hahaness=alias
        from .egress_proxy import GW_HOST
        env["ANTHROPIC_BASE_URL"] = f"http://{GW_HOST}"
        env.setdefault("ANTHROPIC_AUTH_TOKEN", "dummy-controlled-by-egress-gw")

    # W5.1：引擎流量走出口代理（白名单+审计+面板数据源；env 通道=可回退）
    from .config import CONFIG as _C
    if _C.security.egress_mode in ("warn", "enforce") \
            and _C.security.egress_proxy_port:
        proxy = f"http://127.0.0.1:{_C.security.egress_proxy_port}"
        env.setdefault("https_proxy", proxy)
        env.setdefault("HTTPS_PROXY", proxy)
        env.setdefault("http_proxy", proxy)
        env.setdefault("HTTP_PROXY", proxy)
        env["no_proxy"] = env["NO_PROXY"] = "127.0.0.1,localhost"

    # W2-a 执行沙箱（security.sandbox=bwrap 时包裹；同路径 bind 保协议通道）
    from . import sandbox as sandbox_mod
    from . import workspace as _ws_mod
    from .audit import audit as _audit
    cmd, sbx_mode = sandbox_mod.wrap_engine(
        cmd, env, engine=spec.name, sid=call.session_id, cwd=Path(call.cwd),
        project_root=_ws_mod.project_root_of(call.sid))
    if sbx_mode != "direct":
        _audit("sandbox_violation" if sbx_mode == "direct-fallback" else "snapshot",
               {"mode": sbx_mode, "engine": spec.name, "sid": call.sid},
               sid=call.sid, turn_id=call.turn_id)
    log.info("%s turn 开始 turn=%s resume=%s cwd=%s", spec.name, call.turn_id,
             call.resume, call.cwd)

    t0 = time.time()
    cancelled = False
    proc = None
    tail: LogTail | None = None
    sink = _Sink(spec.adapter(), call.on_event)

    try:
        # stdout 直写文件（不经管道）：daemon 死后 CLI 无反压继续写——收养的
        # 根基。父进程的 fd 副本 spawn 完立刻关（子进程持有 dup）。
        fo = out_log.open("wb")
        fe = err_log.open("wb")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(call.cwd), stdout=fo, stderr=fe,
                start_new_session=True, env=env)
        except OSError as e:
            return sink.build_result(
                ok=False, error=f"spawn_failed: {e}", duration_s=0.0,
                log_out=str(out_log), log_err=str(err_log))
        finally:
            fo.close()
            fe.close()
        _register_pid(proc.pid)
        if call.on_spawned is not None:
            try:
                call.on_spawned(proc.pid, out_log, err_log)
            except Exception:
                log.exception("on_spawned 回调异常（忽略）")

        tail = LogTail(out_log)
        last_line_ts = time.time()
        tick = 0
        err_msg: str | None = None
        stopped = False
        while proc.returncode is None:
            await asyncio.sleep(0.1)
            tick += 1
            lines = tail.poll()
            if lines:
                last_line_ts = time.time()
                for raw in lines:
                    await sink.feed_raw(raw)
            if tick % 5:                      # 控制检查 0.5s 一拍
                continue
            elapsed = time.time() - t0
            if stop is not None and stop.requested:
                stopped = True
                err_msg = "stopped_by_user"
                break
            if call.timeout_s and elapsed > call.timeout_s:
                err_msg = f"hard_timeout {elapsed:.0f}s > {call.timeout_s}s"
                break
            silence = time.time() - last_line_ts
            if silence > call.stall_timeout_s:
                ts_age = spec.transcript_age(call.session_id)
                if ts_age is None or ts_age > call.stall_timeout_s:
                    err_msg = (f"stalled {silence:.0f}s (transcript_age="
                               f"{'na' if ts_age is None else f'{ts_age:.0f}s'})")
                    break
        if err_msg is not None and proc.returncode is None:
            _kill_pg(proc.pid)
        # 终局排空：进程退出后文件即完整（同步写）；残片按半行语义尝试解析
        for _ in range(20):
            lines = tail.poll()
            if not lines:
                break
            for raw in lines:
                await sink.feed_raw(raw)
        await sink.feed_raw(tail.flush_pending())
        await proc.wait()
        _unregister_pid(proc.pid)
    except asyncio.CancelledError:
        # serve 退场（uvicorn _cancel_all_tasks）：不杀、不等、不关 transport
        # （transport.close() 对未退子进程会 kill）。tail fd 关掉即可——
        # pid 文件与 turns.pid/log 留给下一个 daemon 收养。
        cancelled = True
        raise
    finally:
        if not cancelled:
            if proc is not None and proc.returncode is None:
                _kill_pg(proc.pid)   # 防御：异常路径漏杀
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            if proc is not None:
                _unregister_pid(proc.pid)
        if tail is not None:
            tail.close()

    duration = time.time() - t0
    rc = proc.returncode
    # 流已结束：适配器合成收尾事件（opencode 冲出 pending part + 合成 result；
    # 恒等引擎为空）。**必须用 sink 里的有状态 adapter**——fresh adapter 无从冲出
    for nev in sink.adapter.finalize(rc, duration):
        await sink.feed_event(nev)
    err_msg_final: str | None = None
    if stopped:
        pass                              # stopped 是正常语义，不算 error
    elif rc != 0:
        err_tail = ""
        try:
            err_tail = err_log.read_text(errors="replace").strip()[-300:]
        except OSError:
            pass
        err_msg_final = f"exit={rc} {err_tail}".strip()

    ok = rc == 0 and not err_msg_final
    res = sink.build_result(
        ok=ok, exit_code=rc, duration_s=duration,
        session_id=sink.meta.get("session_id") or call.session_id,
        error=err_msg_final, stopped=stopped,
        log_out=str(out_log), log_err=str(err_log))
    log.info("%s turn 结束 turn=%s ok=%s rc=%s dur=%.0fs cost=%s subtype=%s err=%s",
             spec.name, call.turn_id, ok, rc, duration, res.cost_usd,
             res.subtype, err_msg_final)
    return res


# ---------------------------------------------------------------- 收养
@dataclass
class AdoptCall:
    """收养一个 daemon 停机幸存的 turn 所需的全部现场。"""
    turn_id: int
    pid: int
    engine: str
    session_id: str              # 引擎会话 id（transcript_age 兜底用）
    out_log: Path
    err_log: Path
    started_at: float            # wall-clock（turns.started_at，timeout 剩余额度基）
    timeout_s: int = 3600
    stall_timeout_s: int = 1800
    on_event: Callable[[dict], Awaitable[None]] | None = None
    on_replayed: Callable[[], None] | None = None   # 静默重放完成（切直播）


async def supervise_adopted(call: AdoptCall, stop: StopHandle) -> TurnProcResult:
    """收养监督：静默重放输出日志 → tail 直播 → 判活轮询 → rc 不可知收尾。

    rc 拿不到（非子进程不能 waitpid）——政策：stopped > result 行（ok 沿用
    subtype 语义）> opencode 合成（err_log 空且 adapter 无 error 按 rc=0）>
    error。timeout 按 started_at 的 wall-clock 剩余额度（不因重启豁免）；
    stall 时钟在收养时重置（重放 drain 完成前不查）。
    """
    spec = engines_mod.resolve(call.engine)
    tail = LogTail(call.out_log)
    sink = _Sink(spec.adapter(), call.on_event)
    t0 = call.started_at
    try:
        # ---- 阶段1 静默重放至 EOF（MB 级日志每 500 行让出事件循环）
        n = 0
        while True:
            lines = tail.poll()
            if not lines:
                break
            for raw in lines:
                await sink.feed_raw(raw)
            n += len(lines)
            if n >= 500:
                n = 0
                await asyncio.sleep(0)
        if call.on_replayed is not None:
            try:
                call.on_replayed()
            except Exception:  # noqa: BLE001
                pass

        # ---- 阶段2 监督（0.5s 拍，与 run_turn 同构）
        last_line_ts = time.time()          # stall 时钟重置
        err_msg: str | None = None
        stopped = False
        while pid_alive_for_turn(call.pid, call.turn_id):
            await asyncio.sleep(0.5)
            lines = tail.poll()
            if lines:
                last_line_ts = time.time()
                for raw in lines:
                    await sink.feed_raw(raw)
            if stop.requested:
                stopped = True
                err_msg = None
                _kill_pg(call.pid)
                break
            elapsed = time.time() - t0
            if call.timeout_s and elapsed > call.timeout_s:
                err_msg = f"hard_timeout {elapsed:.0f}s > {call.timeout_s}s"
                _kill_pg(call.pid)
                break
            silence = time.time() - last_line_ts
            if silence > call.stall_timeout_s:
                ts_age = spec.transcript_age(call.session_id)
                if ts_age is None or ts_age > call.stall_timeout_s:
                    err_msg = (f"stalled {silence:.0f}s (transcript_age="
                               f"{'na' if ts_age is None else f'{ts_age:.0f}s'})")
                    _kill_pg(call.pid)
                    break
        # stopped 后等 pid 消失（killpg 宽限）
        if stopped:
            deadline = time.time() + 10
            while time.time() < deadline and pid_alive_for_turn(call.pid, call.turn_id):
                await asyncio.sleep(0.2)

        # ---- 阶段3 终局排空 + rc 不可知政策
        for _ in range(20):
            lines = tail.poll()
            if not lines:
                break
            for raw in lines:
                await sink.feed_raw(raw)
        await sink.feed_raw(tail.flush_pending())
        duration = time.time() - t0
        err_tail = ""
        try:
            err_tail = call.err_log.read_text(errors="replace").strip()[-300:]
        except OSError:
            pass

        if stopped:
            return sink.build_result(ok=False, exit_code=None, duration_s=duration,
                                     session_id=call.session_id, stopped=True,
                                     log_out=str(call.out_log), log_err=str(call.err_log))
        if sink.has_result:
            # result 行是权威（subtype 语义与在线路径一致：error_* + rc=0 也记 done）
            return sink.build_result(ok=err_msg is None, exit_code=None,
                                     duration_s=duration,
                                     session_id=sink.meta.get("session_id") or call.session_id,
                                     error=err_msg,
                                     log_out=str(call.out_log), log_err=str(call.err_log))
        # 无 result 行：opencode 无 rc 可言——err_log 空且 adapter 无 error 按
        # rc=0 合成（用 sink 的 adapter——带着重放状态，fresh adapter 无从合成）；
        # 否则如实 error（claude/loadn 无 result 行 = 异常死）
        if err_msg is None and not err_tail:
            for nev in sink.adapter.finalize(0, duration):
                await sink.feed_event(nev)
            if sink.has_result:
                return sink.build_result(ok=True, exit_code=None, duration_s=duration,
                                         session_id=call.session_id,
                                         log_out=str(call.out_log), log_err=str(call.err_log))
        reason = err_msg or (f"exit=unknown {err_tail}".strip() if err_tail
                             else "process vanished (no result event)")
        return sink.build_result(ok=False, exit_code=None, duration_s=duration,
                                 session_id=call.session_id, error=reason,
                                 log_out=str(call.out_log), log_err=str(call.err_log))
    finally:
        tail.close()
        _unregister_pid(call.pid)


def forensic_snapshot(tag: str, extra: str = "") -> None:
    """外部击杀取证（承袭 kaggo _forensic_snapshot）：进程树现场落盘。"""
    import subprocess as sp
    d = PATHS["logs"] / "kill_forensics"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{int(time.time())}_{tag}.txt"
    try:
        tree = sp.run(["ps", "-eo", "pid,ppid,stat,etime,cmd"], capture_output=True,
                      text=True, timeout=10).stdout
        p.write_text(f"# {iso()} {tag}\n{extra}\n\n{tree}")
    except Exception as e:  # noqa: BLE001
        log.warning("取证快照失败：%s", e)
