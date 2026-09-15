"""进程治理——Bash 后台任务的生命周期管理与双信号判死。

孤儿防御是本模块的第一原则：只治理自己登记过的 pid（spawn 时
start_new_session=True 独立进程组，pgid==pid），绝不扫全局进程表——
扫全局杀掉别人家进程是嵌入式 agent 事故的经典来源。

三块职责：
- spawn_bg：登记 + 输出 tee 到 cwd/logs/task_<id>.out（异步 reader 协程）
- watchdog：双信号判死——某任务 stdout 静默超阈值【且】产物路径无新写入
  才标 stalled（只标记不杀，杀不杀由上层决断；写文件型任务安静不算死）
- shutdown：SIGTERM→宽限→SIGKILL 收割全部登记进程组，供 CLI 退出钩子
  （SIGTERM handler）调用
"""
from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from hahaness.constants import BASH_KILL_GRACE_S, STREAM_LINE_MAX


@dataclass
class ProcInfo:
    """一个后台任务的登记信息（SessionState.background_tasks 的条目形态）。"""

    id: str
    pid: int
    pgid: int
    cmd: list[str]
    started_at: float
    status: str               # running|done|failed|killed
    output_path: str


def _mtime(path: str | Path) -> float:
    """路径存在返回 mtime，否则 0.0（判死路径探测用，不抛错）。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _sig_group(pgid: int, sig: int) -> None:
    """对进程组发信号；组已消失则静默（收割竞态是常态）。"""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


async def kill_process_group(proc: asyncio.subprocess.Process,
                             grace_s: float = BASH_KILL_GRACE_S) -> bool:
    """SIGTERM 进程组 → 宽限 → SIGKILL；返回是否发出过信号（False=早已退出）。"""
    if proc.returncode is not None:
        return False
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        pgid = proc.pid          # start_new_session 下 pgid==pid，兜底直取
    _sig_group(pgid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
        return True
    except asyncio.TimeoutError:
        pass
    _sig_group(pgid, signal.SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
    except asyncio.TimeoutError:
        pass
    return True


def _tail(path: str | Path, chars: int) -> str:
    """读文件尾部 chars 字符（poll 展示用；读不到返回空串）。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    return data[-(chars * 4):].decode("utf-8", errors="replace")[-chars:]


