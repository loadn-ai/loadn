"""`loadn skills lock|verify` 子命令（P0-3 供应链锁的工具面）。

- lock：为当前已发现的外部来源 skill（SKILL.md frontmatter 带 source）
  生成/补全锁条目，写 $LOADN_HOME/skills.lock.json（用户可写层；包内锁
  随发布分发不动）。已存在且哈希变化的条目**不静默覆盖**——打 rug-pull
  告警并要求 --update 显式确认（学 W4 MCP 哈希锁文案）。
- verify：全部外部 skill 必须命中锁且哈希一致；否则退出码 1（CI 发布门）。
幂等：同内容重复 lock 产出字节级相同文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from loadn.core.skills import discover_skills, load_locks, lock_paths, skill_hash
from loadn.util import parse_frontmatter


def _external_skills(cwd: Path) -> dict[str, dict]:
    """name → {source, sourceType, skillPath, computedHash}（当前外部来源）。

    enforce_lock=False 扫全量——未锁外部 skill 也要能进锁（生成器语义）。
    """
    out: dict[str, dict] = {}
    for name, info in discover_skills(cwd, enforce_lock=False).items():
        try:
            meta, _ = parse_frontmatter(info.path.read_text(encoding="utf-8"))
        except OSError:
            continue
        source = str(meta.get("source") or "").strip()
        if not source:
            continue
        source_type, _, ref = source.partition(":")
        out[name] = {
            "source": ref or source,
            "sourceType": source_type if ref else "local",
            "skillPath": str(info.path),
            "computedHash": skill_hash(info.path),
        }
    return out


def cmd_lock(cwd: Path, update: bool) -> int:
    user_lock = lock_paths()[-1]              # $LOADN_HOME 层（可写）
    current = _external_skills(cwd)
    existing = {k: v for k, v in load_locks().items()
                if k in current or not update}
    # rug-pull 检测：已锁条目哈希变化 → 不静默覆盖
    changed = {k: (existing[k].get("computedHash"), v["computedHash"])
               for k, v in current.items()
               if k in existing and existing[k].get("computedHash") != v["computedHash"]}
    if changed and not update:
        for k, (old, new) in changed.items():
            print(f"[B6] skill {k} 内容与锁不符（rug-pull 风险）："
                  f"{(old or '')[:12]}… → {new[:12]}…——须人工确认", file=sys.stderr)
        print("确认有意变更后加 --update 显式更新锁", file=sys.stderr)
        return 1
    merged = dict(existing)
    if update:
        merged.update(current)
    else:
        for k, v in current.items():
            merged.setdefault(k, v)
    payload = {"version": 1,
               "skills": {k: merged[k] for k in sorted(merged) if k in merged
                          and (update or k in current or k in existing)}}
    user_lock.parent.mkdir(parents=True, exist_ok=True)
    user_lock.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    n_new = len(current) - len({k for k in current if k in existing})
    print(f"锁已写入 {user_lock}（外部 skill {len(current)} 个，"
          f"新增 {max(n_new, 0)}{', 更新 ' + str(len(changed)) if changed else ''}）")
    return 0


def cmd_verify(cwd: Path) -> int:
    """CI 发布门：任何外部 skill 无锁/哈希不符 → 退出 1。"""
    locks = load_locks()
    bad = 0
    for name, entry in _external_skills(cwd).items():
        locked = locks.get(name)
        if locked is None:
            print(f"✗ {name}：外部来源但不在锁文件（loadn skills lock 生成）",
                  file=sys.stderr)
            bad += 1
        elif locked.get("computedHash") != entry["computedHash"]:
            print(f"✗ {name}：内容与锁不符（rug-pull）——"
                  "人工确认后 loadn skills lock --update", file=sys.stderr)
            bad += 1
    if bad:
        print(f"verify：{bad} 个 skill 未过锁", file=sys.stderr)
        return 1
    print("verify：全部外部 skill 与锁一致")
    return 0


def main(argv: list[str]) -> int:
    """`loadn skills <lock|verify> [--update]`。"""
    import argparse
    from pathlib import Path as _P

    ap = argparse.ArgumentParser(prog="loadn skills",
                                 description="skill 供应链锁（P0-3）")
    sub = ap.add_subparsers(dest="scmd", required=True)
    p_lock = sub.add_parser("lock", help="生成/补全锁（--update 显式更新变更项）")
    p_lock.add_argument("--update", action="store_true",
                        help="已锁条目哈希变化时显式覆盖（人工确认后）")
    sub.add_parser("verify", help="校验全部外部 skill（CI 用，失败退出 1）")
    args = ap.parse_args(argv)
    cwd = _P.cwd()
    if args.scmd == "lock":
        return cmd_lock(cwd, update=args.update)
    return cmd_verify(cwd)
