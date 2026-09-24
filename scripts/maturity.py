#!/usr/bin/env python3
"""成熟度记分卡生成（P3-6，openclaw taxonomy 消费面同构）。

读 taxonomy.yaml（子系统×能力×coverage-id）+ pytest marker 收集
（tests/ 里 `@pytest.mark.coverage("<id>")` / pytestmark 形态）→
markdown 记分卡：
- 每能力：M 级 / 证据测试（文件名列表）/ 状态（✅ 有证据；🔴 **空
  coverage**——声明无测试；🟡 coverage 声明但无测试命中）
- profile 视图：smoke-ci / nightly 各覆盖哪些 ID（CI 按 profile 跑）
- snapshot 锚当前 git sha（覆盖写 yaml 里的占位）

用法：python scripts/maturity.py [--out out.md]
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TAXONOMY = REPO / "taxonomy.yaml"

_COV_RE = re.compile(
    r'coverage\("([^"]+)"\)|coverage\(\'([^\']+)\'\)')


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=5
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def collect_test_coverage() -> dict[str, list[str]]:
    """pytest marker 收集：coverage-id → [测试文件名]。"""
    cov: dict[str, list[str]] = {}
    for py in (REPO / "tests").rglob("test_*.py"):
        try:
            text = py.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _COV_RE.finditer(text):
            cid = m.group(1) or m.group(2)
            cov.setdefault(cid, []).append(py.relative_to(REPO).as_posix())
    return cov


def main() -> int:
    import yaml
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="", help="落盘路径（缺省 stdout）")
    args = ap.parse_args()

    data = yaml.safe_load(TAXONOMY.read_text(encoding="utf-8"))
    cov = collect_test_coverage()
    sha = git_sha()

    levels = {lv.get("code", lv["id"]): lv for lv in data.get("levels", [])}
    lines = ["# loadn 成熟度记分卡",
             "",
             f"snapshot：`{sha}`（{data.get('snapshot', {}).get('date', '?')}）"
             f"｜生成：scripts/maturity.py｜证据=pytest marker `coverage`",
             ""]
    empty, claimed_missing, evidenced = 0, 0, 0
    for sub in data.get("subsystems", []):
        lines += [f"## {sub.get('label', sub['id'])}（{sub['id']}）", "",
                  "| 能力 | 级 | 证据测试 | 状态 |", "|---|---|---|---|"]
        for cap in sub.get("capabilities", []):
            cid = cap.get("coverage") or ""
            label = cap.get("label", cap["id"])
            lv = levels.get(cap.get("level", ""),
                            {"code": "?", "label": "?"})
            if not cid:
                status = "🔴 空 coverage（声明无测试）"
                evidence = "—"
                empty += 1
            elif cid in cov:
                files = cov[cid]
                evidence = "<br>".join(f"`{f}`" for f in sorted(files))
                status = f"✅ {len(files)} 文件"
                evidenced += 1
            else:
                status = "🟡 coverage 声明但无测试命中"
                evidence = "—"
                claimed_missing += 1
            lines.append(
                f"| {label} | {lv.get('code')} {lv.get('label')} "
                f"| {evidence} | {status} |")
        lines.append("")

    lines += ["## profiles", ""]
    for prof in data.get("profiles", []):
        ids = prof.get("coverageIds", [])
        hit = sum(1 for i in ids if i in cov)
        lines.append(f"- **{prof['id']}**（{prof.get('evidenceMode', 'slim')}）："
                     f"{hit}/{len(ids)} coverage 有证据")
        for i in ids:
            mark = "✅" if i in cov else "🟡"
            lines.append(f"  - {mark} `{i}`")

    lines += ["", "---", "",
              f"合计：✅ 有证据 **{evidenced}**｜🔴 空 coverage **{empty}**"
              f"｜🟡 声明未命中 **{claimed_missing}**",
              "", "> 🔴 = 声明了能力但没有 coverage-id（补 marker 或降级声明）；"
              "🟡 = 声明了 id 但没有测试命中（测试丢了或改名没同步 taxonomy）"]

    out = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"记分卡 → {args.out}")
        print(f"✅ {evidenced}｜🔴 {empty}｜🟡 {claimed_missing}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
