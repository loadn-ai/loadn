#!/usr/bin/env python3
"""历史消息穿插回放回填：从 var/logs/calls/turn<id>_*.out 的原始 stream-json
重建 blocks_json（text 段按真实顺序穿插进 thinking/tool 之间）。

2026-09-22 前落库的消息只有 thinking/tool 块 + content 拼接文本，穿插顺序
丢失（前端只能显示「N 步过程」面板 + 合并正文）。本脚本按 engine._consume
同款逻辑重放原始日志恢复顺序。

护栏：①重建结果 thinking/tool 块数 < 现库（日志缺失/截断）→ 跳过不写；
②无 text 块 → 跳过；③先备份 DB。幂等：已是新格式（含 text 块）的消息跳过。

用法：python3 scripts/backfill_replay_blocks.py [--dry] [--turn 1058]
"""
from __future__ import annotations

import glob
import json
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from loadn_webui import db as db_mod  # noqa: E402
from loadn_webui.config import PATHS  # noqa: E402
from loadn_webui.engine import (  # noqa: E402
    _clip_input, _content_text, _RESULT_CAP, _THINK_CAP, _tool_brief,
)


def replay_blocks(log_path: Path) -> list[dict] | None:
    """按引擎同款逻辑重放日志 → 穿插 blocks（文件不存在返回 None）。"""
    blocks: list[dict] = []
    tool_names: dict[str, str] = {}
    n_lines = 0
    with open(log_path, errors="replace") as f:
        for line in f:
            n_lines += 1
            try:
                ev = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            t = ev.get("type")
            if t == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "text" and (b.get("text") or "").strip():
                        blocks.append({"type": "text", "text": b["text"]})
                    elif bt == "thinking" and (b.get("thinking") or "").strip():
                        blocks.append({"type": "thinking",
                                       "text": b["thinking"][:_THINK_CAP]})
                    elif bt == "tool_use":
                        name = b.get("name", "?")
                        blocks.append({"type": "tool", "id": b.get("id"), "name": name,
                                       "brief": _tool_brief(name, b.get("input") or {}),
                                       "input": _clip_input(b.get("input"))})
                        if b.get("id"):
                            tool_names[b["id"]] = name
            elif t == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                        continue
                    result = _content_text(b.get("content"))[:_RESULT_CAP]
                    is_err = bool(b.get("is_error"))
                    tuid = b.get("tool_use_id")
                    for blk in reversed(blocks):
                        if blk.get("type") == "tool" and blk.get("id") == tuid:
                            blk["result"] = result
                            blk["is_error"] = is_err
                            break
    if n_lines == 0:
        return None
    return blocks


def main() -> None:
    dry = "--dry" in sys.argv
    only_turn = None
    if "--turn" in sys.argv:
        only_turn = int(sys.argv[sys.argv.index("--turn") + 1])
    logs_dir = PATHS["var"] / "logs" / "calls"
    with db_mod.conn() as c:
        rows = c.execute(
            "SELECT id, session_id, turn_id, content, blocks_json FROM messages "
            "WHERE role='assistant' AND blocks_json IS NOT NULL ORDER BY id").fetchall()
    done = skipped = noglob = mismatch = 0
    for r in rows:
        if only_turn is not None and r["turn_id"] != only_turn:
            continue
        try:
            cur = json.loads(r["blocks_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if any(b.get("type") == "text" for b in cur if isinstance(b, dict)):
            continue   # 已是新格式
        files = sorted(glob.glob(str(logs_dir / f"turn{r['turn_id']}_*.out")),
                       key=lambda p: Path(p).stat().st_mtime)
        if not files:
            noglob += 1
            continue
        rebuilt = replay_blocks(Path(files[-1]))
        if not rebuilt:
            noglob += 1
            continue
        if not any(b["type"] == "text" for b in rebuilt):
            skipped += 1
            continue
        # 护栏：重建的 thinking/tool 数必须 ≥ 现库（防日志缺尾部把数据写瘦）
        n_old = sum(1 for b in cur if isinstance(b, dict) and b.get("type") != "text")
        n_new = sum(1 for b in rebuilt if b["type"] != "text")
        if n_new < n_old:
            mismatch += 1
            print(f"  跳过 msg {r['id']} (turn {r['turn_id']}): "
                  f"日志块 {n_new} < 现库 {n_old}（日志不完整）")
            continue
        if dry:
            print(f"[dry] msg {r['id']} turn {r['turn_id']}: "
                  f"{n_old} → {len(rebuilt)} 块（含 {len(rebuilt) - n_new} text 段）")
        else:
            with db_mod.conn() as c:
                c.execute("UPDATE messages SET blocks_json=? WHERE id=?",
                          (json.dumps(rebuilt, ensure_ascii=False), r["id"]))
        done += 1
    mode = "dry-run " if dry else ""
    print(f"{mode}完成：回填 {done}，无 text 段跳过 {skipped}，无日志 {noglob}，护栏拦下 {mismatch}")


if __name__ == "__main__":
    if not Path(PATHS["db"]).exists():
        raise SystemExit(f"db 不存在: {PATHS['db']}")
    if "--dry" not in sys.argv:
        bak = PATHS["var"] / f"workdaddy.db.bak.blocks-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy(PATHS["db"], bak)
        print(f"已备份 DB → {bak}")
    main()
