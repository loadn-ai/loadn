"""P3-3 LSP 诊断回注验收。

- 诊断主链：fake LSP server（真子进程、真 Content-Length 帧）→
  diagnose 返回「file:行 严重度 source: 消息」格式
- 增量过滤：他文件 uri 的 publishDiagnostics 丢弃；同 uri 才采纳
- 静默降级：后缀无映射 / server 不在 PATH → 空串不抛
- 截断预算：超长诊断 ≤ MAX_CHARS+省略号
- 配置门：lsp_enabled 默认关（工具返回空串）；.mcp.json 注入门
- MCP stdio server E2E：initialize/initialized 回执 + tools/call
- 引擎回注：Edit 成功且 mcp__lsp__diagnostics 在工具表 → 工具结果
  尾部拼 [lsp diagnostics]；工具缺席 → 无尾巴（黑盒边界，静默）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from loadn.tools.base import Tool
from loadn_webui import lsp_host as lh
from loadn_webui.config import CONFIG

# ---------------------------------------------------------------- fake LSP
_FAKE_LSP = r'''
import json, sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "ok"

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

def publish(uri, diags):
    send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
          "params": {"uri": uri, "diagnostics": diags}})

R = {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 5}}

while True:
    try:
        msg = read_msg()
    except Exception:
        break
    m = msg.get("method")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "capabilities": {},
            "serverInfo": {"name": "fake-lsp"}}})
    elif m == "textDocument/didOpen":
        uri = msg["params"]["textDocument"]["uri"]
        if MODE == "stale":
            publish("file:///other/file.py", [          # 他文件：须被丢弃
                {"range": R, "severity": 1, "message": "OTHER FILE"}])
            publish(uri, [
                {"range": R, "severity": 1, "message": "undefined name 'x'"}])
        elif MODE == "huge":
            publish(uri, [{"range": R, "severity": 1,
                           "message": "E" * 5000}])
        else:
            publish(uri, [
                {"range": R, "severity": 1, "message": "undefined name 'x'",
                 "source": "fakepy"},
                {"range": {"start": {"line": 2, "character": 0},
                           "end": {"line": 2, "character": 1}},
                 "severity": 2, "message": "unused variable 'y'"}])
'''


def _fake_server(tmp_path: Path, mode: str = "ok") -> list[str]:
    script = tmp_path / f"fake_lsp_{mode}.py"
    script.write_text(_FAKE_LSP, encoding="utf-8")
    return [sys.executable, str(script), mode]


def _on(monkeypatch, argv: list[str] | None = None) -> None:
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", True)
    monkeypatch.setattr(lh, "SERVERS", {".py": ("python", argv or ["nope"])}
                        if argv else lh.SERVERS)
    lh._sessions.clear()


def _py(tmp_path: Path, name: str = "bad.py") -> Path:
    f = tmp_path / name
    f.write_text("def broken(:\n", encoding="utf-8")
    return f


# ---------------------------------------------------------------- 帧
def test_frame_roundtrip():
    msg = {"jsonrpc": "2.0", "method": "x", "params": {"中文": 1}}
    frame = lh.encode_frame(msg)
    assert frame.startswith(b"Content-Length: ")
    import io
    buf = io.BytesIO(frame)
    assert lh.read_frame(buf) == msg
    assert lh.read_frame(io.BytesIO(b"")) is None        # EOF
    assert lh.read_frame(io.BytesIO(b"Content-Length: zz\r\n\r\n")) is None


# ---------------------------------------------------------------- diagnose
def test_diagnose_via_fake_server(tmp_path, monkeypatch):
    _on(monkeypatch, _fake_server(tmp_path))
    out = lh.diagnose(_py(tmp_path))
    assert "bad.py:1 E fakepy: undefined name 'x'" in out
    assert "bad.py:3 W lsp: unused variable 'y'" in out


def test_other_uri_filtered(tmp_path, monkeypatch):
    """他文件的 publishDiagnostics 丢弃，同 uri 才采纳。"""
    _on(monkeypatch, _fake_server(tmp_path, "stale"))
    out = lh.diagnose(_py(tmp_path))
    assert "undefined name 'x'" in out
    assert "OTHER FILE" not in out


def test_truncation_budget(tmp_path, monkeypatch):
    _on(monkeypatch, _fake_server(tmp_path, "huge"))
    out = lh.diagnose(_py(tmp_path))
    assert len(out) <= lh.MAX_CHARS + 1 and out.endswith("…")


def test_silent_degradations(tmp_path, monkeypatch):
    _on(monkeypatch, argv=["definitely-not-installed-lsp"])
    assert lh.diagnose(_py(tmp_path)) == ""              # PATH 缺席静默
    assert lh.diagnose(tmp_path / "note.md") == ""       # 后缀无映射


def test_missing_file_silent(tmp_path, monkeypatch):
    _on(monkeypatch, _fake_server(tmp_path))
    # 文件不存在：didOpen 前读文本即 OSError → 调用方静默（工具层兜）
    try:
        out = lh.diagnose(tmp_path / "ghost.py")
        assert out == ""
    except OSError:
        pass                                             # 两种形态都可接受


def test_config_gate_off(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", False)
    out = lh.tool_diagnostics({"file_path": str(_py(tmp_path))}, "s1")
    assert out == ""                                     # 门关：空串（静默）


# ---------------------------------------------------------------- 注入门
def test_mcp_json_injection_gate(tmp_path, monkeypatch):
    from loadn_webui import workspace as ws_mod
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", True)
    monkeypatch.setattr(CONFIG.security, "codemode_enabled", False)
    monkeypatch.setattr(CONFIG.resources, "cdp_url", "")
    monkeypatch.setattr(CONFIG, "mcp", type("M", (), {"servers": {}})())
    ws_mod.write_mcp_json(tmp_path, None)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["mcpServers"]["lsp"]["args"] == ["_lsp-mcp"]
    assert "lsp" in json.loads((tmp_path / ".mcp-lock.json").read_text())
    # 门关：不注入（merge 空 → 落盘文件移除）
    monkeypatch.setattr(CONFIG.security, "lsp_enabled", False)
    ws_mod.write_mcp_json(tmp_path, None)
    assert not (tmp_path / ".mcp.json").exists()


# ---------------------------------------------------------------- MCP E2E
def test_mcp_stdio_server(tmp_path):
    """子进程真 server：协议回执 + tools/call 拿到 fake 诊断。"""
    (tmp_path / "config.yaml").write_text(
        "security:\n  lsp_enabled: true\n", encoding="utf-8")
    import os
    env = {**os.environ,
           "LOADN_WEBUI_HOME": str(tmp_path),
           "LOADN_LSP_SERVERS": json.dumps(
               {".py": _fake_server(tmp_path)})}
    target = _py(tmp_path, "e2e.py")
    calls = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "lsp.diagnostics",
                    "arguments": {"file_path": str(target)}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "lsp.nothing", "arguments": {}}},
    ]
    p = subprocess.run(
        [sys.executable, "-m", "loadn_webui.lsp_host"],
        input="\n".join(json.dumps(c) for c in calls) + "\n",
        capture_output=True, text=True, timeout=90, env=env)
    evs = [json.loads(ln) for ln in p.stdout.splitlines()
           if ln.startswith("{")]
    init = next(e for e in evs if e.get("id") == 1)
    assert init["result"]["serverInfo"]["name"] == "loadn-lsp"
    listing = next(e for e in evs if e.get("id") == 2)
    assert [t["name"] for t in listing["result"]["tools"]] == \
        ["lsp.diagnostics"]
    diag = next(e for e in evs if e.get("id") == 3)
    text = diag["result"]["content"][0]["text"]
    assert "e2e.py:1 E fakepy: undefined name" in text
    unknown = next(e for e in evs if e.get("id") == 4)
    assert unknown["result"]["isError"]


# ---------------------------------------------------------------- 引擎回注
class _FakeDiag(Tool):
    """假 lsp MCP 工具（引擎只见 mcp__lsp__diagnostics 名字——黑盒）。"""
    name = "mcp__lsp__diagnostics"
    description = "fake diagnostics"
    input_schema: dict = {"type": "object"}
    timeout_s = 20
    seen: list[dict] = []

    async def execute(self, args, ctx):
        _FakeDiag.seen.append(dict(args))
        return f"{Path(args['file_path']).name}:1 E fake: boom"


async def test_engine_appends_diag_tail(tmp_path, monkeypatch):
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool

    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    f = tmp_path / "app.py"
    f.write_text("v = 1\n", encoding="utf-8")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Read", {"file_path": str(f)}),
        H.tool_round("t2", "Edit", {"file_path": str(f),
                                     "old_string": "v = 1",
                                     "new_string": "v = 2"}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 40, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider,
                     tools={"Read": ReadTool(), "Edit": EditTool(),
                            "mcp__lsp__diagnostics": _FakeDiag()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=6))
    await core.run_turn("改文件")
    # 诊断查询被调用且带对了文件
    assert any(a["file_path"].endswith("app.py") for a in _FakeDiag.seen)
    # 工具结果尾部有诊断段
    ts = (tmp_path / "home" / "sessions" / session.session_id
          / "transcript.jsonl").read_text()
    assert "[lsp diagnostics]" in ts and "boom" in ts


async def test_engine_silent_without_diag_tool(tmp_path, monkeypatch):
    """工具表没有 lsp 工具 → 结果干净无尾巴（缺席静默）。"""
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool

    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    f = tmp_path / "app2.py"
    f.write_text("v = 1\n", encoding="utf-8")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Read", {"file_path": str(f)}),
        H.tool_round("t2", "Edit", {"file_path": str(f),
                                     "old_string": "v = 1",
                                     "new_string": "v = 2"}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 40, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider,
                     tools={"Read": ReadTool(), "Edit": EditTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=6))
    await core.run_turn("改文件")
    ts = (tmp_path / "home" / "sessions" / session.session_id
          / "transcript.jsonl").read_text()
    assert "[lsp diagnostics]" not in ts