class ProcessSupervisor:
    """登记式进程组治理（事件循环内使用，非线程安全）。"""

    def __init__(self) -> None:
        self._procs: dict[str, ProcInfo] = {}
        self._proc_objs: dict[str, asyncio.subprocess.Process] = {}
        self._readers: dict[str, asyncio.Task] = {}
        self._last_output: dict[str, float] = {}      # id → 最近输出时间
        self._killing: set[str] = set()               # 显式收割标记（reader 让位）
        self._stalled: set[str] = set()
        self._watchdog_task: asyncio.Task | None = None
        self._seq = 0

    # ---------------------------------------------------------------- spawn
    async def spawn_bg(self, cmd: list[str], cwd: str | Path,
                       env: dict | None = None) -> ProcInfo:
        """启动后台进程并登记：输出 tee 到 cwd/logs/task_<id>.out。"""
        self._seq += 1
        task_id = f"t{self._seq}"
        logs = Path(cwd) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(cwd), env=env, start_new_session=True,
            limit=STREAM_LINE_MAX)
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pgid = proc.pid
        info = ProcInfo(id=task_id, pid=proc.pid, pgid=pgid, cmd=list(cmd),
                        started_at=time.time(), status="running",
                        output_path=str(logs / f"task_{task_id}.out"))
        self._procs[task_id] = info
        self._proc_objs[task_id] = proc
        self._last_output[task_id] = info.started_at
        self._readers[task_id] = asyncio.create_task(
            self._reader(info, proc), name=f"sup-reader-{task_id}")
        return info

    async def _reader(self, info: ProcInfo,
                      proc: asyncio.subprocess.Process) -> None:
        """异步 reader：stdout 持续落盘；EOF 后回收状态。"""
        try:
            with open(info.output_path, "ab") as f:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    f.flush()
                    self._last_output[info.id] = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass                    # 读侧异常不扩大事故，输出文件保留已写部分
        try:
            rc = await proc.wait()
        except Exception:
            rc = -1
        # 显式收割（kill/shutdown）的落态由收割方写 killed，reader 不抢
        if info.status == "running" and info.id not in self._killing:
            info.status = "done" if rc == 0 else "failed"

    # ---------------------------------------------------------------- 查询
    def poll(self, task_id: str) -> dict:
        """单任务状态 + 输出尾部 2000 字符。"""
        info = self._procs.get(task_id)
        if info is None:
            raise KeyError(f"未知后台任务 {task_id}")
        out = asdict(info)
        out["output_tail"] = _tail(info.output_path, 2000)
        return out

    def list(self) -> list[dict]:
        """全部登记任务的概览（不含输出尾部，轻量）。"""
        return [asdict(info) for info in self._procs.values()]

    @property
    def stalled_ids(self) -> list[str]:
        """watchdog 判定的静默任务（只标记不杀，上层决断）。"""
        return sorted(self._stalled)

    # ---------------------------------------------------------------- kill
    async def kill(self, task_id: str) -> dict:
        """杀单个登记任务（SIGTERM→宽限→SIGKILL 进程组）。"""
        info = self._procs.get(task_id)
        if info is None:
            raise KeyError(f"未知后台任务 {task_id}")
        proc = self._proc_objs.get(task_id)
        signalled = False
        if proc is not None and info.status == "running":
            self._killing.add(task_id)
            signalled = await kill_process_group(proc)
        if signalled:
            info.status = "killed"
        return {"id": info.id, "status": info.status}

    # ---------------------------------------------------------------- watchdog
    async def watchdog(self, io_silence_s: float, artifact_paths=(),
                       interval: float = 5.0) -> None:
        """循环判死：stdout 静默超阈值【且】产物无新写入 → 标 stalled。

        双信号的另一半是任务自己的输出文件 mtime（与内存计数取大）；
        artifact_paths 是任务可能在写的产物路径（外部文件），任一新鲜
        即视为存活——写文件不出声的批处理不该被判死。
        """
        try:
            while True:
                await asyncio.sleep(interval)
                now = time.time()
                for info in list(self._procs.values()):
                    if info.status != "running":
                        continue
                    last_out = max(self._last_output.get(info.id,
                                                         info.started_at),
                                   _mtime(info.output_path))
                    if now - last_out <= io_silence_s:
                        continue
                    if artifact_paths:
                        fresh = max((m for m in (_mtime(p)
                                                 for p in artifact_paths) if m),
                                    default=0.0)
                        if now - fresh <= io_silence_s:
                            continue
                    self._stalled.add(info.id)
        except asyncio.CancelledError:
            raise

    def start_watchdog(self, io_silence_s: float, artifact_paths=(),
                       interval: float = 5.0) -> None:
        """拉起 watchdog 后台协程（重复调用先停旧的）。"""
        self.stop_watchdog()
        self._watchdog_task = asyncio.create_task(
            self.watchdog(io_silence_s, artifact_paths, interval),
            name="sup-watchdog")

    def stop_watchdog(self) -> None:
        """停掉 watchdog 协程（幂等；同步发取消，收尸由 shutdown/事件循环兜）。"""
        task = self._watchdog_task
        self._watchdog_task = None
        if task is not None and not task.done():
            task.cancel()

    # ---------------------------------------------------------------- shutdown
    async def shutdown(self) -> None:
        """收割全部登记进程组（SIGTERM→3s→SIGKILL），状态落 killed。幂等。"""
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass                    # 自己 cancel 的，收尸即完成
            except Exception:
                pass
            self._watchdog_task = None
        alive = [info for info in self._procs.values() if info.status == "running"]
        for info in alive:
            self._killing.add(info.id)
            _sig_group(info.pgid, signal.SIGTERM)
        if alive:
            deadline = time.monotonic() + BASH_KILL_GRACE_S
            for info in alive:
                proc = self._proc_objs.get(info.id)
                if proc is None:
                    info.status = "killed"
                    continue
                try:
                    await asyncio.wait_for(
                        proc.wait(), timeout=max(0.0, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    _sig_group(info.pgid, signal.SIGKILL)
                    try:
                        await asyncio.wait_for(proc.wait(),
                                               timeout=BASH_KILL_GRACE_S)
                    except asyncio.TimeoutError:
                        pass
                info.status = "killed"       # 显式收割，覆盖 reader 可能的落态
        if self._readers:
            await asyncio.gather(*self._readers.values(),
                                 return_exceptions=True)
            self._readers.clear()
