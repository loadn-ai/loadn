"""bash_policy 突变存活补测（M1-bash_policy 高价值盲区）。

存活变异（17 测试全绿漏杀）：
- L186/L209 严格 > → >=：同 severity 规则并存时**后来者覆盖先者**——
  决策合并的平局语义从未对赌（多条同 severity 规则命中时取哪条的
  reason 应是确定性的：第一条）
- L259 `not path.exists() and data["bash_rules"] == []` → or：
  新建分支误判——存在空规则 policy.json 的 and/or 语义
- L88 `_UNSAFE_EXPANSIONS` in→not in：不安全展开检测反转
"""
from __future__ import annotations

import json
from pathlib import Path

from loadn import bash_policy as bp


def _policy_file(tmp_path: Path, rules: list[dict]) -> None:
    p = tmp_path / ".loadn" / "policy.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"bash_rules": rules}), encoding="utf-8")


def _rules_of(tmp_path: Path) -> list:
    return bp.parse_rules(bp._read_policy(
        tmp_path / ".loadn" / "policy.json")["bash_rules"])


# ---------------------------------------------------------- 平局语义（L209）
def test_same_severity_first_rule_wins(tmp_path):
    """两条 allow 规则同 severity → 取**第一条**的 reason（确定性平局）。"""
    _policy_file(tmp_path, [
        {"prefix": ["pip", "install"], "decision": "allow",
         "justification": "第一条"},
        {"prefix": ["pip"], "decision": "allow",
         "justification": "第二条"},
    ])
    rules = _rules_of(tmp_path)
    v = bp._best(rules, ["pip", "install", "x"])
    assert v is not None and v.decision == "allow"
    assert "第一条" in v.reason          # > 语义：后来同级不覆盖


def test_severity_more_specific_ask_wins(tmp_path):
    """severity 序对赌：更具体的 ask 规则在 deny 允许集中胜出。"""
    _policy_file(tmp_path, [
        {"prefix": ["sudo"], "decision": "deny"},
        {"prefix": ["sudo", "ls"], "decision": "ask"},
    ])
    rules = _rules_of(tmp_path)
    v = bp._best(rules, ["sudo", "ls"])
    assert v is not None and v.decision == "deny"      # deny severity 最高


# ---------------------------------------------------------- 新建分支（L259）
def test_amend_policy_on_existing_empty(tmp_path):
    """已存在但空规则的 policy.json：追加规则正常落盘。"""
    _policy_file(tmp_path, [])
    bp.amend_policy(tmp_path, prefix=["make"], decision="allow",
                    justification="构建", source="test")
    data = json.loads((tmp_path / ".loadn" / "policy.json").read_text())
    assert len(data["bash_rules"]) == 1
    assert data["bash_rules"][0]["prefix"] == ["make"]


def test_amend_policy_new_file_creates(tmp_path):
    """不存在的 policy.json：新建骨架（迁移路径）+ 版本号 + 规则。"""
    bp.amend_policy(tmp_path, prefix=["pytest"], decision="allow",
                    justification="测试", source="test")
    data = json.loads((tmp_path / ".loadn" / "policy.json").read_text())
    assert any(r["prefix"] == ["pytest"] for r in data["bash_rules"])
    assert data.get("version") == bp.POLICY_VERSION


def test_amend_policy_idempotent(tmp_path):
    """同前缀重复回写：不重复（幂等）。"""
    bp.amend_policy(tmp_path, prefix=["ls"], decision="allow", source="t")
    bp.amend_policy(tmp_path, prefix=["ls"], decision="allow", source="t")
    data = json.loads((tmp_path / ".loadn" / "policy.json").read_text())
    assert sum(1 for r in data["bash_rules"] if r["prefix"] == ["ls"]) == 1


# ---------------------------------------------------------- 不安全展开（L88）
def test_unsafe_expansion_detected():
    """命令替换/参数展开 → 不放行（fail-closed：ask 或 block，非 allow）。"""
    rules = bp.parse_rules([
        {"prefix": ["echo"], "decision": "allow"}])
    v = bp.evaluate(rules, "echo $(rm -rf /)")
    assert v.decision != "allow"


def test_plain_allowed_command_passes():
    rules = bp.parse_rules([
        {"prefix": ["echo"], "decision": "allow"}])
    v = bp.evaluate(rules, "echo hello")
    assert v.decision == "allow"
