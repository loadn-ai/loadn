"""契约：前端人名池镜像与引擎 AGENT_NAME_POOL 一致（含顺序）。

兜底取名公式 (n-1)%len 在两侧（引擎 subagent.py / 前端 agents.ts）——顺序
漂移 = 旧数据 retro 命名取错人。纯读两文件比对，零 token。
"""
from __future__ import annotations

import pathlib
import re

from loadn.constants import AGENT_NAME_POOL

_TS = (pathlib.Path(__file__).resolve().parents[2] / "ui" / "src"
       / "stores" / "agents.ts")


def test_frontend_pool_matches_engine():
    src = _TS.read_text(encoding="utf-8")
    m = re.search(r"AGENT_NAME_POOL[^=]*=\s*\[(.*?)\]", src, re.S)
    assert m, "agents.ts 缺 AGENT_NAME_POOL 镜像（须与 loadn/constants.py 同步）"
    names = re.findall(r"'([^']+)'", m.group(1))
    assert tuple(names) == tuple(AGENT_NAME_POOL), \
        f"前端人名池与引擎漂移：前端 {len(names)} 名 / 引擎 {len(AGENT_NAME_POOL)} 名"
