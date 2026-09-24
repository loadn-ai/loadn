"""LSP 诊断回注（P3-3，opencode D2 同构）：平台侧 MCP server。

目标：Edit 后无需跑全量测试即知**类型/语法错增量**。黑盒边界——
引擎不直接管 LSP 进程，只经 MCP 工具 `lsp.diagnostics` 查询：

    平台 lsp_host（懒启动 pylsp/typescript-language-server…）
      └─ LSP stdio（Content-Length 帧）→ publishDiagnostics
    引擎 loop._run_tool：Edit/Write/MultiEdit 成功后调
    mcp__lsp__diagnostics，结果拼进工具结果尾部（截断预算内）

- 懒启动：首次查询某后缀才 spawn 对应 server（闲置零进程）
- 诊断增量按文件过滤（publishDiagnostics uri 匹配；不相关通知丢弃）
- 静默降级：后缀无映射 / server 不在 PATH / 进程死亡 → 空串不报错
- server 映射可配：env `LOADN_LSP_SERVERS`（json：{".py": ["pylsp"]}）
  ——测试与部署同通道（瘦环境可不装 pylsp 而指向别的实现）
- 默认关：config security.lsp_enabled（注入条件同 codemode）
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .audit import audit
from .config import CONFIG

MAX_CHARS = 2_000             # 诊断文本预算（工具结果尾部拼接）
MAX_DIAGS = 20                # 单文件诊断条数上限
MAX_OPEN_BYTES = 512 * 1024   # didOpen 文本上限（超长文件截尾）
DIAG_WAIT_S = 10.0            # publishDiagnostics 等待上限
INIT_TIMEOUT_S = 15.0

# 后缀 → (languageId, server argv)。缺 server = 静默降级（不装不炸）
SERVERS: dict[str, tuple[str, list[str]]] = {
    ".py": ("python", ["pylsp"]),
    ".ts": ("typescript", ["typescript-language-server", "--stdio"]),
    ".tsx": ("typescriptreact", ["typescript-language-server", "--stdio"]),
    ".js": ("javascript", ["typescript-language-server", "--stdio"]),
    ".jsx": ("javascriptreact", ["typescript-language-server", "--stdio"]),
}

_SEVERITY = {1: "E", 2: "W", 3: "I", 4: "H"}


def load_servers() -> dict[str, tuple[str, list[str]]]:
    """默认映射 + env 覆盖（LOADN_LSP_SERVERS：{后缀: argv 列表}）。"""
    merged = dict(SERVERS)
    raw = os.environ.get("LOADN_LSP_SERVERS") or ""
    if raw:
        try:
            data = json.loads(raw)
            for suf, argv in (data or {}).items():
                if isinstance(argv, list) and argv:
                    lang = (SERVERS.get(suf) or (suf.lstrip("."), None))[0]
                    merged[suf] = (lang, [str(a) for a in argv])
        except (ValueError, TypeError):
            pass
    return merged


# ---------------------------------------------------------------- LSP 帧
def encode_frame(msg: dict) -> bytes:
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def read_frame(stream) -> dict | None:
    """从 stdin 类流读一帧（headers + body）；EOF/坏帧 → None。"""
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        k, _, v = line.decode("ascii", errors="replace").partition(":")
        headers[k.strip().lower()] = v.strip()
    try:
        n = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    body = stream.read(n)
    if len(body) < n:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


class LspSession:
    """一个语言 server 子进程的会话（reader 线程分诊响应/通知）。"""

    def __init__(self, argv: list[str], lang: str) -> None:
        self.argv = argv
        self.lang = lang
        self.proc: subprocess.Popen | None = None
        self._rid = 0
        self._lock = threading.Lock()
        self._pending: dict[int, dict] = {}           # id → response
        self._events: dict[int, threading.Event] = {}
        self.notes: queue.Queue = queue.Queue()       # server → 通知流
        self._write_lock = threading.Lock()

    def start(self) -> None:
        self.proc = subprocess.Popen(
            self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        threading.Thread(target=self._reader, daemon=True,
                         name=f"lsp-reader-{self.lang}").start()
        root = Path(os.getcwd()).as_uri()
        self.request("initialize", {
            "processId": os.getpid(), "rootUri": root,
            "capabilities": {"textDocument": {"sync": 1}},   # full 同步
            "workspaceFolders": [{"uri": root, "name": "root"}]})
        self.notify("initialized", {})

    def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            msg = read_frame(self.proc.stdout)
            if msg is None:
                break
            rid = msg.get("id")
            if rid is not None and ("result" in msg or "error" in msg):
                with self._lock:
                    self._pending[rid] = msg
                    ev = self._events.pop(rid, None)
                if ev is not None:
                    ev.set()
            else:
                self.notes.put(msg)

    def _send(self, msg: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise ConnectionError("lsp server 未运行")
        with self._write_lock:
            self.proc.stdin.write(encode_frame(msg))
            self.proc.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._rid += 1
            rid = self._rid
            ev = threading.Event()
            self._events[rid] = ev
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params})
        if not ev.wait(INIT_TIMEOUT_S):
            raise TimeoutError(f"lsp {method} 响应超时")
        with self._lock:
            resp = self._pending.pop(rid, {})
        if "error" in resp:
            raise RuntimeError(f"lsp {method}: {resp['error']}")
        return resp.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass


# ---------------------------------------------------------------- 诊断
_sessions: dict[str, LspSession] = {}


def _session_for(suffix: str, lang: str, argv: list[str]) -> LspSession | None:
    """懒启动 + 存活复查（死了重拉）。server 缺席/起不来 → None。"""
    sess = _sessions.get(suffix)
    if sess is not None and sess.alive():
        return sess
    if sess is not None:
        sess.stop()
        _sessions.pop(suffix, None)
    if shutil.which(argv[0]) is None:
        return None                                   # 未安装：静默降级
    sess = LspSession(argv, lang)
    try:
        sess.start()
    except (OSError, TimeoutError, RuntimeError, ConnectionError):
        sess.stop()
        return None
    audit("lsp_diag", {"action": "spawn", "server": argv[0],
                       "language": lang})
    _sessions[suffix] = sess
    return sess


def diagnose(path: Path) -> str:
    """查询一个文件的诊断。空串=无诊断或不可用（调用方静默）。"""
    entry = load_servers().get(path.suffix)
    if entry is None:
        return ""
    lang, argv = entry
    sess = _session_for(path.suffix, lang, argv)
    if sess is None:
        return ""
    uri = path.resolve().as_uri()
    # 清掉同 uri 陈旧通知（上一轮 publish 不许冒充本轮增量）
    while not sess.notes.empty():
        try:
            sess.notes.get_nowait()
        except queue.Empty:
            break
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_OPEN_BYTES:
        text = text[:MAX_OPEN_BYTES]
    # didClose→didOpen 恒定单路径（重复 didOpen 协议非法；server 对未开
    # 文件的 didClose 是 no-op）
    sess.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    sess.notify("textDocument/didOpen", {
        "textDocument": {"uri": uri, "languageId": lang,
                         "version": 1, "text": text}})
    import time as _t
    deadline = _t.monotonic() + DIAG_WAIT_S
    while _t.monotonic() < deadline:
        try:
            msg = sess.notes.get(timeout=max(0.1, deadline - _t.monotonic()))
        except queue.Empty:
            break
        if msg.get("method") != "textDocument/publishDiagnostics":
            continue
        params = msg.get("params") or {}
        if params.get("uri") != uri:
            continue                                  # 他文件的通知：丢弃
        return _format(path, params.get("diagnostics") or [])
    return ""                                         # 超时：按无诊断


def _format(path: Path, diags: list[dict]) -> str:
    lines = []
    for d in diags[:MAX_DIAGS]:
        ln = ((d.get("range") or {}).get("start") or {}).get("line", 0) + 1
        sev = _SEVERITY.get(d.get("severity"), "?")
        src = d.get("source") or "lsp"
        lines.append(f"{path.name}:{ln} {sev} {src}: {d.get('message', '')}"
                     .rstrip())
    out = "\n".join(lines)
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS] + "…"
    return out


# ---------------------------------------------------------------- MCP server
def tool_diagnostics(args: dict, sid: str) -> str:
    if not CONFIG.security.lsp_enabled:
        return ""            # 引擎侧静默（注入门已在 write_mcp_json）
    raw = str(args.get("file_path") or "").strip()
    if not raw:
        return "缺少 file_path"
    p = Path(raw)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    try:
        return diagnose(p)
    except OSError:
        return ""           # 文件读不了：静默（诊断是补充信息）


TOOLS = {
    "diagnostics": ("lsp.diagnostics", tool_diagnostics,
                    "LSP 诊断查询：返回单文件的类型/语法诊断（pylsp 等，"
                    "懒启动；无 server 时空结果）。编辑后增量检查用",
                    {"file_path": {"type": "string",
                                   "description": "目标文件（绝对或相对 cwd）"}}),
}


def main() -> int:
    """stdio MCP server（与 loadn/mcp/client.py 对话）。"""
    def send(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "loadn-lsp", "version": "0.1.0"}}})
        elif method == "notifications/initialized":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": v[0], "description": v[2],
                 "inputSchema": {"type": "object", "properties": v[3]}}
                for v in TOOLS.values()]}})
        elif method == "tools/call":
            name = (req.get("params") or {}).get("name") or ""
            args = (req.get("params") or {}).get("arguments") or {}
            sid = str((req.get("params") or {}).get("_sid")
                      or os.environ.get("LOADN_SESSION_ID") or "anon")
            if name != "lsp.diagnostics":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"未知工具 {name}"}]}})
                continue
            text = tool_diagnostics(args, sid)
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}]}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
