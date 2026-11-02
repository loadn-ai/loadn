"""verify 脚本防泄漏骨架：一次性 vite dev server（context manager）。

背景（2026-11-02 生产实证）：playwright verify/截图脚本各自 Popen 起
vite dev（--strictPort 固定端口），脚本异常退出/漏 finally 时 dev server
常驻泄漏（4 个存活 7 天，监控面板一排不明 200 端口）。本模块是后续
verify 脚本的统一骨架——进程组管理+finally 必杀+空闲端口自动分配，
根治「脚本死了 server 还活着」。

用法（verify 脚本内）：
    from verify_lib import dev_server
    with dev_server() as base:          # http://127.0.0.1:<port>
        page.goto(f"{base}/#/admin/security")
        ...断言...
    # with 块退出即 SIGTERM 进程组，超时 SIGKILL 兜底
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

UI_ROOT = Path("/data/code/loadn/ui")
READY_TIMEOUT_S = 30.0


def _free_port() -> int:
    """内核分配一个空闲端口（bind 0 → 读回 → 关闭；strictPort 抢占窗口极小）。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def dev_server(root: Path = UI_ROOT, extra_args: list[str] | None = None):
    """起一次性 vite dev（start_new_session 进程组，退出必杀）。

    yield base url（str）。ready 探测 /package.json（vite 必有，200 即活）。
    """
    port = _free_port()
    cmd = ["npx", "vite", "--port", str(port), "--strictPort",
           "--host", "127.0.0.1", *(extra_args or [])]
    proc = subprocess.Popen(
        cmd, cwd=str(root), start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"vite dev 提前退出 rc={proc.returncode}")
            try:
                with urllib.request.urlopen(f"{base}/package.json", timeout=2) as r:
                    json.loads(r.read())         # 200 + 合法 JSON = 就绪
                    break
            except Exception as e:               # noqa: BLE001 — 轮询期异常常态
                last_err = e
                time.sleep(0.3)
        else:
            raise RuntimeError(f"vite dev {READY_TIMEOUT_S}s 未就绪：{last_err}")
        yield base
    finally:
        # 进程组整体收（vite 会 fork esbuild 服务子进程——杀主进程会留孤儿子进程，
        # 这正是历史泄漏的形态之一）
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
