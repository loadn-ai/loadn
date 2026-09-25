"""codemode 受限执行域（P3-4，opencode codemode 同构）：MCP server 形态。

确定性多步操作（批量改名/数据转换）由一小段代码在**受限解释器**完成，
省 N 轮 Edit 工具调用。安全模型：
- **AST 白名单**（自实现，零依赖——RestrictedPython 是新依赖，红线）：
  仅允许表达式/赋值/循环/分支/函数定义/return/容器操作；**禁** import/
  eval/exec/open/属性下划线访问/dunder；白名单 builtins（len/range/
  sorted/str/int/float/list/dict/set/tuple/min/max/sum/abs/enumerate/
  zip/round/any/all/reversed）。
- **资源限**：限时（默认 2s，信号 SIGALRM 墙钟）+ 输出尺寸上限；
  无网络无文件 IO——读写宿主文件的唯一通道是注入的白名单工具回调
  （fs_read/fs_write：cwd 限定，尺寸限），工具调用也计入时限。
- 审计：每次 run 记 codemode_run（代码全文+耗时+结果摘要）；越权 AST
  节点拒绝即记 codemode_blocked 安全事件（进 anomaly 类）。
- 默认关：config security.codemode_enabled（settings 可开）；MCP
  注入条件同开关。
"""
from __future__ import annotations

import ast
import json
import os
import signal
import sys
from pathlib import Path

from .audit import audit
from .config import CONFIG

TIMEOUT_S = 2.0
MAX_OUTPUT_CHARS = 20_000
MAX_FILE_BYTES = 512 * 1024

# AST 节点白名单（表达式全集+流程+容器+函数定义）
_ALLOWED_NODES = (
    ast.Expression, ast.Module,
    ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.For, ast.While, ast.If, ast.IfExp,
    ast.FunctionDef, ast.Return,
    ast.Break, ast.Continue, ast.Pass,
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Call, ast.Attribute, ast.Name, ast.Constant, ast.JoinedStr, ast.FormattedValue,
    ast.List, ast.Tuple, ast.Dict, ast.Set,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension, ast.Slice, ast.Subscript, ast.Starred, ast.NamedExpr,
    ast.Lambda, ast.arg, ast.arguments, ast.keyword,
    ast.Expr, ast.Global, ast.Nonlocal,
    # 形态子节点（非语义原语）：ctx 载体 / 运算符枚举 / f-string 件
    ast.Load, ast.Store, ast.Del,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.LShift, ast.RShift, ast.BitOr, ast.BitXor, ast.BitAnd,
    ast.UAdd, ast.USub, ast.Not, ast.Invert, ast.And, ast.Or,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
)
# 显式禁（即使属上面形状之外的兜底）
_FORBIDDEN_CALL_NAMES = {"eval", "exec", "compile", "open", "__import__",
                         "input", "breakpoint", "vars", "dir", "globals",
                         "locals"}
_SAFE_BUILTINS = {
    "len": len, "range": range, "sorted": sorted, "str": str, "int": int,
    "float": float, "list": list, "dict": dict, "set": set, "tuple": tuple,
    "min": min, "max": max, "sum": sum, "abs": abs, "enumerate": enumerate,
    "zip": zip, "round": round, "any": any, "all": all,
    "reversed": reversed, "bool": bool, "isinstance": isinstance,
    "True": True, "False": False, "None": None,
}


class CodemodeError(Exception):
    """受限域错误（agent 可读）。"""


