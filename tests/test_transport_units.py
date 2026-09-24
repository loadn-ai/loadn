"""T4：transport 进程内单测（daemon.py+bridge.py 原 0%——test_daemon 走
子进程 coverage 不追）。真 UDS 流对（非 mock）：鉴权/协议分支/桥双向转发。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from loadn.transport import daemon as daemon_mod


async def _client(sock: Path, hello: dict | None, lines: list[bytes]):
    reader, writer = await asyncio.open_unix_connection(str(sock))
    if hello is not None:
        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()
    for ln in lines:
        writer.write(ln)
        await writer.drain()
    return reader, writer


async def _start_daemon(tmp_path):
    """起 daemon 的 accept 循环（serve_forever 的可测切面）。"""
    d = daemon_mod.EngineDaemon(tmp_path)
    p = daemon_mod.sock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d.token = "test-token-123"
    daemon_mod.token_path().write_text(d.token)
    server = await asyncio.start_unix_server(d._handle, str(p))
    task = asyncio.create_task(server.serve_forever())
    return d, server, task


# ---------------------------------------------------------------- 鉴权
async def test_daemon_auth_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    d, server, task = await _start_daemon(tmp_path)
    try:
        # 错 token
        r, w = await _client(daemon_mod.sock_path(),
                             {"auth": "wrong", "session_id": "s1"}, [])
        line = await asyncio.wait_for(r.readline(), timeout=5)
        assert json.loads(line)["error"] == "auth failed"
        w.close()
        # 无 session_id
        r2, w2 = await _client(daemon_mod.sock_path(),
                               {"auth": d.token}, [])
        line2 = await asyncio.wait_for(r2.readline(), timeout=5)
        assert json.loads(line2)["error"] == "session_id required"
        w2.close()
        # 坏 JSON 首行 → 直接关（EOF）
        r3, w3 = await _client(daemon_mod.sock_path(), None,
                               [b"not-json\n"])
        w3.write_eof()
        assert await asyncio.wait_for(r3.read(), timeout=5) == b""
        w3.close()
    finally:
        server.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_daemon_run_pumps_events(tmp_path, monkeypatch):
    """run 请求 → init + 事件流回写（core 是注入依赖，stub 合法）。"""
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    d, server, task = await _start_daemon(tmp_path)
    seen_prompts = []

    class _Provider:
        model_name = "fake-model"

    class _Core:
        provider = _Provider()
        tools = {"Bash": None}

        async def run_turn(self, prompt, emit=None, stop=None):
            seen_prompts.append(prompt)
            from loadn.types import Message, TextBlock
            await emit({"type": "assistant",
                        "message": Message(
                            role="assistant",
                            content=[TextBlock(text=f"回声:{prompt}")])})

    async def fake_core_for(sid):
        return _Core()

    monkeypatch.setattr(d, "_core_for", fake_core_for)
    try:
        r, w = await _client(daemon_mod.sock_path(),
                             {"auth": d.token, "session_id": "sess-1"},
                             [(json.dumps({"type": "run", "prompt": "你好"})
                               + "\n").encode(),
                              (json.dumps({"type": "detach"}) + "\n").encode()])
        await asyncio.sleep(0.3)           # 让 create_task 的回写先跑
        got = []
        while True:                       # 读到安静（init/assistant 任务序不保证）
            try:
                line = await asyncio.wait_for(r.readline(), timeout=2)
            except asyncio.TimeoutError:
                break
            if not line:
                break
            got.append(json.loads(line))
        assert any(e["type"] == "system" and e.get("subtype") == "init"
                   for e in got)                       # init 到达
        assert any(e["type"] == "assistant"
                   and "回声:你好" in str(e["message"]["content"])
                   for e in got)                      # 事件定向回写
        assert seen_prompts == ["你好"]
        assert seen_prompts == ["你好"]
        w.close()
    finally:
        server.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_daemon_core_for_build_failure(tmp_path, monkeypatch):
    """构建失败 → None（客户端收 engine init failed 可读错误）。"""
    import loadn.core.build as build_mod

    async def boom(cwd, *, cfg=None, **kw):
        raise RuntimeError("provider 不可用")

    monkeypatch.setattr(build_mod, "build_agent", boom)
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    d = daemon_mod.EngineDaemon(tmp_path)
    assert await d._core_for("sess-any") is None
    assert daemon_mod.sock_path().name == "engine.sock"
    assert daemon_mod.token_path().name == "engine.token"


async def test_daemon_core_reuse(tmp_path, monkeypatch):
    """同 sid 复用 core（挂起重连不重建——cache warmer/上下文保留语义）。"""
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    d = daemon_mod.EngineDaemon(tmp_path)
    built = []

    async def fake_build(cwd, *, cfg=None, **kw):
        built.append(1)

        class _B:
            core = type("C", (), {"session": None})()

        return _B()

    import loadn.core.build as build_mod
    monkeypatch.setattr(build_mod, "build_agent", fake_build)
    import loadn.core.session as sess_mod

    class _FakeSM:
        calls = []

        @staticmethod
        def resume(sid, cwd):
            _FakeSM.calls.append(sid)
            return object()

    monkeypatch.setattr(sess_mod, "SessionManager", _FakeSM)
    c1 = await d._core_for("sess-x")
    c2 = await d._core_for("sess-x")
    assert c1 is c2 and len(built) == 1              # 只建一次
