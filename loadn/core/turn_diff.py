"""turn 级 diff（P3-1，codex turn_diff_tracker 同构）：工具改了哪些行。

- 挂点：Edit/Write/MultiEdit 的 `_write_guarded` 落盘处（唯一写入通道
  ——P0-1 锁内），写前内容快照 → 写后 unified diff 旁挂工具结果尾部。
- **预算纪律**（codex 100ms 预算同构）：difflib 对病态输入（超长行/巨
  文件）会爆预算——两层防护：单文件内容 >MAX_DIFF_BYTES 直接粗粒度
  （"N 字节变更"）；diff 行数 >MAX_DIFF_LINES 截断为行数摘要。计时超
  预算（罕见）同样回退——**diff 永不阻塞工具完成**。
- 消费：工具结果尾部 `# diff` 段（模型自救可见）；turn 汇总进
  transcript 的 `result` 事件可选 `diff_summary` 字段（v2 联动 P2-3）；
  审计摘要 `diff_hash`（sha256 前 16 位——账本不存全文，全文在
  transcript 快照）。
"""
from __future__ import annotations

import difflib
import hashlib
import time
from pathlib import Path

DIFF_BUDGET_S = 0.1                  # 100ms 预算（codex 同构）
MAX_DIFF_BYTES = 512 * 1024          # 单文件超此直接粗粒度
MAX_DIFF_LINES = 200                 # diff 行数截断


def snapshot(path: Path) -> bytes | None:
    """写前内容（读失败=二进制/权限 → None=改后不可比对）。"""
    try:
        return path.read_bytes()
    except OSError:
        return None


def compute(path: Path, before: bytes | None, after: bytes,
            budget_s: float = DIFF_BUDGET_S) -> tuple[str, str]:
    """unified diff（旁挂文案）+ diff_hash。返回 (diff_text, diff_hash)。

    超预算/病态输入 → 粗粒度文案（"N 字节变更"），hash 仍稳定（内容哈希）。
    """
    h = hashlib.sha256(before or b"" + b"\0" + after).hexdigest()[:16]
    if before is None:
        return f"# diff：新建文件（{len(after)} 字节）", h
    if len(before) > MAX_DIFF_BYTES or len(after) > MAX_DIFF_BYTES:
        return f"# diff：{len(before)} → {len(after)} 字节变更（粗粒度，超单文件上限）", h
    t0 = time.perf_counter()
    try:
        old = before.decode("utf-8", errors="replace").splitlines(keepends=True)
        new = after.decode("utf-8", errors="replace").splitlines(keepends=True)
        diff = list(difflib.unified_diff(
            old, new, fromfile=f"{path}（旧）", tofile=f"{path}（新）"))
    except (ValueError, RecursionError):
        diff = None
    if diff is None or time.perf_counter() - t0 > budget_s:
        return f"# diff：{len(before)} → {len(after)} 字节变更（粗粒度，超预算）", h
    if not diff:
        return "", h                          # 内容未变（mtime 变了而已）
    if len(diff) > MAX_DIFF_LINES:
        head = "".join(diff[:MAX_DIFF_LINES])
        return (head + f"\n…（共 {len(diff)} 行 diff，截断显示 {MAX_DIFF_LINES} 行）", h)
    return "".join(diff), h


def summary_line(diff_text: str) -> str:
    """审计/结果事件用一行摘要。"""
    if not diff_text:
        return ""
    adds = sum(1 for ln in diff_text.splitlines()
               if ln.startswith("+") and not ln.startswith("+++"))
    dels = sum(1 for ln in diff_text.splitlines()
               if ln.startswith("-") and not ln.startswith("---"))
    return f"+{adds}/-{dels}"
