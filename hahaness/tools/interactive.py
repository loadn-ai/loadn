"""InteractiveShell 工具——pty 会话脚本化交互（Terminal-Bench 死法②）。

死因画像：play-zork 类任务 60 次工具调用，每轮游戏交互付一次完整 LLM
调用。本工具让一次调用携带 steps 脚本跑多轮 send/expect，transcript 一次
带回——LLM 轮次砍 5-10x。

实现要点（零外部依赖，tmux 不保证在容器里）：
- pty.openpty：master 置 O_NONBLOCK + TIOCSWINSZ(24×80)（TUI 程序没
  winsize 会炸），env 带 TERM=xterm；**父进程必须 close(slave)**——否则
  子进程退出后 EIO 永不触发，EOF 检测失效。
- 读用轮询（SHELL_READ_POLL_S 周期非阻塞 os.read），不用
  asyncio.add_reader：wait_for 超时取消协程时 fd 回调不属于协程，漏
  remove_reader 会留永久回调往死会话灌数据；轮询无注册态，取消即干净。
- EOF 判定三路：os.read 抛 EIO / 返回 b""（BSD 形态）/ popen 已退出且
  0.2s 无新数据（孙进程占着 slave 时 EIO 迟迟不来）。
- 会话表在 ctx.extras["shells"]（跨 turn 存活、随 ToolContext 隔离）；
  进程经 supervisor.track 只挂收割梯子（master fd 由本工具自管，无
  reader——退出经 mark_exited 落态）。supervisor 为空（子代理）时只靠
  CLI 的 /proc ppid 扫描兜底。
- REPL 语义：send 原样字节（裸 ctrl 键如 \\x03 不补换行）；有 expect 从
  本步发送点起轮询至命中或超时（不含历史——上一轮的提示符就在发送点
  之前，纳入会秒命中假匹配；buffer 滚动后 start 失效则搜全部余量）——
  **超时不是错误**（会话保留，返回 timed_out 让模型续步）；无 expect
  固定等 SHELL_STEP_WAIT_S 排空。单次调用累计预算 SHELL_TOTAL_BUDGET_S，
  先于 loop 层超时返回。
"""
from __future__ import annotations

import asyncio
import errno
import fcntl
import os
import pty
import re
import signal
import struct
import subprocess
import termios
import time

from hahaness.constants import (
    SHELL_BUFFER_MAX,
    SHELL_READ_POLL_S,
    SHELL_SESSIONS_MAX,
    SHELL_STEP_RECV_CLIP,
    SHELL_STEP_TIMEOUT_S,
    SHELL_STEP_WAIT_S,
    SHELL_STEPS_MAX,
    SHELL_TOTAL_BUDGET_S,
)
from hahaness.tools.base import Tool, ToolContext, ToolError

_EOF_GRACE_S = 0.2          # popen 已退出后无新数据的 EOF 判定窗口


class _Shell:
    """一个 pty 会话的运行态。"""

    def __init__(self, name: str, master_fd: int, popen, task_id: str) -> None:
        self.name = name
        self.master_fd = master_fd
        self.popen = popen
        self.task_id = task_id          # supervisor 登记号（可空串）
        self.buffer = b""
        self.eof = False


