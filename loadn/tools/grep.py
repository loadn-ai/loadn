"""Grep 工具——正则内容检索，rg 优先、纯 Python 递归扫描兜底。

有 rg 就 shell out（--no-heading -n 透传 -i/-g/--multiline）；环境里没有
rg（瘦容器/Windows）时用 os.walk 扫描兜底，排除 .git/node_modules/隐藏
目录。命中超 GREP_MAX_HITS 截断并提示收窄——防一次搜索刷爆上下文。
"""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import shutil
import subprocess
from pathlib import Path

from loadn.constants import GREP_MAX_HITS, READ_FILE_MAX_BYTES, STREAM_LINE_MAX
from loadn.supervisor.process import kill_process_group
from loadn.tools.base import Tool, ToolContext, ToolError

_RG_TIMEOUT_S = 30.0          # rg 子进程墙钟上限（兜底扫描是纯内存操作不设限）
_SCAN_BINARY_SNIFF = 4096     # 前若干字节含 NUL 视为二进制跳过


_CAPABILITY: dict | None = None      # 会话级缓存（P3-8：不每次 which）


def capability() -> dict:
    """搜索能力探测（pi/zcode capability 同构，会话缓存）。

    {backend: "rg"|"fallback", rg: 路径|None, version: str|None}。
    探测失败（无 rg）记一条审计——环境降级是部署信号，值得看见。
    """
    global _CAPABILITY
    if _CAPABILITY is not None:
        return _CAPABILITY
    rg = shutil.which("rg")
    version = None
    if rg:
        try:
            out = subprocess.run([rg, "--version"], capture_output=True,
                                 text=True, timeout=5)
            version = out.stdout.splitlines()[0].split()[-1]                 if out.returncode == 0 and out.stdout else None
        except (OSError, subprocess.SubprocessError, IndexError):
            version = None
    _CAPABILITY = {"backend": "rg" if rg else "fallback",
                   "rg": rg, "version": version}
    if not rg:
        from loadn.util import get_logger
        get_logger(__name__).warning(
            "rg 不可用——Grep 走纯 Python 扫描兜底（速度慢，rg -g/--multiline"
            " 参数语义降级：glob 仍生效、multiline 逐行近似）")
    return _CAPABILITY


def _rg_binary() -> str | None:
    """rg 可执行文件路径（capability 缓存的便捷口；测试可 monkeypatch）。"""
    return capability()["rg"]


def _glob_match(fp: Path, root: Path, pattern: str) -> bool:
    """glob 过滤：basename 或相对路径 fnmatch（`*.py`/`**/*.py` 均命中任意深度）。"""
    try:
        rel = fp.relative_to(root).as_posix()
    except ValueError:
        rel = fp.as_posix()
    pats = [pattern]
    if pattern.startswith("**/"):
        pats.append(pattern[3:])          # **/*.py 也要命中根级 a.py
    else:
        pats.append("**/" + pattern)      # *.py 也要命中子目录文件
    return any(fnmatch.fnmatch(fp.name, p) or fnmatch.fnmatch(rel, p)
               for p in pats)


def _pruned_dirs(dirnames: list[str]) -> None:
    """原地剪掉 .git/node_modules/隐藏目录（os.walk 的 dirnames 就地改写）。"""
    dirnames[:] = [d for d in dirnames
                   if not d.startswith(".") and d != "node_modules"]


