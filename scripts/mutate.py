#!/usr/bin/env python3
"""轻量突变测试 runner（自研——mutmut3 与本项目 conftest/asyncio 兼容差）。

用法：
  python scripts/mutate.py --targets security      # 按组跑（见 GROUPS）
  python scripts/mutate.py --files loadn_webui/policy.py  # 按文件跑
  python scripts/mutate.py --resume reports/mutate-<id>.json

设计：
- **AST 定位变异点，文本级替换**（lineno/col_offset 换源段，ast.unparse
  生成片段）——比 token 级精确，比整树 unparse 保 diff 最小
- 变异算子 5 类（高杀伤价值）：比较符反转 / and·or 互换 / True·False
  互换 / if 守卫恒真恒假 / 常数边界 ±1
- 每变异只跑**窄测试集**（TARGET_TESTS 映射——全量太慢），
  退出码非 0 = 杀死；超时（默认 60s）= 存活（挂死变异）
- 原子恢复：git checkout -- <file>（每变异后）；报告 JSON 落
  reports/mutate-<ts>.json（存活变异带 reproducer）
- 串行执行（共享 worktree 变异互扰——并行需 git worktree，暂缓）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv/bin/python")

# ---------------------------------------------------------------- 目标组
GROUPS: dict[str, list[str]] = {
    "security": [
        "loadn_webui/policy.py",
        "loadn_webui/approve.py",
        "loadn_webui/vault.py",
        "loadn/truststore.py",
        "loadn_webui/canary.py",
        "loadn_webui/net_policy.py",
        "loadn/bash_policy.py",
    ],
    "writetools": [
        "loadn/tools/edit.py",
        "loadn/tools/write.py",
        "loadn/tools/multiedit.py",
        "loadn/core/turn_diff.py",
        "loadn/core/autocommit.py",
        "loadn/core/autolint.py",
    ],
    "sandbox": [
        "loadn_webui/sandbox.py",
        "loadn_webui/egress_proxy.py",
        "loadn_webui/egress_grants.py",
    ],
    "session": [
        "loadn/core/session.py",
        "loadn/core/hooks.py",
        "loadn/transport/daemon.py",
    ],
}

# 文件 → 窄测试集（杀死该文件变异的最小充分集；映射缺失=跑该文件
# 同名 test_* 的启发式——本 runner 只接受显式映射，防全量慢）
TARGET_TESTS: dict[str, list[str]] = {
    "loadn_webui/policy.py": [
        "tests/security/test_a1_blocklist.py",
        "tests/security/test_a5_bash_policy.py",
        "tests/security/test_a6_policy_amend.py",
        "tests/security/test_guard_negatives.py",
        "tests/security/test_policy_hook_gates.py",
        "tests/test_security_knobs.py",
        "tests/test_egress_align.py",
    ],
    "loadn_webui/approve.py": [
        "tests/security/test_a3_approvals.py",
        "tests/security/test_guard_negatives.py",
        "tests/test_w0_security.py",
    ],
    "loadn_webui/vault.py": [
        "tests/test_vault.py",
        "tests/security/test_e2_audit_chain.py",
    ],
    "loadn/truststore.py": [
        "tests/security/test_b5_trust_gate.py",
    ],
    "loadn_webui/canary.py": [
        "tests/security/test_c1_canary.py",
    ],
    "loadn_webui/net_policy.py": [
        "tests/security/test_a7_net_policy.py",
        "tests/test_egress_align.py",
    ],
    "loadn/bash_policy.py": [
        "tests/security/test_a5_bash_policy.py",
        "tests/security/test_a6_policy_amend.py",
    ],
    "loadn/tools/edit.py": [
        "tests/security/test_guard_negatives.py",
        "tests/test_file_mutex.py",
        "tests/test_turn_diff.py",
    ],
    "loadn/tools/write.py": [
        "tests/security/test_guard_negatives.py",
        "tests/test_file_mutex.py",
        "tests/test_turn_diff.py",
    ],
    "loadn/tools/multiedit.py": [
        "tests/test_file_mutex.py",
    ],
    "loadn/core/turn_diff.py": [
        "tests/test_turn_diff.py",
    ],
    "loadn/core/autocommit.py": [
        "tests/test_autocommit.py",
    ],
    "loadn/core/autolint.py": [
        "tests/test_autolint.py",
    ],
    "loadn_webui/sandbox.py": [
        "tests/security/test_d1_sandbox.py",
        "tests/test_sandbox_units.py",
        "tests/test_egress_align.py",
    ],
    "loadn_webui/egress_proxy.py": [
        "tests/test_egress_units.py",
        "tests/test_egress_ask.py",
        "tests/test_egress_align.py",
    ],
    "loadn_webui/egress_grants.py": [
        "tests/test_egress_balance.py",
        "tests/test_egress_ask.py",
    ],
    "loadn/core/session.py": [
        "tests/test_t9_units.py",
        "tests/test_session_eng.py",
    ],
    "loadn/core/hooks.py": [
        "tests/test_hooks_ext_cmds.py",
        "tests/test_ext.py",
    ],
    "loadn/transport/daemon.py": [
        "tests/test_transport_units.py",
        "tests/test_daemon.py",
    ],
}

MUT_TIMEOUT_S = 90          # 单变异窄测试集上限（挂死=存活）
BASELINE_TIMEOUT_S = 600    # 基线（未变异）集上限

_CMP_SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
             ast.Lt: ast.GtE, ast.GtE: ast.Lt,
             ast.Gt: ast.LtE, ast.LtE: ast.Gt,
             ast.Is: ast.IsNot, ast.IsNot: ast.Is,
             ast.In: ast.NotIn, ast.NotIn: ast.In}


@dataclass
class Mutant:
    file: str
    line: int
    col: int
    end_col: int
    orig: str
    new: str
    kind: str

    def key(self) -> str:
        return f"{self.file}:{self.line}:{self.col}:{self.kind}"


@dataclass
class Report:
    file: str
    total: int = 0
    killed: int = 0
    survived: list[dict] = field(default_factory=list)
    timeout: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- 变异生成
def gen_mutants(path: Path) -> list[Mutant]:
    src = path.read_text(encoding="utf-8")
    src.splitlines(keepends=True)
    tree = ast.parse(src)
    out: list[Mutant] = []
    rel = str(path.relative_to(REPO))

    def _emit(node, new_src: str, kind: str) -> None:
        seg = ast.get_source_segment(src, node)
        if seg is None or seg == new_src:
            return
        if "\n" in seg or "\n" in new_src:
            return          # 多行片段：单行替换会损坏文件（假杀根源）
        out.append(Mutant(rel, node.lineno, node.col_offset,
                          getattr(node, "end_col_offset", node.col_offset),
                          seg, new_src, kind))

    for node in ast.walk(tree):
        # 1) 比较符反转
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                swap = _CMP_SWAP.get(type(op))
                if swap is None:
                    continue
                new_ops = list(node.ops)
                new_ops[i] = swap()
                clone = ast.Compare(left=node.left, ops=new_ops,
                                    comparators=node.comparators)
                _emit(node, ast.unparse(clone), f"cmp:{type(op).__name__}")
        # 2) and/or 互换
        elif isinstance(node, ast.BoolOp):
            swap = ast.Or if isinstance(node.op, ast.And) else ast.And
            clone = ast.BoolOp(op=swap(), values=node.values)
            _emit(node, ast.unparse(clone), f"bool:{type(node).__name__}")
        # 3) True/False 互换（常数）
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            clone = ast.Constant(value=not node.value)
            _emit(node, ast.unparse(clone), "const:bool")
        # 4) if 守卫恒真/恒假
        elif isinstance(node, ast.If):
            for val, kind in ((ast.Constant(value=False), "if:force-skip"),
                              (ast.Constant(value=True), "if:force-run")):
                clone = ast.If(test=val, body=node.body,
                              orelse=node.orelse)
                _emit(node, ast.unparse(clone), kind)
        # 5) 数值常数边界 ±1（>0 的 int）
        elif isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and not isinstance(node.value, bool) and node.value > 0:
            clone = ast.Constant(value=node.value + 1)
            _emit(node, ast.unparse(clone), "const:int+1")
    # 去重（同一位置的 force-skip/force-run 等可能撞）
    seen: set[str] = set()
    uniq: list[Mutant] = []
    for m in out:
        if m.key() not in seen and m.orig != m.new:
            seen.add(m.key())
            uniq.append(m)
    return uniq


def apply_mutant(path: Path, m: Mutant) -> None:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    ln = lines[m.line - 1]
    lines[m.line - 1] = ln[:m.col] + m.new + ln[m.col + len(m.orig):]
    path.write_text("".join(lines), encoding="utf-8")


def restore(path: Path) -> None:
    subprocess.run(["git", "checkout", "--", str(path)], cwd=REPO,
                   check=False)
    # 同尺寸变异+同秒 mtime 时 git checkout 不触发 pyc 失效（M1-vault 实证
    # 的基建坑）——restore 连 pyc 一并清，杜绝陈旧字节码
    pyc = path.parent / "__pycache__" / (
        path.stem + ".cpython-" + str(sys.version_info[0])
        + str(sys.version_info[1]) + ".pyc")
    pyc.unlink(missing_ok=True)


# ---------------------------------------------------------------- 执行
def run_tests(tests: list[str], timeout: float) -> tuple[int, float]:
    t0 = time.time()
    try:
        r = subprocess.run(
            [PY, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider",
             *tests],
            cwd=REPO, capture_output=True, timeout=timeout,
            env={**os.environ, "LOADN_PROVIDER": "fake"})
        return r.returncode, time.time() - t0
    except subprocess.TimeoutExpired:
        return -1, time.time() - t0


def scan(files: list[str], report_path: Path, from_idx: int = 0,
         max_n: int = 0) -> dict:
    results: dict[str, Report] = {}
    all_survived: list[dict] = []
    for rel in files:
        path = REPO / rel
        tests = TARGET_TESTS.get(rel)
        if not tests:
            print(f"!! {rel}: 无测试映射，跳过（防全量慢）")
            continue
        # 基线：未变异必须全绿（红基线=测试集本身坏）
        rc, dt = run_tests(tests, BASELINE_TIMEOUT_S)
        if rc != 0:
            print(f"!! 基线红 {rel}（rc={rc}）——跳过该文件")
            continue
        mutants = gen_mutants(path)[from_idx:]
        if max_n:
            mutants = mutants[:max_n]
        rep = Report(file=rel, total=len(mutants))
        print(f"== {rel}: {len(mutants)} 变异（from {from_idx}）")
        for i, m in enumerate(mutants):
            apply_mutant(path, m)
            try:
                rc, dt = run_tests(tests, MUT_TIMEOUT_S)
            finally:
                restore(path)
            rec = {"file": m.file, "line": m.line, "kind": m.kind,
                   "orig": m.orig[:120], "new": m.new[:120]}
            if rc == -1:
                rep.timeout.append(rec)
                all_survived.append({**rec, "why": "timeout"})
            elif rc == 0:
                # 复验一次（runner 竞态/环境抖动的假阳防护）
                apply_mutant(path, m)
                try:
                    rc2, _ = run_tests(tests, MUT_TIMEOUT_S)
                finally:
                    restore(path)
                if rc2 == 0:
                    rep.survived.append(rec)
                    all_survived.append({**rec, "why": "tests-green"})
                else:
                    rep.killed += 1   # 复验杀死（首次绿是抖动）
            else:
                rep.killed += 1
            if (i + 1) % 20 == 0:
                print(f"   {i + 1}/{len(mutants)} "
                      f"(杀 {rep.killed} 活 {len(rep.survived)} "
                      f"挂 {len(rep.timeout)})")
        results[rel] = rep
        report_path.write_text(json.dumps(
            {"results": {k: vars(v) for k, v in results.items()},
             "survived": all_survived}, ensure_ascii=False, indent=1))
        kill = rep.killed / rep.total * 100 if rep.total else 100
        print(f"== {rel}: 杀伤率 {kill:.0f}% "
              f"({rep.killed}/{rep.total}，活 {len(rep.survived)} "
              f"挂 {len(rep.timeout)})")
    # 总表
    tot = sum(r.total for r in results.values())
    kil = sum(r.killed for r in results.values())
    print(f"\n总杀伤率：{kil / tot * 100 if tot else 100:.1f}% "
          f"({kil}/{tot})；存活 {len(all_survived)} → {report_path}")
    return {"kill_rate": kil / tot if tot else 1.0,
            "survived": all_survived}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", help="组名（security/writetools/sandbox/session）")
    ap.add_argument("--files", nargs="*", help="显式文件列表")
    ap.add_argument("--report", default=None)
    ap.add_argument("--from-idx", type=int, default=0,
                    help="跳过前 N 个变异（分段跑大文件）")
    ap.add_argument("--max", type=int, default=0,
                    help="本段最多 N 个变异（0=不限）")
    args = ap.parse_args()
    files: list[str] = []
    if args.targets:
        files += GROUPS[args.targets]
    if args.files:
        files += args.files
    if not files:
        ap.error("--targets 或 --files 必给其一")
    REPO.mkdir(exist_ok=True)
    rp = REPO / (args.report or
                 f"reports/mutate-{int(time.time())}.json")
    rp.parent.mkdir(exist_ok=True)
    out = scan(files, rp, from_idx=args.from_idx, max_n=args.max)
    return 0 if out["kill_rate"] >= 0.85 else 1


if __name__ == "__main__":
    raise SystemExit(main())