def validate(source: str, sid: str) -> ast.Module:
    """AST 白名单校验；越权记安全事件。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise CodemodeError(f"语法错误：{e}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise CodemodeError(
                f"越权语法 {type(node).__name__}（codemode 白名单外——"
                "import/eval/文件 IO 均不可用）")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise CodemodeError(f"越权属性访问 _{node.attr}（私有/dunder 禁）")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise CodemodeError("import 被禁（codemode 无模块系统）")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_CALL_NAMES:
            raise CodemodeError(f"{node.id} 被禁")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in _FORBIDDEN_CALL_NAMES:
            raise CodemodeError(f"{node.func.id}() 被禁")
    return tree


class _Timeout(Exception):
    pass


def _alarm(sig, frame):
    raise _Timeout()


def _make_tools(cwd: Path, sid: str) -> dict:
    """白名单工具回调（文件读写唯一通道——cwd 限定+尺寸限+审计）。"""
    def fs_read(path: str) -> str:
        p = (cwd / path).resolve()
        if not str(p).startswith(str(cwd.resolve())):
            raise CodemodeError(f"fs_read 越界：{path}（限 cwd 内）")
        if p.stat().st_size > MAX_FILE_BYTES:
            raise CodemodeError("文件超读取上限")
        audit("codemode_run", {"action": "fs_read", "sid": sid, "path": path})
        return p.read_text(encoding="utf-8", errors="replace")

    def fs_write(path: str, content: str) -> str:
        p = (cwd / path).resolve()
        if not str(p).startswith(str(cwd.resolve())):
            raise CodemodeError(f"fs_write 越界：{path}（限 cwd 内）")
        if len(content) > MAX_OUTPUT_CHARS:
            raise CodemodeError("写入内容超上限")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        audit("codemode_run", {"action": "fs_write", "sid": sid,
                               "path": path, "chars": len(content)})
        return f"wrote {len(content)} chars"

    return {"fs_read": fs_read, "fs_write": fs_write}


def run(source: str, sid: str = "", cwd: Path | None = None,
        timeout_s: float = TIMEOUT_S) -> str:
    """编译执行（validate → exec 白名单 globals → 结果摘要）。

    返回值约定：脚本最后表达式/print 收集进 result；fs_* 调用走工具
    回调。任何异常→CodemodeError（agent 可读），_Timeout→超限文案。
    """
    tree = validate(source, sid)
    cwd = cwd or Path(".")
    import time
    t0 = time.perf_counter()

    captured: list[str] = []

    def _print(*args):
        captured.append(" ".join(str(a) for a in args))
        if sum(len(c) for c in captured) > MAX_OUTPUT_CHARS:
            raise CodemodeError("输出超上限（print 过量）")

    g: dict = {"__builtins__": {}, **_SAFE_BUILTINS,
               "print": _print, **_make_tools(cwd, sid or "anon")}
    code = compile(tree, "<codemode>", "exec")
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        exec(code, g)                                  # noqa: S102 —— 白名单 AST
    except _Timeout:
        audit("anomaly", {"what": "codemode_timeout", "sid": sid,
                          "code": source[:400]})
        raise CodemodeError(f"超时 {timeout_s}s 已终止（循环过大？）") from None
    except CodemodeError:
        raise
    except Exception as e:                              # noqa: BLE001
        raise CodemodeError(f"运行错误：{type(e).__name__}: {e}") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    out = "\n".join(captured)
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + "…（截断）"
    dt = (time.perf_counter() - t0) * 1000
    audit("codemode_run", {"sid": sid, "ms": round(dt),
                           "code": source[:1000],
                           "output_chars": len(out)})
    return out or "(no output)"


# ---------------------------------------------------------------- MCP server
def tool_run(args: dict, sid: str) -> str:
    if not CONFIG.security.codemode_enabled:
        return ("codemode 未启用（config security.codemode_enabled: true 开启；"
                "默认关——受限执行域是显式选择）")
    try:
        return run(str(args.get("code") or ""), sid=sid)
    except CodemodeError as e:
        audit("anomaly", {"what": "codemode_blocked", "sid": sid,
                          "reason": str(e)[:200]})
        return f"拒绝：{e}"


TOOLS = {
    "run": ("codemode.run", tool_run,
            "受限 Python 执行域：AST 白名单（无 import/eval/文件 IO——读写仅"
            "经 fs_read/fs_write 回调，限 cwd），限时 2s。批量确定性操作"
            "（改名/转换）首选，省逐文件 Edit 的轮次",
            {"code": {"type": "string",
                      "description": "Python 片段（print 输出即结果）"}}),
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
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "loadn-codemode", "version": "0.1.0"}}})
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
            if name != "codemode.run":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"未知工具 {name}"}]}})
                continue
            try:
                # T6 修（真 bug）：此前直调 run() 绕过 codemode_enabled
                # 配置门——tool_run 才是带门的入口（进程内消费方同语义）。
                # tool_run 把 CodemodeError 吞成「拒绝：…」文本（引擎侧
                # 静默语义）——stdio 面据此标 isError（agent 可读错误）
                text = tool_run({"code": str(args.get("code") or "")}, sid)
                err = text.startswith("拒绝：")
            except CodemodeError as e:
                text, err = f"拒绝：{e}", True
            send({"jsonrpc": "2.0", "id": rid, "result": {
                **({"isError": True} if err else {}),
                "content": [{"type": "text", "text": text}]}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
