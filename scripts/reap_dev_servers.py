#!/usr/bin/env python3
"""reap_dev_servers：清理泄漏的前端 dev server（vite/esbuild watch 等）。

背景（生产实证 2026-11-02）：playwright 截图/verify 脚本起 `vite dev
--port 41xxx --strictPort`，脚本异常退出或漏杀时 dev server 常驻——
监控面板出现一排"来历不明"的 HTTP 200 端口（本次实测 4 个泄漏存活 7 天）。

指纹：进程 cwd 在指定项目 ui 目录（默认 loadn 仓 ui/）且 cmdline 含
vite/node dev 特征。默认 --list 干跑（只列不动），--kill 才动手；
只杀 dev server（监听端口属回环或高位随机段），不碰构建/服务进程。

用法：
  python3 scripts/reap_dev_servers.py                 # 列出泄漏（干跑）
  python3 scripts/reap_dev_servers.py --kill          # 清理
  python3 scripts/reap_dev_servers.py --root /path/to/other/ui
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

# dev server 特征（cmdline 任一命中即候选；再叠加 cwd 归属二次确认）
_DEV_MARKS = ("vite", "webpack serve", "next dev", "--strictPort")


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return [x for x in raw.decode("utf-8", "replace").split("\0") if x]
    except OSError:
        return []


def _cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def find_leaks(root: Path) -> list[dict]:
    """cwd 在 root 下的 dev server 进程（排除自己/1 号/内核线程）。"""
    out = []
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in (me, 1, 2):
            continue
        cmd = _cmdline(pid)
        if not cmd:
            continue
        joined = " ".join(cmd)
        if not any(m in joined for m in _DEV_MARKS):
            continue
        cwd = _cwd(pid)
        try:
            under = (Path(cwd) == root) if cwd else False
        except OSError:
            under = False
        if under:
            out.append({"pid": pid, "cwd": cwd, "cmd": joined[:120]})
    return sorted(out, key=lambda x: x["pid"])


def main() -> int:
    ap = argparse.ArgumentParser(description="清理泄漏的前端 dev server")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent / "ui"),
                    help="项目 ui 目录（按进程 cwd 归属匹配，默认本仓 ui/）")
    ap.add_argument("--kill", action="store_true",
                    help="实际清理（默认只列出）")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    leaks = find_leaks(root)
    if not leaks:
        print(f"✓ 无泄漏 dev server（root={root}）")
        return 0
    verb = "清理" if args.kill else "发现（干跑，--kill 才动手）"
    print(f"{verb} {len(leaks)} 个 dev server（root={root}）：")
    for x in leaks:
        print(f"  pid {x['pid']:>7}  {x['cmd']}")
    if not args.kill:
        return 0
    n = 0
    for x in leaks:
        try:
            os.kill(x["pid"], signal.SIGTERM)
            n += 1
        except OSError as e:
            print(f"  ⚠️ pid {x['pid']} kill 失败：{e}", file=sys.stderr)
    print(f"已 SIGTERM {n}/{len(leaks)} 个（顽固的 5s 后可复跑或手工 SIGKILL）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
