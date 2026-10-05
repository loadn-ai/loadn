"""`loadn memory` 子命令（P4/P5）：跨项目记忆域的手动管理面。

- promote <id>：把项目域记忆条目提升为用户级（原域删除、溯源保留）。
- log [--domain user|project]：记忆域 git 提交史（一次 commit=一次「记住」）。
- restore <commit> [--domain ...]：从提交恢复单条记忆（禁整仓 reset——
  只物化该提交新增的 .md 条目并重新入库）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from loadn.core import memory as mem
from loadn.util import parse_frontmatter


def _domain_dir(cwd: Path, domain: str) -> Path:
    if domain not in (mem.PROJECT_DOMAIN, mem.USER_DOMAIN):
        print(f"未知 domain：{domain}（user|project）", file=sys.stderr)
        raise SystemExit(2)
    return mem.memory_dir(cwd, domain)


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


def cmd_log(cwd: Path, domain: str) -> int:
    dir_ = _domain_dir(cwd, domain)
    if not (dir_ / ".git").exists():
        print("（该域尚未 git 化——首笔写入后自动 init）")
        return 0
    r = mem._git(dir_, "log", "--oneline", "-50")
    if r is None or r.returncode != 0:
        print("git log 失败", file=sys.stderr)
        return 1
    out = r.stdout.strip()
    print(out if out else "（无提交）")
    return 0


def cmd_restore(cwd: Path, commit: str, domain: str) -> int:
    """恢复该提交新增的记忆条目（不 reset 整仓；新条目重新入库新提交）。"""
    dir_ = _domain_dir(cwd, domain)
    if not (dir_ / ".git").exists():
        print("该域没有 git 历史", file=sys.stderr)
        return 1
    r = mem._git(dir_, "show", "--name-only", "--pretty=format:",
                 "-1", commit)
    if r is None or r.returncode != 0:
        print(f"找不到提交 {commit}", file=sys.stderr)
        return 1
    mds = [ln.strip() for ln in (r.stdout or "").splitlines()
           if re.fullmatch(r"[0-9a-f]{12}\.md", ln.strip())]
    if not mds:
        print(f"提交 {commit} 没有记忆条目文件（可能只是 manifest/reject 提交）",
              file=sys.stderr)
        return 1
    restored = 0
    for fname in mds:
        show = mem._git(dir_, "show", f"{commit}:{fname}")
        if show is None or show.returncode != 0:
            continue
        meta, body = parse_frontmatter(show.stdout)
        summary = str(meta.get("summary") or fname[:12])
        out = mem.remember(cwd, summary, body.strip(),
                           origin_session=str(meta.get("origin_session") or "?"),
                           domain=domain)
        if out:
            restored += 1
    if not restored:
        print("恢复失败（内容不可读或全部被护栏拦截）", file=sys.stderr)
        return 1
    print(f"已恢复 {restored} 条（新 id 见 {dir_.name}/manifest.json；"
          "历史不受影响）")
    return 0


def main(argv: list[str]) -> int:
    """`loadn memory <promote|log|restore> [...]`。"""
    ap = argparse.ArgumentParser(prog="loadn memory",
                                 description="长期记忆域管理（P4/P5）")
    sub = ap.add_subparsers(dest="mcmd", required=True)
    p_prom = sub.add_parser("promote", help="把项目记忆提升为用户级（跨项目）")
    p_prom.add_argument("id", help="记忆条目 id（manifest entries[].id）")
    p_log = sub.add_parser("log", help="域 git 提交史")
    p_log.add_argument("--domain", default=mem.PROJECT_DOMAIN,
                       choices=[mem.PROJECT_DOMAIN, mem.USER_DOMAIN])
    p_res = sub.add_parser("restore", help="从提交恢复单条记忆（不 reset）")
    p_res.add_argument("commit", help="提交 hash（log 里的短 hash）")
    p_res.add_argument("--domain", default=mem.PROJECT_DOMAIN,
                       choices=[mem.PROJECT_DOMAIN, mem.USER_DOMAIN])
    args = ap.parse_args(argv)
    cwd = Path.cwd()
    if args.mcmd == "promote":
        return cmd_promote(cwd, args.id)
    if args.mcmd == "log":
        return cmd_log(cwd, args.domain)
    if args.mcmd == "restore":
        return cmd_restore(cwd, args.commit, args.domain)
    return 1
