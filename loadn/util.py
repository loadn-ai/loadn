"""轻量工具函数（logging 门面 + frontmatter 解析——零第三方依赖）。"""
from __future__ import annotations

import logging
import re
import sys

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def get_logger(name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    if not lg.handlers and not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            stream=sys.stderr)
    return lg


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Markdown frontmatter 解析：返回 (meta, 正文)。

    支持的值形态（skills/agent 定义够用，不引 yaml 依赖）：
      key: value                  → str
      key: [a, b, c]              → list[str]（内联逗号列表）
      key:                        → 后续每行 "  - item" 收进 list[str]
    无 frontmatter → ({}, 原文)。坏行跳过不炸。
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    pending_key: str | None = None
    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        indented = ln[:1] in (" ", "\t")
        if indented and pending_key is not None:
            item = ln.strip()
            if item.startswith("- "):
                item = item[2:].strip()
            if item:
                meta[pending_key].append(item)
            continue
        pending_key = None
        if ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        if not val:
            meta[key] = []
            pending_key = key
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [v.strip().strip("'\"") for v in inner.split(",")
                         if v.strip()] if inner else []
        else:
            meta[key] = val.strip("'\"")
    return meta, text[m.end():]