class InteractiveShellTool(Tool):
    """pty 会话：与交互程序（REPL/游戏/向导/TUI）脚本化多轮对话。"""

    name = "InteractiveShell"
    # 40 步 × 10s = 400s > loop 层 300s 默认——必须抬高，否则 transcript
    # 会被泛化超时丢掉（会话状态存活但本轮交互结果丢失）
    timeout_s = 1800
    description = (
        "与交互式程序（REPL/终端游戏/安装向导/读提示符应答的命令行）建立"
        " pty 会话并脚本化交互：一次调用可携带多个 step（每个 step = send"
        " 一条输入 + expect 等待一个输出模式），全部 transcript 一次带回"
        "——比每轮交互调一次工具省 5-10 倍轮次。普通非交互命令用 Bash（别"
        "用本工具）；也别用 Bash 跑交互程序——它的 stdin 是关闭的，程序会"
        "挂死到 60s 被转后台。send 原样写入（裸控制键直接发如 '\\x03'=Ctrl-C）；"
        "expect 是正则；expect 超时不报错（返回 timed_out，会话保留可续）。"
    )
    input_schema: dict = {
        "type": "object",
        "properties": {
            "session": {"type": "string",
                        "description": "已存在会话名（续用/查询/杀）"},
            "command": {"type": "string",
                        "description": "新建会话要跑的命令（如 zork / python3 / psql -U pg db）"},
            "steps": {
                "type": "array",
                "description": "按序执行的交互步 [{send, expect?, timeout_s?}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "send": {"type": "string", "description": "要发送的输入（原样字节；ctrl 键用 \\x03 形态）"},
                        "expect": {"type": "string",
                                   "description": "等待的输出模式（正则；缺省=固定等待 1.5s 后收全部新输出）"},
                        "timeout_s": {"type": "number",
                                      "description": f"本步等待超时（默认 {SHELL_STEP_TIMEOUT_S:g}s；超时不报错）"},
                    },
                    "required": ["send"],
                },
            },
            "capture_only": {"type": "boolean",
                             "description": "true=只回传当前 buffer 尾部，不发输入"},
            "kill": {"type": "boolean", "description": "true=杀掉并移除会话"},
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        shells: dict[str, _Shell] = ctx.extras.setdefault("shells", {})
        # ---- kill 分支
        if args.get("kill"):
            name = str(args.get("session") or "")
            sh = shells.get(name)
            if sh is None:
                raise ToolError(f"会话不存在：{name}（现有：{sorted(shells)}）")
            _kill_shell(sh, ctx)
            del shells[name]
            return f"会话 {name} 已终止并移除"
        # ---- 定位/新建会话
        name = str(args.get("session") or "")
        if name:
            sh = shells.get(name)
            if sh is None:
                raise ToolError(f"会话不存在：{name}（现有：{sorted(shells)}）")
        else:
            command = str(args.get("command") or "").strip()
            if not command:
                raise ToolError("新建会话需要 command（或给出已存在的 session）")
            if len(shells) >= SHELL_SESSIONS_MAX:
                raise ToolError(f"会话数已达上限 {SHELL_SESSIONS_MAX}"
                                f"（现有：{sorted(shells)}），先 kill 腾位")
            sh = _spawn_shell(command, ctx)
            shells[sh.name] = sh
        # ---- capture_only 分支
        if args.get("capture_only"):
            _pump(sh, ctx)
            return f"[{sh.name}] 当前输出尾部：\n{_tail(sh.buffer, 8192)}"
        # ---- steps 执行
        steps = args.get("steps")
        if not steps and not args.get("command") and not args.get("capture_only"):
            raise ToolError("需要 steps（或 command 新建、capture_only/kill）")
        if not isinstance(steps, list):
            steps = []
        if len(steps) > SHELL_STEPS_MAX:
            raise ToolError(f"steps 超上限 {SHELL_STEPS_MAX}（收到 {len(steps)}），拆多次调用")
        if sh.eof:
            return (f"[{sh.name}] 会话已结束（EOF）。最后输出：\n"
                    f"{_tail(sh.buffer, 8192)}\n需要继续请新建会话。")
        lines = [f"[{sh.name}] pid={sh.popen.pid}"
                 + ("" if not steps else f"，执行 {len(steps)} 步：")]
        budget = SHELL_TOTAL_BUDGET_S
        # 纯新建（无 steps）：先固定排空一段初始输出（banner/游戏开场白）
        if not steps:
            await asyncio.sleep(SHELL_STEP_WAIT_S)
            _pump(sh, ctx)
            if sh.buffer:
                lines.append("初始输出：\n" + _clip_unicode(
                    _tail(sh.buffer, 8192), 8192))
        for i, step in enumerate(steps, 1):
            if not isinstance(step, dict) or not isinstance(step.get("send"), str):
                raise ToolError(f"steps[{i}] 需为 {{send: str, expect?: str, timeout_s?: num}}")
            step_t0 = time.monotonic()
            limit = min(float(step.get("timeout_s") or SHELL_STEP_TIMEOUT_S),
                        max(1.0, budget))
            sent, received, timed_out, eof = await self._step(sh, ctx, step, limit)
            budget -= time.monotonic() - step_t0
            recv_txt = received if received else "（无新输出）"
            tag = "（expect 超时，会话保留）" if timed_out else ""
            lines.append(f"--- step {i}：send={sent!r}{tag}")
            lines.append(_clip_unicode(recv_txt, SHELL_STEP_RECV_CLIP))
            if eof:
                lines.append("（会话已结束 EOF，后续 step 跳过）")
                break
        if steps and not lines[-1].startswith("（会话已结束"):
            lines.append(f"--- 会话 {sh.name} 仍存活，可继续用 session 续交互")
        return "\n".join(lines)

    # ------------------------------------------------------------ 单步
    async def _step(self, sh: _Shell, ctx: ToolContext, step: dict,
                    limit_s: float) -> tuple[str, str, bool, bool]:
        """执行一步：send → 等待 expect 命中/固定排空。

        返回 (sent, 新输出文本, expect 是否超时, 会话是否 EOF)。
        """
        raw = step["send"]
        payload = raw.encode("utf-8", errors="replace")
        # 裸控制键（\x03 等）不补换行；普通输入确保以 \n 结尾触发执行
        if payload and not payload.endswith(b"\n") \
                and payload[-1] >= 0x20:
            payload += b"\n"
        _write_master(sh.master_fd, payload)
        start = len(sh.buffer)
        expect = step.get("expect")
        deadline = time.monotonic() + (limit_s if expect else SHELL_STEP_WAIT_S)
        last_data = time.monotonic()
        while True:
            got_new = _pump(sh, ctx)
            if got_new:
                last_data = time.monotonic()
            if expect is not None:
                # 匹配窗从本步发送点起（不含历史——上一轮的提示符就在
                # 发送点之前，纳入会秒命中假匹配）；buffer 滚动后 start
                # 失效（> 当前长度）则搜全部余量（尾部含本步新数据）
                ws = start if start <= len(sh.buffer) else 0
                window = sh.buffer[ws:].decode("utf-8", errors="replace")
                if re.search(expect, window, re.MULTILINE):
                    new = sh.buffer[ws:].decode("utf-8", errors="replace")
                    return raw, new, False, sh.eof
            if time.monotonic() >= deadline:
                ws = start if start <= len(sh.buffer) else 0
                new = sh.buffer[ws:].decode("utf-8", errors="replace")
                # 超时非错误：会话保留，返回 timed_out 让模型续步/改策略
                return raw, new, expect is not None, sh.eof
            if sh.eof and not got_new:
                ws = start if start <= len(sh.buffer) else 0
                new = sh.buffer[ws:].decode("utf-8", errors="replace")
                return raw, new, False, True
            # popen 已死且短窗无新数据 → 按 EOF 收（孙进程占 slave 时 EIO 不来）
            if sh.popen.poll() is not None \
                    and time.monotonic() - last_data > _EOF_GRACE_S:
                sh.eof = True
                _mark_exit(sh, ctx)
                ws = start if start <= len(sh.buffer) else 0
                new = sh.buffer[ws:].decode("utf-8", errors="replace")
                return raw, new, expect is not None, True
            await asyncio.sleep(SHELL_READ_POLL_S)


