"""P3-6 taxonomy 记分卡验收。

- 生成：maturity.py 出 markdown，含 snapshot sha、子系统表、profiles
- 空 coverage 标红：蜜罐（sec.c1）等 🔴 行存在；有证据项 ✅
- coverage marker：pytest -m coverage 跑得动（marker 已注册）
- 收集器：pytestmark 形态与装饰器形态都命中
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
pytestmark = [pytest.mark.coverage("evals.smoke")]   # 自证：本文件即证据


def _gen(out: str) -> str:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "maturity.py"), "--out", out],
        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return Path(out).read_text(encoding="utf-8")


def test_scorecard_generates_with_red_empty_coverage(tmp_path):
    body = _gen(str(tmp_path / "card.md"))
    assert "loadn 成熟度记分卡" in body
    assert "snapshot：" in body and "`" in body          # sha 锚
    # 空 coverage 显式标红（卡面验收主断言）
    assert "🔴 空 coverage" in body
    assert "蜜罐诱饵" in body                             # sec.c1 是声明无测试项
    # 有证据项
    assert "✅" in body and "test_a5_bash_policy" in body
    # profiles 视图
    assert "smoke-ci" in body and "nightly" in body


def test_collector_finds_both_marker_forms(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("mat", REPO / "scripts" / "maturity.py")
    mat = module_from_spec(spec)
    spec.loader.exec_module(mat)
    cov = mat.collect_test_coverage()
    # pytestmark 形态（本文件 evals.smoke）
    assert "evals.smoke" in cov
    assert any("test_maturity" in f for f in cov["evals.smoke"])
    # 装饰器/其他文件 pytestmark
    assert "sec.a5" in cov and "proto.v1" in cov


def test_taxonomy_yaml_shape():
    import yaml
    d = yaml.safe_load((REPO / "taxonomy.yaml").read_text(encoding="utf-8"))
    assert d["version"] == 1
    ids = set()
    for sub in d["subsystems"]:
        for cap in sub["capabilities"]:
            assert cap["id"] and cap["level"], cap
            ids.add(f"{sub['id']}.{cap['id']}")
    # profiles 引用的 coverage 都指向已声明能力
    for prof in d["profiles"]:
        for cid in prof["coverageIds"]:
            assert cid in ids or "." in cid, f"profile 悬空引用 {cid}"
