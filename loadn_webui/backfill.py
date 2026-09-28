"""历史成本回填：从 var/logs/calls/turn<TID>_<ms>.out 的 result 事件提 modelUsage
写入 turns.models_json（存量 turns 跑在 claude_runner 解析 modelUsage 之前）。

同一 turn 多次运行（stop/重试）会有多个 .out：文件名毫秒戳等长，字典后写覆盖
即"最后一次运行"的用量，与 engine 对 turn 行的覆写语义一致。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import db as db_mod
from .config import PATHS

_NAME_RE = re.compile(r"^turn(\d+)_\d+\.out$")


def _extract_model_usage(out_file: Path) -> dict | None:
    """单文件里找 result 事件的 modelUsage（子串预过滤，坏行/半行跳过）。"""
    try:
        text = out_file.read_text(errors="replace")
    except OSError:
        return None
    if '"modelUsage"' not in text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if '"modelUsage"' not in line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result" and isinstance(ev.get("modelUsage"), dict):
            mu = {k: v for k, v in ev["modelUsage"].items() if isinstance(v, dict)}
            return mu or None
    return None


def run(force: bool = False) -> dict:
    stats = {"scanned": 0, "with_model_usage": 0, "backfilled": 0,
             "skipped_existing": 0, "orphan_files": 0}
    d = PATHS["call_logs"]
    if not d.exists():
        return stats
    pending: dict[int, dict] = {}
    for f in sorted(d.glob("*.out")):
        m = _NAME_RE.match(f.name)
        if not m:
            continue
        stats["scanned"] += 1
        mu = _extract_model_usage(f)
        if mu is None:
            continue
        stats["with_model_usage"] += 1
        pending[int(m.group(1))] = mu
    with db_mod.conn() as c:
        for tid, mu in pending.items():
            row = c.execute("SELECT models_json FROM turns WHERE id=?", (tid,)).fetchone()
            if row is None:
                stats["orphan_files"] += 1          # turn 已删（DELETE /turns）
                continue
            if row["models_json"] and not force:
                stats["skipped_existing"] += 1
                continue
            db_mod.update_turn(c, tid, models_json=json.dumps(mu, ensure_ascii=False))
            stats["backfilled"] += 1
    return stats
