"""claude CLI 引擎（现状行为的恒等迁移，自 claude_runner.py 搬入）。"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import CONFIG
from .base import EngineSpec, summarize_transcript_tail

if TYPE_CHECKING:
    from ..claude_runner import TurnCall

# 探测序：PATH → nvm 最新版本目录扫描（不锁具体版本号，可移植）
def _nvm_scan() -> str:
    base = Path.home() / ".nvm/versions/node"
    if base.is_dir():
        vs = sorted(base.iterdir(), reverse=True)
        for v in vs:
            cand = v / "bin/claude"
            if cand.exists():
                return str(cand)
    return ""
CLAUDE_FALLBACK = _nvm_scan() or str(Path.home() / ".local/bin/claude")


def resolve_claude_bin() -> str:
    override = (CONFIG.engines.claude.bin or CONFIG.claude.claude_bin
                or os.environ.get("LOADN_CLAUDE_BIN") or os.environ.get("WORKDADDY_CLAUDE_BIN"))
    if override:
        return override
    return shutil.which("claude") or CLAUDE_FALLBACK


def _uuid_or_new(session_id: str) -> str:
    """真实 claude CLI 强制 --session-id 为 UUID；非法值换新 UUID（P0 实测校验）。"""
    try:
        return str(uuid.UUID(session_id))
    except (ValueError, AttributeError):
        return str(uuid.uuid4())


class ClaudeSpec(EngineSpec):
    name = "claude"

    def resolve_bin(self) -> str | list[str]:
        return resolve_claude_bin()

    def build_argv(self, call: TurnCall) -> tuple[list[str], dict]:
        cmd = [
            resolve_claude_bin(), "-p",
            "--verbose",                       # stream-json 必需（P0 实测）
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
            "--permission-mode", "bypassPermissions",
            "--effort", call.effort or CONFIG.claude.effort,
        ]
        cmd += list(CONFIG.engines.claude.extra_args or [])
        model = call.model or CONFIG.claude.model or CONFIG.engines.claude.model
        if model:
            cmd += ["--model", model]
        if call.max_turns:                # 收敛闸：agentic 轮次上限（result subtype=error_max_turns）
            cmd += ["--max-turns", str(call.max_turns)]
        # off 档执行域门与 loadn 引擎同源（TurnCall.disallowed_tools：
        # profile + off_tier 门）——claude CLI 原生同名旗标，面级隐藏+调用拒。
        for t in (call.disallowed_tools or []):
            cmd += ["--disallowedTools", t]
        session = _uuid_or_new(call.session_id)
        if call.resume:
            cmd += ["--resume", session]
        else:
            cmd += ["--session-id", session]
        cmd += ["--", call.prompt]   # 三轮修：-- 终结符（单词消息
        return cmd, {}                  # 如 --version 不被当 flag 劫持）

    def transcript_age(self, session_id: str) -> float | None:
        """session transcript（claude CLI 持续追加的 jsonl）年龄——独立于 stdout 的活跃信号。"""
        try:
            hits = (Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl")
            mtimes = [h.stat().st_mtime for h in hits]
            return time.time() - max(mtimes) if mtimes else None
        except OSError:
            return None

    def session_tail(self, session_id: str, *, max_chars: int = 1800) -> str:
        try:
            hits = sorted((Path.home() / ".claude" / "projects")
                          .glob(f"*/{session_id}.jsonl"),
                          key=lambda p: p.stat().st_mtime)
            return summarize_transcript_tail(hits[-1], max_chars=max_chars) if hits else ""
        except OSError:
            return ""
