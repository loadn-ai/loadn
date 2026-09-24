"""P3-2 evals 发布门验收。

- smoke suite 全绿且生成报告文件
- **故意破坏能红**：临时注入一个必然失败的场景 → exit 1 且报告含 ❌
- 判定器单元：文件断言（contains/equals/missing）/transcript 断言
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("evals.smoke")]

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(suite: str = "smoke") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "evals" / "run.py"), "--suite", suite],
        capture_output=True, text=True, timeout=120, cwd=str(REPO))


def test_smoke_suite_green_with_report():
    p = _run()
    assert p.returncode == 0, p.stdout + p.stderr
    assert "✅" in p.stdout and "❌" not in p.stdout
    assert "报告 →" in p.stdout
    reports = sorted((REPO / "evals" / "reports").glob("*-smoke.md"))
    assert reports, "报告未落盘"
    body = reports[-1].read_text(encoding="utf-8")
    assert "通过 **5** / 失败 **0**" in body


def test_broken_scenario_gates_red(tmp_path, monkeypatch):
    """注入一个必然失败的场景：exit 1 + 报告 ❌（发布阻断语义）。"""
    scen = REPO / "evals" / "scenarios" / "zz-broken-check.yaml"
    scen.write_text("""name: zz-broken-check
suites: [smoke]
description: 故意失败——验证门会红
files:
  x.txt: "original"
tools_enabled: [Write]
script:
  - - tool: Write
      input:
        file_path: "x.txt"
        content: "rewritten"
assert_files:
  - path: x.txt
    contains: ["never-appears"]
""", encoding="utf-8")
    try:
        p = _run()
        assert p.returncode == 1
        assert "❌ zz-broken-check" in p.stdout
        assert "never-appears" in p.stdout       # 失败原因可读
    finally:
        scen.unlink(missing_ok=True)


# ---------------------------------------------------------------- 判定器单元
def test_judge_files_branches(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("ev", REPO / "evals" / "run.py")
    ev = module_from_spec(spec)
    spec.loader.exec_module(ev)
    (tmp_path / "a.txt").write_text("hello world")
    assert ev.judge_files(tmp_path, [{"path": "a.txt",
                                      "contains": ["hello"]}]) == []
    assert ev.judge_files(tmp_path, [{"path": "a.txt",
                                      "contains": ["bye"]}]) != []
    assert ev.judge_files(tmp_path, [{"path": "missing.txt",
                                      "contains": ["x"]}]) != []
    assert ev.judge_files(tmp_path, [{"path": "missing.txt",
                                      "missing": True}]) == []
    assert ev.judge_files(tmp_path, [{"path": "a.txt",
                                      "equals": "hello world"}]) == []
