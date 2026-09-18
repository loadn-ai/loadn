"""hahaness——自研 Claude Code 内涵的原生 agent 引擎。

Python 3.10+ / asyncio，仅一个运行时依赖（httpx）。CLI 输出刻意兼容 claude CLI
stream-json 契约（宿主以子进程接入，判死/记账/SSE 外层零改动），
同时可独立使用（REPL/headless）。工程详设见仓库计划文档。

目录：core/（循环引擎）tools/ providers/ supervisor/ mcp/ persistence/ cli/。
"""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.6.0"


def hahaness_home() -> Path:
    """数据根（sessions/transcript/session.db 落点）；HAHANESS_HOME 覆盖。"""
    return Path(os.environ.get("HAHANESS_HOME") or Path.home() / ".agent")
