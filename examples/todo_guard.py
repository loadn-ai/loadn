"""例 8/10：TodoWrite 质量门——空标题/空清单即拦（input 语义校验示例）。"""
from __future__ import annotations


def _on_pre_tool_use(payload: dict):
    if payload.get("tool") != "TodoWrite":
        return None
    todos = payload.get("input", {}).get("todos") or []
    if not todos:
        return {"decision": "block",
                "reason": "todo-guard：清空任务清单请用明确意图表述，"
                          "不发空 TodoWrite"}
    for t in todos:
        if not str(t.get("subject") or "").strip():
            return {"decision": "block",
                    "reason": "todo-guard：任务缺 subject"}
    return None


def load(ext) -> None:
    ext.on("PreToolUse", _on_pre_tool_use)
