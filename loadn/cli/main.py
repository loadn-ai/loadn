"""loadn CLI 入口（argv 契约与 claude CLI 同构——宿主 harness 可直接 spawn）。

  loadn [-p/--print] [--output-format {text,stream-json,json}] [--verbose]
           [--model M] [--effort E] [--variant E] [--max-turns N]
           (--session-id UUID | --resume UUID) [--fork]
           [--dangerously-skip-permissions] [--permission-mode M] [--yolo]
           [--append-system-prompt TEXT] [--no-compact] [--version] [PROMPT]

约定：PROMPT 恒为末位 positional（无参时读管道 stdin）；session flag 恒
--session-id/--resume；同文案错误 "Session ID already in use"（宿主的
fresh→resume 翻转分支复用）。exit code：0=success；1=error_* 终态。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from loadn import __version__
from loadn.constants import HEARTBEAT_INTERVAL_S
from loadn.util import get_logger

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="loadn", add_help=True)
    ap.add_argument("-p", "--print", dest="print_mode", action="store_true",
                    help="无头单轮模式（非交互）")
    ap.add_argument("--output-format", default="text",
                    choices=["text", "stream-json", "json"])
    ap.add_argument("--verbose", action="store_true",
                    help="stream-json 下逐 delta 外发 stream_event（与 claude CLI 同语义）")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", dest="effort", default=None)
    ap.add_argument("--variant", dest="effort", default=None)   # 别名
    ap.add_argument("--max-turns", type=int, default=None)
    ap.add_argument("--session-id", dest="session_id", default=None,
                    help="以指定 id 开新会话（transcript 已存在则报 already in use）")
    ap.add_argument("--resume", dest="resume_id", default=None, help="续会话")
    ap.add_argument("--fork", action="store_true", help="配合 --resume：复制到新会话 id")
    ap.add_argument("--dangerously-skip-permissions", dest="yolo",
                    action="store_true")
    ap.add_argument("--yolo", dest="yolo", action="store_true",
                    help="--dangerously-skip-permissions 别名")
    ap.add_argument("--permission-mode", default=None,
                    choices=["default", "acceptEdits", "plan", "bypassPermissions"])
    ap.add_argument("--append-system-prompt", default=None)
    ap.add_argument("--no-compact", action="store_true", help="禁用内置上下文压缩")
    ap.add_argument("--grind", action="store_true",
                    help="死磕模式：纯文本收工前过完工自检（产物核对+预算告知），"
                         "未过自动续战；配 --budget-minutes 告知剩余时间")
    ap.add_argument("--budget-minutes", type=float, default=None,
                    help="任务总预算（分钟），死磕模式用于剩余时间告知")
    ap.add_argument("--no-plan", action="store_true",
                    help="禁用并行拆分调度（接到任务不评估拆分，直跑）")
    ap.add_argument("--protocol", default="v1", choices=["v1", "v2"],
                    help="事件流协议版本（P2-3：v2=增量事件+双发桥；默认 v1 旧宿主零感知）")
    ap.add_argument("--version", action="version",
                    version=f"loadn {__version__}")
    ap.add_argument("prompt", nargs="?", default=None)
    return ap


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # P0-3 子命令面：`loadn skills lock|verify`（argv 与 claude CLI 同构的
    # PROMPT 语义不破坏——仅当首参恰为 skills 时分派）
    if raw and raw[0] == "skills":
        from loadn.cli.skills_lock import main as skills_main
        return skills_main(raw[1:])
    if raw and raw[0] == "auth":
        from loadn.cli.auth import main as auth_main
        return auth_main(raw[1:])
    if raw and raw[0] == "daemon":
        from loadn.transport.daemon import main as daemon_main
        return daemon_main()
    if raw and raw[0] == "daemon-bridge":
        from loadn.transport.bridge import main as bridge_main
        return bridge_main()
    args = build_parser().parse_args(raw)
    prompt = args.prompt
    if prompt is None and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip() or None

    if not args.print_mode and args.output_format == "stream-json":
        print("loadn: stream-json 需要 -p（无头模式）", file=sys.stderr)
        return 2

    cwd = Path(os.getcwd())
    mode = (args.permission_mode
            or ("bypassPermissions" if args.yolo else "default"))

    from loadn.providers import provider_config
    cfg = provider_config()
    if args.model:
        cfg["model"] = args.model
    if args.effort:
        cfg.setdefault("extra", {})["effort"] = args.effort

    rc = asyncio.run(_run(args, prompt, cwd, cfg, mode))
    _kill_my_children()   # 兜底：杀掉自己派生、未被治理收尾的进程组
    return rc


# ---------------------------------------------------------------- 会话锁
def _lock_path(sid: str) -> Path:
    from loadn import loadn_home
    return loadn_home() / "sessions" / sid / "lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _acquire_lock(sid: str) -> Path | None:
    """同 id 并发运行 → None（already in use）。已有历史 transcript 的
    --session-id 也拒绝（镜像 claude 行为，宿主依赖该文案翻转 resume）。"""
    from loadn import loadn_home
    p = _lock_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        try:
            pid = int(p.read_text().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and pid != os.getpid() and _pid_alive(pid):
            return None
    ts = loadn_home() / "sessions" / sid / "transcript.jsonl"
    if ts.exists():
        return None   # 新档语义：历史已存在即拒（要续请用 --resume）
    p.write_text(str(os.getpid()))
    return p


def _release_lock(p: Path | None) -> None:
    if p is not None:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- 主流程
async def _run(args, prompt: str | None, cwd: Path, cfg: dict, mode: str) -> int:
    from loadn.cli.stream_json import StreamJsonEmitter
    from loadn.core.build import build_agent
    from loadn.core.loop import StopFlag
    from loadn.core.session import SessionManager

    sid = args.resume_id or args.session_id
    lock: Path | None = None
    if args.session_id:
        lock = _acquire_lock(args.session_id)
        if lock is None:
            print(f"Session ID already in use: {args.session_id}", file=sys.stderr)
            return 1

    stop = StopFlag()
    _install_signal_handlers(stop)

    # P0-2 信任门（交互模式问一次，admit 后本会话即按已信任加载——
    # 必须在 build_agent 之前；headless -p 不问，gate() fail-closed）
    if not args.print_mode:
        from loadn.cli.repl import trust_preflight
        trust_preflight(cwd)

    try:
        if args.fork and args.resume_id:
            session = SessionManager.fork(args.resume_id, cwd)
            sid = session.session_id
            bundle = await build_agent(cwd, session_id=sid, cfg=cfg,
                                       permission_mode=mode,
                                       max_turns=args.max_turns,
                                       no_compact=args.no_compact,
                                       no_plan=args.no_plan,
                                       grind=args.grind,
                                       budget_minutes=args.budget_minutes)
        else:
            bundle = await build_agent(
                cwd, session_id=sid, cfg=cfg, permission_mode=mode,
                max_turns=args.max_turns, no_compact=args.no_compact,
                no_plan=args.no_plan,
                grind=args.grind,
                budget_minutes=args.budget_minutes)
        core, sess = bundle.core, bundle.session

        if args.append_system_prompt:
            _patch_system(core, args.append_system_prompt)

        fmt = args.output_format
        emitter = StreamJsonEmitter(model=cfg.get("model") or "",
                                    tools=sorted(core.tools),
                                    protocol=getattr(args, "protocol", "v1"))
        emitter.session_id = sess.session_id
        if fmt == "stream-json":
            emitter.send_init(sess.session_id)

        if not args.print_mode:
            from loadn.cli.repl import run_repl
            return await run_repl(bundle, emitter, fmt, stop)

        if prompt is None:
            print("loadn: -p 模式需要 PROMPT 参数或管道 stdin", file=sys.stderr)
            return 2

        # stream-json：全事件流（turn 事件→result 由 emitter 收尾）；
        # json：只出最终 result；text：人读增量。--verbose 时逐 delta 外发
        # stream_event（claude CLI 同位语义）。
        emit = emitter if fmt == "stream-json" else None
        hb_task = asyncio.create_task(_heartbeat(emitter, fmt))
        try:
            summary = await core.run_turn(
                prompt, emit=emit, stop=stop,
                stream_events=(fmt == "stream-json" and args.verbose))
            if fmt == "json":
                emitter.send_result(summary)
            elif fmt == "text":
                sys.stdout.write((summary.text or "(无文本输出)") + "\n")
                sys.stdout.flush()
            if summary.subtype != "success":
                # 错误文案进 stderr（claude CLI 同位语义；宿主平台 的
                # fresh→resume 翻转分支靠 "already in use" stderr 文案）
                err_line = summary.error or summary.subtype
                print(err_line, file=sys.stderr)
            rc = 0 if summary.subtype == "success" else 1
        finally:
            hb_task.cancel()
            await _shutdown_bundle(bundle)
        return rc
    finally:
        _release_lock(lock)


async def _heartbeat(emitter, fmt: str) -> None:
    """工具长执行期间的心跳行（stdout 判活信号；text/json 格式不发）。"""
    if fmt != "stream-json":
        return
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        emitter.send_heartbeat()


async def _shutdown_bundle(bundle) -> None:
    for conn in getattr(bundle, "mcp_conns", []) or []:
        try:
            await conn.stop()
        except Exception:  # noqa: BLE001
            pass
    sup = getattr(bundle, "supervisor", None)
    if sup is not None:
        try:
            await sup.shutdown()
        except Exception:  # noqa: BLE001
            pass


def _patch_system(core, extra: str) -> None:
    inner = core.assembler

    class _Patched:
        def build(self, **kw):
            return inner.build(**kw) + "\n\n## 追加指令\n" + extra

    core.assembler = _Patched()


def _install_signal_handlers(stop) -> None:
    def _sigint(*_):
        if stop.requested:
            sys.exit(130)   # 第二次 Ctrl-C：硬退
        stop.requested = True

    def _sigterm(*_):
        # 优雅收尾：stop 标记 + 子进程治理由 _kill_my_children 兜底
        stop.requested = True
        sys.exit(143)

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _sigint)
        loop.add_signal_handler(signal.SIGTERM, _sigterm)
    except (NotImplementedError, RuntimeError):
        pass


def _kill_my_children() -> None:
    """杀掉本进程派生的全部进程组（Bash 工具子进程自成进程组，killpg 主组
    够不到它们——SIGTERM 兜底扫描 /proc 按 ppid 清扫）。"""
    me = os.getpid()
    import glob as _glob
    for stat in _glob.glob("/proc/[0-9]*/stat"):
        try:
            raw = Path(stat).read_text()
            ppid = int(raw.rsplit(")", 1)[1].split()[1])
            pid = int(stat.split("/")[2])
        except (OSError, IndexError, ValueError):
            continue
        if ppid == me and pid != me:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
