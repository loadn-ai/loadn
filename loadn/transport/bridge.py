"""桥进程：stdin/stdout ↔ UDS（codex stdio-to-uds 38 行同构的 Python 形）。

用法：`loadn daemon-bridge`（env LOADN_ENGINE_SOCK 指定 socket 路径）。
宿主 spawn 本进程替代直接 spawn 引擎——对宿主而言仍是 stdin/stdout 的
NDJSON 子进程，零改动；实际数据面在 daemon 的 UDS。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


async def run_bridge(sock_path: Path) -> int:
    """stdin→UDS 与 UDS→stdout 双向裸转发（半关闭语义同 codex）。"""

    loop = asyncio.get_event_loop()

    def _pump_sync(src, dst_writer) -> None:
        """同步半边：read1（有多少读多少——read(n) 会阻塞等满 n 字节，
        管道小消息直接挂死）→ 写入对端 writer 的线程安全队列。"""
        while True:
            try:
                chunk = src.read1(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            loop.call_soon_threadsafe(_enqueue, dst_writer, chunk)

    def _enqueue(writer, chunk):
        writer.write(chunk)

    # UDS 侧是 asyncio 流：异步泵；stdio 侧同步读 → call_soon_threadsafe
    try:
        reader, writer = await asyncio.open_unix_connection(str(sock_path))
    except OSError as e:
        print(f"loadn daemon-bridge: 连接 {sock_path} 失败：{e}", file=sys.stderr)
        return 1

    import threading

    async def _uds_to_stdout():
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        except OSError:
            pass

    def _stdin_eof():
        # stdin 关闭 → 半关闭 UDS 写侧（codex shutdown 语义）；daemon 侧
        # readline 得 EOF 收尾连接 → 本侧 reader EOF → 进程退出
        try:
            loop.call_soon_threadsafe(writer.close)
        except RuntimeError:
            pass

    t = threading.Thread(target=lambda: (
        _pump_sync(sys.stdin.buffer, writer), _stdin_eof()), daemon=True)
    t.start()
    try:
        await _uds_to_stdout()
    finally:
        try:
            writer.close()
        except OSError:
            pass
    return 0


def main() -> int:
    sock = os.environ.get("LOADN_ENGINE_SOCK") or \
        str(Path.home() / ".loadn" / "var" / "engine.sock")
    return asyncio.run(run_bridge(Path(sock)))


if __name__ == "__main__":
    raise SystemExit(main())
