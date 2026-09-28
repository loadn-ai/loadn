"""例 5/10：Write 前私钥泄漏门（防 secret 落盘）。

PreToolUse 检查 Write/MultiEdit/Edit 内容含 PEM 私钥头即阻断。
"""
from __future__ import annotations

_MARKS = ("BEGIN RSA PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY",
          "BEGIN EC PRIVATE KEY", "BEGIN PRIVATE KEY")


def _on_pre_tool_use(payload: dict):
    if payload.get("tool") not in ("Write", "Edit", "MultiEdit"):
        return None
    blob = str(payload.get("input", {}))
    for mark in _MARKS:
        if mark in blob.upper() or mark in blob:
            return {"decision": "block",
                    "reason": f"检测到私钥材料（{mark}）——禁止写入文件，"
                              "凭证请走 vault"}
    return None


def load(ext) -> None:
    ext.on("PreToolUse", _on_pre_tool_use)