# ---------------------------------------------------------------- 进程面
def _spawn_shell(command: str, ctx: ToolContext) -> _Shell:
    """pty 上起 bash -c command；supervisor 只挂收割梯子（无 reader）。"""
    master_fd, slave_fd = pty.openpty()
    try:
        fcntl.fcntl(master_fd, fcntl.F_SETFL,
                    fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)
        # TUI 程序（无 winsize 会炸）：24×80 足够读，也压输出宽度
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", 24, 80, 0, 0))
        env = {**os.environ, "TERM": "xterm"}
        popen = subprocess.Popen(
            ["bash", "-c", command],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            cwd=str(ctx.cwd), env=env, start_new_session=True)
    finally:
        os.close(slave_fd)        # 父进程必须关：否则子退出后 EIO 永不触发
    task_id = ""
    sup = ctx.supervisor
    if sup is not None:
        try:
            info = sup.track(popen.pid, popen.pid,
                             ["bash", "-c", command[:200]], proc=popen)
            task_id = info.id
        except Exception:         # noqa: BLE001 — 登记失败不阻断会话
            task_id = ""
    name = f"sh{len(ctx.extras.get('shells', {})) + 1}"
    return _Shell(name, master_fd, popen, task_id)


def _kill_shell(sh: _Shell, ctx: ToolContext) -> None:
    """杀会话：进程组 SIGKILL + 关 master + 同步 wait 兜底 + 落态出表。"""
    try:
        os.killpg(sh.popen.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.close(sh.master_fd)
    except OSError:
        pass
    try:
        sh.popen.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    sh.eof = True
    sup = ctx.supervisor
    if sup is not None and sh.task_id:
        try:
            sup.mark_exited(sh.task_id, sh.popen.returncode or 0)
        except Exception:         # noqa: BLE001
            pass


def _mark_exit(sh: _Shell, ctx: ToolContext) -> None:
    sup = ctx.supervisor
    if sup is not None and sh.task_id:
        try:
            sup.mark_exited(sh.task_id, sh.popen.returncode or 0)
        except Exception:         # noqa: BLE001
            pass


def _pump(sh: _Shell, ctx: ToolContext) -> bool:
    """非阻塞读尽 master；返回是否有新数据。EOF 三路判定见模块 docstring。"""
    got = False
    while not sh.eof:
        try:
            data = os.read(sh.master_fd, 65536)
        except BlockingIOError:
            break
        except OSError as e:
            # Linux：slave 全关后 read(master) 抛 EIO → EOF
            if e.errno in (errno.EIO, errno.EBADF):
                sh.eof = True
                _mark_exit(sh, ctx)
            break
        if not data:              # BSD 形态：b"" 即 EOF
            sh.eof = True
            _mark_exit(sh, ctx)
            break
        sh.buffer += data
        got = True
    if len(sh.buffer) > SHELL_BUFFER_MAX:
        sh.buffer = sh.buffer[-SHELL_BUFFER_MAX:]   # 滚动保尾
    return got


def _write_master(fd: int, payload: bytes) -> None:
    """写 master（EAGAIN 重试一次；写侧满意味着程序不读输入，放弃即自杀线索）。"""
    for attempt in (0, 1):
        try:
            os.write(fd, payload)
            return
        except BlockingIOError:
            if attempt == 0:
                time.sleep(0.01)
                continue
        except OSError:
            return


def _tail(buf: bytes, n: int) -> str:
    return buf[-(n * 4):].decode("utf-8", errors="replace")[-n:]


def _clip_unicode(text: str, n: int) -> str:
    """保尾裁剪（尾部是 expect 命中点与最新状态，比头部更有价值）。"""
    return text if len(text) <= n else "…[截断]\n" + text[-n:]


tool = InteractiveShellTool()     # ToolRegistry.default() 收集的模块级实例
