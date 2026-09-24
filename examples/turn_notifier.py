"""例 7/10：Stop 事件——turn 收尾通知（观察 turn 终态）。

写一行摘要到 cwd/.loadn/turns.log（subtype + 耗时侧信息）。
"""
from __future__ import annotations

import time


def _on_stop(payload: dict):
    try:
        with open(".loadn/turns.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')} turn 结束："
                    f"{payload.get('subtype')} {str(payload.get('text', ''))[:80]}\n")
    except OSError:
        pass
    return None


def load(ext) -> None:
    ext.on("Stop", _on_stop)
