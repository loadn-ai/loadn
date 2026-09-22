"""引擎注册表：claude | loadn（默认候选）| opencode；hahaness=旧名 alias。

选择序：profile.engine > CONFIG.engines.default；未知名 log 警告回落 claude。
"""
from __future__ import annotations

from ..config import CONFIG
from ..util import get_logger
from .base import EngineSpec, EventAdapter
from .claude import ClaudeSpec, resolve_claude_bin
from .loadn import LoadnSpec
from .opencode import OpencodeSpec

log = get_logger(__name__)

ENGINES: dict[str, EngineSpec] = {
    "claude": ClaudeSpec(),
    "loadn": LoadnSpec(),
    "hahaness": LoadnSpec(),   # 旧名 alias（一版）：同一 spec，配置键 fallback
    # opencode 能力位速览：supports_transcript=False（判死只剩 stdout 一路）｜
    # max_turns_flag=False（无 --max-turns，靠 timeout_s 收敛）｜
    # session_id_domain="any"（ses_… 非 UUID，轮换=空串 fresh）｜
    # 有状态适配器（NDJSON→stream-json，result 恒在 finalize 合成）
    "opencode": OpencodeSpec(),
}


def default_engine() -> str:
    return CONFIG.engines.default if CONFIG.engines.default in ENGINES else "claude"


def resolve(engine: str | None) -> EngineSpec:
    name = engine or default_engine()
    spec = ENGINES.get(name)
    if spec is None:
        log.warning("未知引擎 %s，回落 claude", name)
        spec = ENGINES["claude"]
    return spec