class GrepTool(Tool):
    """正则检索文件内容（content / files_with_matches / count 三种输出）。"""

    name = "Grep"

    @property
    def description(self) -> str:
        cap = capability()
        base = ("在文件内容里搜正则 pattern。output_mode：content（默认，带"
                "行号）、files_with_matches（只要文件名）、count（每文件"
                "命中数）。glob 过滤文件（如 \"*.py\"），ignore_case 忽略"
                "大小写。大仓库优先用本工具而不是 Bash+cat。")
        if cap["backend"] == "rg":
            return base
        # 降级声明（P3-8）：模型知道当前能力，少发无效调用
        return (base + "【当前环境 ripgrep 不可用，已回退纯 Python 扫描："
                "大仓库会慢；--multiline 走逐行近似（跨行 pattern 可能漏）；"
                "glob 过滤语义保持一致】")
    input_schema: dict = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式（Rust/Python 兼容子集）"},
            "path": {"type": "string", "description": "检索根目录或单文件（默认 cwd）"},
            "glob": {"type": "string", "description": "文件名过滤（rg -g 语义，如 \"*.py\"）"},
            "ignore_case": {"type": "boolean", "description": "忽略大小写（-i）"},
            "multiline": {"type": "boolean", "description": "跨行匹配（--multiline）"},
            "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
        },
        "required": ["pattern"],
    }
    read_only = True

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        pattern = args.get("pattern")
        if not pattern or not isinstance(pattern, str):
            raise ToolError("缺少必填参数 pattern")
        output_mode = args.get("output_mode") or "content"
        if output_mode not in ("content", "files_with_matches", "count"):
            raise ToolError(
                f"output_mode 需为 content|files_with_matches|count（收到 {output_mode!r}）")
        root = Path(args.get("path") or str(ctx.cwd))
        if not root.exists():
            raise ToolError(f"path 不存在：{root}")
        glob_ = args.get("glob")
        ignore_case = bool(args.get("ignore_case"))
        multiline = bool(args.get("multiline"))
        try:
            re.compile(pattern,
                       (re.IGNORECASE if ignore_case else 0)
                       | ((re.MULTILINE | re.DOTALL) if multiline else 0))
        except re.error as e:
            raise ToolError(f"正则无效：{e}") from None
        if _rg_binary():
            lines = await self._via_rg(_rg_binary(), pattern, str(root), glob_,
                                       ignore_case, multiline, output_mode)
        else:
            lines = await self._py_scan(pattern, root, glob_, ignore_case,
                                        multiline, output_mode)
        if not lines:
            return "（无命中）"
        if len(lines) > GREP_MAX_HITS:
            rest = len(lines) - GREP_MAX_HITS
            lines = lines[:GREP_MAX_HITS]
            lines.append(f"…还有 {rest} 条命中，建议收窄 pattern 或 path")
        return "\n".join(lines)

    # ---------------------------------------------------------------- rg 后端
    async def _via_rg(self, rg_bin: str, pattern: str, root: str,
                      glob_: str | None, ignore_case: bool, multiline: bool,
                      output_mode: str) -> list[str]:
        cmd = [rg_bin, "--no-heading"]
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        else:
            cmd.append("-n")
        if ignore_case:
            cmd.append("-i")
        if glob_:
            cmd.extend(["-g", glob_])
        if multiline:
            cmd.append("--multiline")
        cmd += ["--", pattern, root]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=STREAM_LINE_MAX)
        try:
            out, err = await asyncio.wait_for(proc.communicate(),
                                              timeout=_RG_TIMEOUT_S)
        except asyncio.TimeoutError:
            await kill_process_group(proc)
            raise ToolError(f"Grep（rg）超时 {_RG_TIMEOUT_S:.0f}s 已终止；"
                            "建议收窄 path 或加 glob 过滤") from None
        if proc.returncode == 2:      # 0=有命中 1=无命中 2=错误
            raise ToolError(f"rg 报错：{err.decode('utf-8', errors='replace')[:300]}")
        return out.decode("utf-8", errors="replace").splitlines()

    # ---------------------------------------------------------------- Python 兜底
    async def _py_scan(self, pattern: str, root: Path, glob_: str | None,
                       ignore_case: bool, multiline: bool,
                       output_mode: str) -> list[str]:
        flags = (re.IGNORECASE if ignore_case else 0)
        if multiline:
            flags |= re.MULTILINE | re.DOTALL
        regex = re.compile(pattern, flags)
        files: list[Path]
        if root.is_file():
            files = [root]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(root):
                _pruned_dirs(dirnames)
                files.extend(Path(dirpath) / fn for fn in filenames)
        hits: list[str] = []
        counts: dict[str, int] = {}
        for fp in files:
            if glob_ and not _glob_match(fp, root, glob_):
                continue
            try:
                if fp.stat().st_size > READ_FILE_MAX_BYTES:
                    continue
                text = fp.read_bytes().decode("utf-8", errors="ignore")
            except OSError:
                continue
            if "\x00" in text[:_SCAN_BINARY_SNIFF]:
                continue                       # 二进制文件跳过
            if multiline:
                found = 0
                for m in regex.finditer(text):
                    found += 1
                    if output_mode == "content":
                        ln = text.count("\n", 0, m.start()) + 1
                        frag = m.group(0)
                        if len(frag) > 200:
                            frag = frag[:200] + "…"
                        hits.append(f"{fp}:{ln}:{frag}")
            else:
                found = 0
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        found += 1
                        if output_mode == "content":
                            hits.append(f"{fp}:{i}:{line.rstrip()}")
            if found:
                counts[str(fp)] = found
        if output_mode == "files_with_matches":
            return list(counts.keys())
        if output_mode == "count":
            return [f"{fp}:{n}" for fp, n in counts.items()]
        return hits


tool = GrepTool()             # ToolRegistry.default() 收集的模块级实例
