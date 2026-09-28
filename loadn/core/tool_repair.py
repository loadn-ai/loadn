"""tool-call-repair（P1-2，OpenClaw O1 同构）：廉价网关把工具调用写成正文
时自动修复，不丢调用。

- 语法双形态：fenced（```tool Name / {json} / ```）与行内（`Name{json}`，
  反引号包裹）。
- **standalone 语义**（openclaw parseStandalonePlainTextToolCallBlocks
  同构）：整个 assistant 文本（去空白）必须全部由工具调用块构成才修复；
  正文混排 → None 不修。代码块/引用内的同形文本因此天然受保护
  （protection 由语义承载，无需独立区间扫描）。
- promote 后的调用走与真实调用**完全相同**的执行路径（权限引擎 +
  PreToolUse 钩子 + 审批门）——不得绕过（卡面红线）。
- 修复动作 transcript 记 `tool_call_repaired` 事件（env
  LOADN_TOOL_REPAIR=0 可关）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from loadn.util import get_logger

log = get_logger(__name__)

# fenced：```tool Name（或 ```tool:Name）换行 JSON 换行 ```
_FENCED_RE = re.compile(
    r"```tool[: ]+\s*(?P<name>[A-Za-z_][\w.-]*)\s*\n"
    r"(?P<body>\{.*?\})\s*\n?```",
    re.S)
# 行内：`Name{json}`（反引号包裹，json 须对象）
_INLINE_RE = re.compile(
    r"`(?P<name>[A-Za-z_][\w.-]*)\s*(?P<body>\{[^{}]*\})`")


@dataclass
class ToolCallBlock:
    name: str
    args: dict
    span: tuple[int, int]           # (start, end) 原文区间
    syntax: str                      # fenced | inline


def parse_standalone_blocks(text: str,
                            known_tools: set[str] | None = None
                            ) -> list[ToolCallBlock] | None:
    """openclaw standalone 语义：全文本=纯调用块序列才返回，否则 None。

    known_tools 给定时，块名须命中（防把任意 JSON 段误当调用）。
    """
    if not text or not text.strip():
        return None
    blocks: list[ToolCallBlock] = []
    pos = 0
    s = text
    while True:
        # 跳过块间空白
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s):
            break
        m = _FENCED_RE.match(s, pos) or _INLINE_RE.match(s, pos)
        if not m:
            return None                       # 混排正文/未知形态 → 不修
        name = m.group("name")
        if known_tools is not None and name not in known_tools:
            return None                       # 非已知工具名 → 不修（防误伤）
        try:
            args = json.loads(m.group("body"))
        except json.JSONDecodeError:
            return None
        if not isinstance(args, dict):
            return None
        blocks.append(ToolCallBlock(name, args, m.span(),
                                    "fenced" if m.re is _FENCED_RE else "inline"))
        pos = m.end()
    return blocks or None


def strip_blocks(text: str, blocks: list[ToolCallBlock]) -> str:
    """去掉调用块后的用户可见正文（standalone 语义下通常只剩空白）。"""
    out = text
    for b in sorted(blocks, key=lambda x: x.span[0], reverse=True):
        out = out[:b.span[0]] + out[b.span[1]:]
    return out.strip()


def repair_enabled() -> bool:
    import os
    return os.environ.get("LOADN_TOOL_REPAIR", "1") != "0"


def try_repair(text: str, known_tools: set[str] | None) -> list[ToolCallBlock]:
    """便捷口：可关开关 + standalone 解析（失败返回空表）。"""
    if not repair_enabled():
        return []
    blocks = parse_standalone_blocks(text, known_tools)
    return blocks or []
