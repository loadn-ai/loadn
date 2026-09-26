"""P2-1 daemon 多传输验收（子进程真 daemon + UDS 连接；fake provider 零 token）。

- 鉴权：无/错 token attach 被拒（connection_auth 语义）；token 文件 0600
- spawn→run turn→事件流回连接（v1 方言白名单）
- 断开→重连→同会话续作（AgentCore 复用——daemon 内 cores 不重建）
- 桥：spawn→UDS→stdout 透传（宿主视角仍是 NDJSON 子进程）
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("engine.daemon")]

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PY = sys.executable


class Daemon:
    def __init__(self, cwd: Path, home: Path):
        self.cwd, self.home = cwd, home
        self.proc: subprocess.Popen | None = None
        self.token = ""
        self.sock = home / "var" / "engine.sock"

    def start(self):
        env = {**os.environ, "LOADN_HOME": str(self.home),
               "LOADN_PROVIDER": "fake", "LOADN_FAKE_DIR": str(self.cwd / ".fake"),
               "PYTHONPATH": str(REPO)}
        self.proc = subprocess.Popen(
            [PY, "-m", "loadn", "daemon"], cwd=self.cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            tok = self.home / "var" / "engine.token"
            if tok.exists() and self.sock.exists():
                self.token = tok.read_text().strip()
                return
            time.sleep(0.1)
        raise TimeoutError("daemon 未就绪")

    def stop(self):
        if self.proc:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    async def connect(self, sid: str, token: str | None = None):
        r, w = await asyncio.open_unix_connection(str(self.sock))
        w.write((json.dumps({"auth": token or self.token, "session_id": sid})
                 + "\n").encode())
        await w.drain()
        return r, w

    async def read_events(self, r, n: int, timeout: float = 30) -> list[dict]:
        """读到第 n 条或（n=0 时）读到 result 终态。"""
        out = []
        deadline = asyncio.get_event_loop().time() + timeout

        async def _next() -> str | None:
            try:
                return await asyncio.wait_for(
                    r.readline(), timeout=max(0.1, deadline -
                                              asyncio.get_event_loop().time()))
            except asyncio.TimeoutError:
                return None
        while True:
            ln = await _next()
            if not ln:
                break
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            out.append(ev)
            if n and len(out) >= n:
                break
            if not n and ev.get("type") == "result":
                break
        return out


async def _run_turn(d: Daemon, sid: str, prompt: str,
                    token: str | None = None) -> list[dict]:
    r, w = await d.connect(sid, token)
    w.write((json.dumps({"type": "run", "prompt": prompt}) + "\n").encode())
    await w.drain()
    evs = await d.read_events(r, 0)          # 读到 result 终态
    w.close()
    return evs


@pytest.fixture
def daemon(tmp_path):
    (tmp_path / ".fake").mkdir()
    (tmp_path / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "echo hi"}}))
    d = Daemon(tmp_path, tmp_path / "home")
    d.start()
    yield d
    d.stop()


# ---------------------------------------------------------------- 鉴权
async def test_auth_rejected(daemon, tmp_path):
    r, w = await daemon.connect("s1", token="WRONG")
    evs = await daemon.read_events(r, 1, timeout=5)
    assert evs and evs[0]["type"] == "error" and "auth" in evs[0]["error"]
    w.close()
    assert (daemon.home / "var" / "engine.token").stat().st_mode & 0o777 == 0o600
    assert daemon.sock.stat().st_mode & 0o777 == 0o600   # socket 同 0600


# ---------------------------------------------------------------- turn + 断连重连
async def test_turn_and_resume_same_session(daemon):
    sid = "daem0n00-0000-4000-8000-000000000001"
    evs = await _run_turn(daemon, sid, "第一轮")
    types = [e["type"] for e in evs]
    assert "system" in types and "result" in types
    init = next(e for e in evs if e["type"] == "system" and
                e.get("subtype") == "init")
    assert init["session_id"] == sid
    assert init.get("model") not in ("", None, "unknown")  # 真名非回退位
    # 断开（_run_turn 已 close）→ 重连同 sid：同会话续作——transcript 两轮
    # user 事件俱在（挂起保活的进程内证据；行为级=第二轮也正常出终态）
    evs2 = await _run_turn(daemon, sid, "第二轮")
    assert any(e["type"] == "result" for e in evs2)
    init2 = next(e for e in evs2 if e["type"] == "system" and
                 e.get("subtype") == "init")
    assert init2["session_id"] == sid
    ts = (daemon.home / "sessions" / sid / "transcript.jsonl").read_text()
    assert ts.count('"type": "user"') + ts.count('"type":"user"') >= 2


# ---------------------------------------------------------------- 桥
def test_bridge_relays_stdio(tmp_path, daemon):
    """spawn 桥进程：宿主视角 stdout NDJSON（stdin 发 turn，读回事件流）。"""
    env = {**os.environ, "LOADN_HOME": str(daemon.home),
           "LOADN_ENGINE_SOCK": str(daemon.sock),
           "PYTHONPATH": str(REPO)}
    sid = "br1dg3e0-0000-4000-8000-000000000002"
    p = subprocess.Popen(
        [PY, "-m", "loadn", "daemon-bridge"], cwd=tmp_path, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True)
    hello = json.dumps({"auth": daemon.token, "session_id": sid})
    req = json.dumps({"type": "run", "prompt": "经桥跑一轮"})
    p.stdin.write(hello + "\n" + req + "\n")
    p.stdin.flush()
    got = []
    deadline = time.time() + 30
    while time.time() < deadline:
        ln = p.stdout.readline()
        if not ln:
            break
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        got.append(ev)
        if ev.get("type") == "result":
            break
    p.stdin.close()
    p.wait(timeout=10)
    types = [g["type"] for g in got]
    assert "system" in types and "result" in types   # 方言原样过桥
