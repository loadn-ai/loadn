"""文案层入口：按 env LOADN_LANG 选表（缺省 zh；未知回落 zh）。"""
from __future__ import annotations

import os


def messages() -> dict:
    lang = os.environ.get("LOADN_LANG", "zh").strip()[:2].lower()
    if lang == "en":
        from . import notify_en
        return notify_en.MESSAGES
    from . import notify_zh
    return notify_zh.MESSAGES
