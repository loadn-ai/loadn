"""例 3/10：PreToolUse 门——拦危险 Bash（on(event) 的 block 语义）。

handler 返回 {"decision": "block", "reason": …} 即阻断（与外部命令
钩子 exit 2 同语义；reason 会回填给模型自救）。
"""
from __future__ import annotations

_BANNED = ("rm -rf /", "mkfs", "dd if=/dev/zero")


def _on_pre_tool_use(payload: dict):
    if payload.get("tool") != "Bash":
        return None
    cmd = " ".join(str(payload.get("input", {}).get("command", "")).split())
    for bad in _BANNED:
        if bad in cmd:
            return {"decision": "block",
                    "reason": f"permission-gate 拦截危险命令：{bad}"}
    return None


def load(ext) -> None:
    ext.on("PreToolUse", _on_pre_tool_use)
