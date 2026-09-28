"""loadn 引擎：自研原生 agent 引擎（monorepo loadn/ 包）的子进程接缝。

flag 面与 claude CLI 刻意同构（-p --verbose --output-format stream-json …），
事件流原生 stream-json（恒等适配器）——webui 的判死/记账/SSE 外层零改动。
loadn 侧契约详见 loadn/cli/ 与 docs/PROTOCOL.md：argv[-1]=PROMPT、session flag
恒 --session-id/--resume、"Session ID already in use" 同文案错误（复用 engine
的 fresh→resume 翻转分支）。R1 起不再注入 PYTHONPATH=CODE_ROOT——引擎经
console_script/pip 解析（v1.1 §4.3）。
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import CONFIG
from .base import EngineSpec, summarize_transcript_tail
from .claude import _uuid_or_new

if TYPE_CHECKING:
    from ..claude_runner import TurnCall


def loadn_home() -> Path:
    """loadn 引擎数据根（transcript/session.db 落点）；LOADN_HOME 覆盖（测试隔离）。"""
    return Path(os.environ.get("LOADN_HOME") or os.environ.get("HAHANESS_HOME")
                or Path.home() / ".loadn")


class LoadnSpec(EngineSpec):
    name = "loadn"

    def resolve_bin(self) -> str | list[str]:
        override = (CONFIG.engines.loadn.bin
                    or os.environ.get("LOADN_ENGINE_BIN")
                    or os.environ.get("WORKDADDY_HAHANESS_BIN"))
        if override:
            return override
        which = shutil.which("loadn")
        if which:
            return which
        # 本进程同 venv 兜底（webui 与引擎同环境安装的常见形态）
        sibling = Path(sys.executable).parent / "loadn"
        if sibling.exists():
            return str(sibling)
        return [sys.executable, "-m", "loadn"]

    def build_env(self) -> dict:
        return {}          # R1 起 clearenv 白名单的起点：不再注入 PYTHONPATH hack

    def build_argv(self, call: TurnCall) -> tuple[list[str], dict]:
        binp = self.resolve_bin()
        if not isinstance(binp, list):
            binp = [binp]        # str 路径包一层（防 *str 逐字符解包）
        cmd = [*binp, "-p",
               "--verbose",
               "--output-format", "stream-json",
               "--dangerously-skip-permissions",
               "--permission-mode", "bypassPermissions",
               "--effort", call.effort or CONFIG.claude.effort,
               ]
        cmd += list(CONFIG.engines.loadn.extra_args or [])
        model = call.model or CONFIG.claude.model or CONFIG.engines.loadn.model
        if model:
            cmd += ["--model", model]
        if call.max_turns:
            cmd += ["--max-turns", str(call.max_turns)]
        # 双重压缩协调：外层轮换（rotate_input_tokens）已设 → 禁 loadn 内压；
        # engines.no_compact 显式覆盖两头（True 恒禁 / False 恒放行）。
        no_compact = CONFIG.engines.no_compact
        if no_compact is None:
            no_compact = call.rotate_input_tokens is not None
        if no_compact:
            cmd += ["--no-compact"]
        session = _uuid_or_new(call.session_id)
        if call.resume:
            cmd += ["--resume", session]
        else:
            cmd += ["--session-id", session]
        cmd.append(call.prompt)
        env = self.build_env()
        # 随时插话（steering）：宿主把用户插话追加进 workspace 的
        # .steer.<sid>.jsonl（带 sid 段——共享工作区多任务并发时插话不串台），
        # loadn 主循环每轮 LLM 调用前轮询注入——运行中的消息不等排队。
        # 引擎侧双读 LOADN_/HAHANESS_（R1 fallback），此处传新名。
        env["LOADN_STEER_FILE"] = str(Path(call.cwd) / f".steer.{call.sid}.jsonl")
        return cmd, env

    def transcript_age(self, session_id: str) -> float | None:
        try:
            p = loadn_home() / "sessions" / session_id / "transcript.jsonl"
            return time.time() - p.stat().st_mtime if p.exists() else None
        except OSError:
            return None

    def session_tail(self, session_id: str, *, max_chars: int = 1800) -> str:
        # 缺文件 → ''（summarize 内 OSError 兜底）
        return summarize_transcript_tail(
            loadn_home() / "sessions" / session_id / "transcript.jsonl",
            max_chars=max_chars)
