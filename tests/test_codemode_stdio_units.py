"""T6d：codemode_mcp stdio server 子进程对赌（原 68.4%——main() 协议
分支从未跑过）+ validate SyntaxError / fs_* 双向越界。

config 门：server 子进程读真 CONFIG（进程内 import 时求值）——
LOADN_WEBUI_HOME 指到带 codemode_enabled: true 的临时根。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loadn_webui import codemode_mcp as cm
from loadn_webui.config import CONFIG


def _mcp_session(calls: list[dict], tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "security:\n  codemode_enabled: true\n", encoding="utf-8")
    env = {**os.environ, "LOADN_WEBUI_HOME": str(tmp_path)}
    lines = "\n".join(json.dumps(c) for c in calls) + "\n"
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui.codemode_mcp"],
        input=lines, capture_output=True, text=True, timeout=30, env=env)
    evs = [json.loads(ln) for ln in p.stdout.splitlines()
           if ln.startswith("{")]
    return evs


def test_stdio_server_protocol(tmp_path):
    evs = _mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "codemode.run",
                    "arguments": {"code": "print('hello')"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "codemode.nothing", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "codemode.run",
                    "arguments": {"code": "import os"}}},   # 越权：拒
    ], tmp_path)
    init = next(e for e in evs if e.get("id") == 1)
    assert init["result"]["serverInfo"]["name"] == "loadn-codemode"
    listing = next(e for e in evs if e.get("id") == 2)
    assert [t["name"] for t in listing["result"]["tools"]] == ["codemode.run"]
    ok = next(e for e in evs if e.get("id") == 3)
    assert ok["result"]["content"][0]["text"].startswith("hello")
    unknown = next(e for e in evs if e.get("id") == 4)
    assert unknown["result"]["isError"]
    denied = next(e for e in evs if e.get("id") == 5)
    assert denied["result"]["isError"] and "拒绝" in \
        denied["result"]["content"][0]["text"]


def test_stdio_gate_off(tmp_path):
    """门关：run 返回「未启用」可读文案（fail-closed 不炸）。"""
    (tmp_path / "config.yaml").write_text("", encoding="utf-8")
    env = {**os.environ, "LOADN_WEBUI_HOME": str(tmp_path)}
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui.codemode_mcp"],
        input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "codemode.run",
                                     "arguments": {"code": "print(1)"}}}) + "\n",
        capture_output=True, text=True, timeout=30, env=env)
    ev = json.loads(p.stdout.splitlines()[0])
    assert "未启用" in ev["result"]["content"][0]["text"]


# ---------------------------------------------------------------- 函数面
def test_validate_syntax_error():
    with pytest.raises(cm.CodemodeError, match="语法错误"):
        cm.validate("def broken(:\n", "s")


def test_fs_read_write_boundary(tmp_path):
    """fs_* 回调双向越界：cwd 外读/写均拒（CodemodeError 可读文案）。"""
    tools = cm._make_tools(tmp_path, "s-b")
    inside = tmp_path / "in.txt"
    inside.write_text("内", encoding="utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("外", encoding="utf-8")
    assert tools["fs_read"]("in.txt") == "内"
    assert "chars" in tools["fs_write"]("out.txt", "写")
    assert (tmp_path / "out.txt").read_text() == "写"
    with pytest.raises(cm.CodemodeError, match="越界"):
        tools["fs_read"]("../outside.txt")
    with pytest.raises(cm.CodemodeError, match="越界"):
        tools["fs_write"]("../escape.txt", "x")


def test_fs_read_size_cap(tmp_path, monkeypatch):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (cm.MAX_FILE_BYTES + 1))
    tools = cm._make_tools(tmp_path, "s-c")
    with pytest.raises(cm.CodemodeError, match="超"):
        tools["fs_read"]("big.bin")


def test_fs_write_size_cap(tmp_path):
    tools = cm._make_tools(tmp_path, "s-d")
    with pytest.raises(cm.CodemodeError, match="超上限"):
        tools["fs_write"]("huge.txt", "y" * (cm.MAX_OUTPUT_CHARS + 1))


def test_run_timeout_audit(tmp_path, monkeypatch):
    """死循环超时：SIGALRM 终止 + anomaly 审计（真跑，不 mock 定时器）。"""
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", True)
    # codemode_mcp 模块级绑定 audit——patch cm.audit 才生效
    anomalies = []
    monkeypatch.setattr(
        cm, "audit",
        lambda t, d, **kw: anomalies.append((t, d)) if t == "anomaly"
        else None)
    with pytest.raises(cm.CodemodeError, match="超时"):
        cm.run("while True:\n    pass", sid="s-t", cwd=tmp_path,
               timeout_s=0.5)
    kinds = [t for t, _ in anomalies]
    assert "anomaly" in kinds and any(
        d.get("what") == "codemode_timeout" for t, d in anomalies)
