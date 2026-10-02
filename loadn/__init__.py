"""loadn（读 load-n）——自研 Claude Code 内涵的原生 agent 引擎。

Python 3.10+ / asyncio，仅一个运行时依赖（httpx）。CLI 输出刻意兼容 claude CLI
stream-json 契约（宿主以子进程接入，判死/记账/SSE 外层零改动），
同时可独立使用（REPL/headless）。工程详设见仓库计划文档。

目录：core/（循环引擎）tools/ providers/ supervisor/ mcp/ persistence/ cli/。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

__version__ = "0.6.23"


def loadn_home() -> Path:
    """数据根（sessions/transcript/session.db/agents/skills 落点）。

    env 优先级：LOADN_HOME > HAHANESS_HOME（旧名兼容一版）> 默认 ~/.loadn。
    首次调用若默认目录不存在而旧 ~/.agent 存在，整体搬迁（旧名留 .bak 不删）。
    """
    if os.environ.get("LOADN_HOME"):
        return Path(os.environ["LOADN_HOME"])
    if os.environ.get("HAHANESS_HOME"):
        return Path(os.environ["HAHANESS_HOME"])
    new = Path.home() / ".loadn"
    old = Path.home() / ".agent"
    if not new.exists() and old.exists():
        try:
            shutil.move(str(old), str(new))
        except OSError:
            return old          # 搬不动（占用/权限）就用旧根，行为不坏
    return new
