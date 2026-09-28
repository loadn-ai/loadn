"""例 6/10：PostToolUse 输出改写（override 语义示例）。

handler 返回 {"output": …} 覆盖工具结果——这里把 Read 的输出整体
大写（演示能力本身；实际用途如脱敏/截断/格式归一）。
"""
from __future__ import annotations


def _on_post_tool_use(payload: dict):
    if payload.get("tool") != "Read":
        return None
    out = payload.get("output")
    if isinstance(out, str) and out:
        return {"output": out.upper()}
    return None


def load(ext) -> None:
    ext.on("PostToolUse", _on_post_tool_use)
