"""`loadn memory` 子命令（P4）：跨项目记忆域的手动管理面。

- promote <id>：把项目域记忆条目提升为用户级（原域删除、溯源保留）。
  id 见 $LOADN_HOME/memory/<project>/manifest.json 的 entries[].id。
"""
from __future__ import annotations

import sys
from pathlib import Path

from loadn.core import memory as mem


def cmd_promote(cwd: Path, eid: str) -> int:
    if not mem.user_enabled():
        print("LOADN_USER_MEMORY=off——用户域关闭，promote 不可用", file=sys.stderr)
        return 1
    out = mem.promote(cwd, eid)
    if out is None:
        print(f"项目域记忆里没有 id={eid}（查 manifest.json 的 entries[].id）",
              file=sys.stderr)
        return 1
    print(f"已提升为用户级记忆：[{out['id']}] {out['summary']}")
    return 0


def main(argv: list[str]) -> int:
    """`loadn memory <promote> [...]`。"""
    import argparse

    ap = argparse.ArgumentParser(prog="loadn memory",
                                 description="长期记忆域管理（P4）")
    sub = ap.add_subparsers(dest="mcmd", required=True)
    p_prom = sub.add_parser("promote", help="把项目记忆提升为用户级（跨项目）")
    p_prom.add_argument("id", help="记忆条目 id（manifest entries[].id）")
    args = ap.parse_args(argv)
    cwd = Path.cwd()
    if args.mcmd == "promote":
        return cmd_promote(cwd, args.id)
    return 1
