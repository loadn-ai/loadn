"""例 10/10：PostToolUse 后 git 检查点提示（写路径观察 + 自动验证钩子）。

编辑后把变更文件清单追加 .loadn/git-checkpoint.log——供人工/CI 对照
auto-commit 影子分支（P3-10）的 turn 提交面。
"""
from __future__ import annotations

import time


def _on_post_tool_use(payload: dict):
    if payload.get("tool") not in ("Edit", "Write", "MultiEdit"):
        return None
    fp = str(payload.get("input", {}).get("file_path") or "")
    if not fp:
        return None
    try:
        with open(".loadn/git-checkpoint.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')} {payload.get('tool')} {fp}\n")
    except OSError:
        pass
    return None


def load(ext) -> None:
    ext.on("PostToolUse", _on_post_tool_use)
