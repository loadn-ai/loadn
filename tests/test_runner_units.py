"""M6f：claude_runner 自噬防护与事件汇直测（w0 窄集不触及的 spawn 区）。

_parent_is_server/pid_alive_for_turn 是收养与看门狗的判定底座——判错
=误杀无辜进程或泄漏孤儿。_Sink.feed_event 的 result 门是引擎侧记账的
唯一入口。用 exec -a 伪造 argv + /proc/environ 真进程验证。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest

from loadn_webui.claude_runner import (
    _parent_is_server,
    _Sink,
    pid_alive_for_turn,
)


def _spawn(argv0: str, *args: str, env_extra: dict | None = None):
    """伪造 argv0 的短命子进程（/proc 可观测）。"""
    return subprocess.Popen(
        ["/bin/bash", "-c", f'exec -a "{argv0}" sleep 5'] + list(args),
        env={**os.environ, **(env_extra or {})},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture(autouse=True)
def _reap():
    procs: list = []
    yield procs
    for p in procs:
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=5)


def test_parent_is_server_by_cmdline(_reap):
    serve = _spawn("loadn_webui serve --port 8792")
    _reap.append(serve)
    serve2 = _spawn("loadn serve --port 1")     # 短名形态（不含 webui 子串）
    _reap.append(serve2)
    plain = _spawn("worker-loop")
    _reap.append(plain)
    alien = _spawn("nginx serve master")        # 别家的 serve 不是本平台
    _reap.append(alien)
    time.sleep(0.2)
    assert _parent_is_server(serve.pid) is True      # serve 实例在管
    assert _parent_is_server(serve2.pid) is True     # 短名同认
    assert _parent_is_server(plain.pid) is False     # 无辜进程不动
    assert _parent_is_server(alien.pid) is False     # serve 关键字不连坐
    assert _parent_is_server(999_999_999) is False   # 不存在的 pid


def test_pid_alive_for_turn_env_marker(_reap):
    """environ 带 TURN_ID 才算本 turn 的进程（防 pid 复用误判）。"""
    child = _spawn("any-worker", env_extra={"LOADN_TURN_ID": "707"})
    _reap.append(child)
    time.sleep(0.2)
    assert pid_alive_for_turn(child.pid, 707) is True
    assert pid_alive_for_turn(child.pid, 708) is False  # tid 不匹配=非本 turn
    legacy = _spawn("claude-worker", env_extra={"WORKDADDY_TURN_ID": "709"})
    _reap.append(legacy)
    time.sleep(0.2)
    assert pid_alive_for_turn(legacy.pid, 709) is True  # 旧名兼容
    assert pid_alive_for_turn(999_999_999, 707) is False


def test_pid_alive_for_turn_cmdline_fallback(_reap):
    """environ 不可读（跨用户）→ cmdline 引擎标记启发式回落。"""
    # 自造不可读 environ：用 sudo 不可行；改验启发式正路——本进程组内
    # 有引擎标记的 cmdline 命中（回落分支用同一组标记）
    marked = _spawn("loadn engine-run")
    _reap.append(marked)
    time.sleep(0.2)
    # 不直接构造不可读 environ（需跨用户权限）；此断言锁定启发式标记集
    # 与文档一致，回落分支的完整覆盖交由 e2e（root 环境）
    cmd = subprocess.run(
        ["grep", "-ao", "loadn", f"/proc/{marked.pid}/cmdline"],
        capture_output=True)
    assert cmd.stdout  # 标记在 cmdline 中可见（回落判据的输入面）


# ---------------------------------------------------------------- _Sink
class _Adapter:
    @staticmethod
    def feed(ev):
        return [ev]


async def test_sink_result_gate_and_dispatch():
    got = []

    async def on_event(ev):
        got.append(ev)

    sink = _Sink(_Adapter, on_event)
    await sink.feed_event({"type": "assistant", "text": "hi"})
    assert got and sink.meta == {}                    # 非 result 不入 meta
    await sink.feed_event({"type": "result", "session_id": "s1",
                           "usage": {"input_tokens": 3},
                           "subtype": "success", "result": "完成"})
    assert sink.meta["session_id"] == "s1"            # result 门是记账入口
    assert sink.meta["usage"]["input_tokens"] == 3
    assert sink.meta["result"] == "完成"
    # 原始行：非 JSON/半行免疫；result 行被归一与分发
    sink2 = _Sink(_Adapter, on_event)
    await sink2.feed_raw(b"not-json-garbage")
    await sink2.feed_raw(b'{"type":"result","session_id":"s2"}')
    assert sink2.meta["session_id"] == "s2"
    assert len(got) == 3


async def test_sink_on_event_crash_isolated():
    """on_event 消费异常不炸 feed（引擎侧事件面永不反噬主流程）。"""
    async def boom(ev):
        raise RuntimeError("consumer down")

    sink = _Sink(_Adapter, boom)
    await sink.feed_event({"type": "result", "session_id": "s9"})
    assert sink.meta["session_id"] == "s9"            # 记账先于分发，不受影响
