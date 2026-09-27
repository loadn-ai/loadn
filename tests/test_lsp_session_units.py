"""T6c：lsp_host 的 LspSession 真子进程单测（原 66.5%——会话层只经
diagnose 间接）。Content-Length 帧协议直测：request/notify/reader 分诊/
进程死亡重拉/env 映射解析。stdio MCP server 协议面已由 test_lsp_host
子进程用例覆盖，不重复。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loadn_webui.integrations import lsp_host as lh

_ECHO_LSP = r'''
import json, sys

def read_msg():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line or line in (b"\r\n", b"\n"):
            break
        k, _, v = line.decode().partition(":")
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    return json.loads(sys.stdin.buffer.read(n))

def send(msg):
    data = json.dumps(msg).encode()
    sys.stdout.buffer.write(
        f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
    sys.stdout.buffer.flush()

while True:
    try:
        msg = read_msg()
    except Exception:
        break
    m = msg.get("method")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}})
    elif m == "echo-req":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "echo": msg.get("params", {}).get("text", "")}})
    elif m == "fail-req":
        send({"jsonrpc": "2.0", "id": msg["id"],
              "error": {"code": 1, "message": "LSP 拒绝"}})
    elif m == "notify-in":
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": msg.get("params", {}).get("uri", ""),
                         "diagnostics": [{"range": {"start": {"line": 0}},
                                          "severity": 1, "message": "x"}]}})
    # 其他 notification：静默
'''


def _mk_sess(tmp_path: Path, code: str = _ECHO_LSP) -> lh.LspSession:
    script = tmp_path / "lsp.py"
    script.write_text(code, encoding="utf-8")
    return lh.LspSession([sys.executable, str(script)], "python")


def test_start_initialize_roundtrip(tmp_path):
    s = _mk_sess(tmp_path)
    s.start()                                     # initialize 全帧往返
    assert s.alive()
    s.stop()


def test_request_result_and_error(tmp_path):
    s = _mk_sess(tmp_path)
    s.start()
    assert s.request("echo-req", {"text": "嗨"}) == {"echo": "嗨"}
    with pytest.raises(RuntimeError, match="LSP 拒绝"):
        s.request("fail-req", {})
    s.stop()


def test_reader_routes_notifications_to_queue(tmp_path):
    """server 主动推 notification → reader 分诊进 notes 队列（响应/通知分流）。"""
    s = _mk_sess(tmp_path)
    s.start()
    s.notify("notify-in", {"uri": "file:///x/a.py"})
    msg = s.notes.get(timeout=5)
    assert msg["method"] == "textDocument/publishDiagnostics"
    assert msg["params"]["uri"] == "file:///x/a.py"
    # 同一连接 request 仍正常（分诊不互相干扰）
    assert s.request("echo-req", {"text": "after"}) == {"echo": "after"}
    s.stop()


def test_start_bad_server_raises(tmp_path):
    """server 立即退出 → start 的 initialize 无响应 → TimeoutError。"""
    script = tmp_path / "bad.py"
    script.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    s = lh.LspSession([sys.executable, str(script)], "python")
    with pytest.raises(TimeoutError):
        s.start()
    s.stop()


def test_session_for_respawn_on_death(tmp_path, monkeypatch):
    """_session_for：进程死亡自动重拉（新进程新实例）。"""
    monkeypatch.setattr(lh.shutil, "which", lambda n: "/usr/bin/python3")
    script = tmp_path / "lsp.py"
    script.write_text(_ECHO_LSP, encoding="utf-8")
    lh._sessions.clear()
    s1 = lh._session_for(".py", "python", [sys.executable, str(script)])
    assert s1 is not None and s1.alive()
    s1.stop()
    import time as _t
    _t.sleep(0.3)          # SIGKILL 异步收尾：立即查 alive 仍 True（未 reap）
    s2 = lh._session_for(".py", "python", [sys.executable, str(script)])
    assert s2 is not None and s2 is not s1           # 死亡 → 重拉新实例
    s2.stop()
    lh._sessions.clear()


def test_session_for_not_installed_none(tmp_path, monkeypatch):
    monkeypatch.setattr(lh.shutil, "which", lambda n: None)
    assert lh._session_for(".py", "python",
                           ["definitely-not-there"]) is None
    lh._sessions.clear()


def test_load_servers_env_override(monkeypatch):
    """LOADN_LSP_SERVERS env 覆盖默认映射（部署/测试同通道）。"""
    monkeypatch.setenv("LOADN_LSP_SERVERS",
                       json.dumps({".py": ["pylsp-custom", "--flag"]}))
    m = lh.load_servers()
    assert m[".py"] == ("python", ["pylsp-custom", "--flag"])
    # 未覆盖后缀保留默认
    assert m[".ts"][1][0] == "typescript-language-server"
    # 坏 env 静默回默认
    monkeypatch.setenv("LOADN_LSP_SERVERS", "{broken")
    assert lh.load_servers()[".py"][1] == ["pylask"] or \
        lh.load_servers()[".py"][1] == ["pylsp"]
