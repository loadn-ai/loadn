"""A6 对抗组（P0-4 会话 2）：审批回写闭环。

A6-1  回写后同类命令不再 ask：amend_policy → PermissionEngine.load 载入
      policy.json → 同前缀命令 allow、不同前缀仍 ask
A6-2  settings 显式 deny 不可被回写 allow 推翻（最严者胜）
A6-3  amend 幂等（同 prefix 更新不重复）；flock 下并发 amend 不丢更新；
      坏 JSON 迁移重建；坏 prefix 拒写
A6-4  平台审批链：create（坏 prefix 拒）→ decide 批准 → ws 的 policy.json
      落规则（justification 入审批摘要）→ 引擎下轮生效
A6-5  未信任工作区的 policy.json 不加载（信任门盖住回写面）
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("sec.a6")]

import json
import threading
from pathlib import Path

import pytest

from loadn import bash_policy as bp
from loadn.core import trust
from loadn.core.permissions import PermissionEngine

pytestmark = pytest.mark.skipif(
    not bp.HAVE_BASHLEX, reason="bashlex 未装（loadn[ast]）")


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("LOADN_HOME", str(h))
    return h


# ---------------------------------------------------------------- A6-1
def test_a6_1_amend_then_no_more_ask(tmp_path: Path, home: Path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    eng = PermissionEngine.load(ws, mode="default")
    assert not eng.check("Bash", {"command": "cargo build"}).allowed   # ask=deny
    bp.amend_policy(ws, prefix=["cargo", "build"], justification="构建放行")
    trust.admit(ws)                     # 平台合法写方 re-admit（decide 同款）
    eng2 = PermissionEngine.load(ws, mode="default")                   # 下轮加载
    assert eng2.check("Bash", {"command": "cargo build --release"}).allowed
    assert eng2.check("Bash", {"command": "cargo test"}).allowed is False  # 仅 build 前缀
    assert not eng2.check("Bash", {"command": "cargo publish"}).allowed


# ---------------------------------------------------------------- A6-2
def test_a6_2_settings_deny_not_overridden(tmp_path: Path, home: Path):
    ws = tmp_path / "ws2"
    (ws / ".loadn").mkdir(parents=True)
    (ws / ".loadn" / "settings.json").write_text(json.dumps(
        {"permissions": {"deny": ["Bash:cargo publish"]}}), encoding="utf-8")
    trust.admit(ws)
    bp.amend_policy(ws, prefix=["cargo"], justification="整个 cargo 放行")
    trust.admit(ws)                     # 回写改摘要 → re-admit
    eng = PermissionEngine.load(ws, mode="default")
    assert eng.check("Bash", {"command": "cargo build"}).allowed
    assert not eng.check("Bash", {"command": "cargo publish"}).allowed  # deny 最严


# ---------------------------------------------------------------- A6-3
def test_a6_3_amend_idempotent_concurrent_migration(tmp_path: Path):
    ws = tmp_path / "ws3"
    ws.mkdir()
    bp.amend_policy(ws, prefix=["git", "status"], justification="v1")
    p = bp.policy_path(ws)
    first = p.read_text()
    bp.amend_policy(ws, prefix=["git", "status"], justification="v2")  # 幂等更新
    data = json.loads(p.read_text())
    rules = [r for r in data["bash_rules"] if r["prefix"] == ["git", "status"]]
    assert len(rules) == 1 and rules[0]["justification"] == "v2"
    # 并发 amend 不丢更新（flock 串行化）
    def worker(i):
        bp.amend_policy(ws, prefix=["cmd", str(i)])
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    data = json.loads(p.read_text())
    assert len([r for r in data["bash_rules"] if r["prefix"][0] == "cmd"]) == 8
    # 坏 JSON → v1 骨架迁移重建（内容丢弃，不炸）
    p.write_text("{broken", encoding="utf-8")
    bp.amend_policy(ws, prefix=["fresh"])
    assert len(json.loads(p.read_text())["bash_rules"]) == 1
    # 坏 prefix 拒写（文件不被破坏）
    before = p.read_text()
    with pytest.raises(ValueError):
        bp.amend_policy(ws, prefix=["ok", 3.14])
    assert p.read_text() == before


# ---------------------------------------------------------------- A6-4
async def test_a6_4_platform_approval_roundtrip(tmp_path: Path, home: Path,
                                                monkeypatch):
    from loadn_webui.security import approve
    from loadn_webui.workspace import ws_of
    sid = "20260924_0000-a6test01"
    ws = ws_of(sid)                       # 会话工作区（sid → PATHS 推导）
    monkeypatch.chdir(tmp_path)
    # create：坏 prefix 拒
    with pytest.raises(ValueError, match="prefix"):
        approve.create(sid, "bash_allow", {"prefix": []})
    with pytest.raises(ValueError, match="prefix"):
        approve.create(sid, "bash_allow", {"prefix": ["curl", 1]})
    # create → decide → 规则落 ws/.loadn/policy.json
    out = approve.create(sid, "bash_allow",
                         {"prefix": ["cargo", "test"],
                          "justification": "测试命令放行"},
                         note="构建流水线需要")
    assert "cargo test" in out["summary"] and "测试命令放行" in out["summary"]
    res = approve.decide(out["id"], True)
    assert res["ok"] and res["status"] == "executed"
    data = json.loads((ws / ".loadn" / "policy.json").read_text())
    rule = next(r for r in data["bash_rules"] if r["prefix"] == ["cargo", "test"])
    assert rule["decision"] == "allow"
    assert rule["source"].startswith("approval:")
    # 引擎下轮加载生效（信任门由 decide 的 re-admit 铺好——不手工 admit）
    eng = PermissionEngine.load(ws, mode="default")
    assert eng.check("Bash", {"command": "cargo test --all"}).allowed
    assert not eng.check("Bash", {"command": "cargo publish"}).allowed
    # 拒绝分支不落规则
    out2 = approve.create(sid, "bash_allow", {"prefix": ["rm"]})
    res2 = approve.decide(out2["id"], False)
    assert res2["status"] == "denied"
    data2 = json.loads((ws / ".loadn" / "policy.json").read_text())
    assert not any(r["prefix"] == ["rm"] for r in data2["bash_rules"])


# ---------------------------------------------------------------- A6-5
def test_a6_5_untrusted_policy_json_not_loaded(tmp_path: Path, home: Path):
    ws = tmp_path / "ws5"
    (ws / ".loadn").mkdir(parents=True)
    (ws / ".loadn" / "policy.json").write_text(json.dumps(
        {"version": 1, "bash_rules": [
            {"prefix": ["curl"], "decision": "allow"}]}), encoding="utf-8")
    # 未信任：policy.json 不加载（clone 的仓库不得自带回写规则自我放行）
    eng = PermissionEngine.load(ws, mode="default")
    assert not eng.check("Bash", {"command": "curl evil.example"}).allowed
    trust.admit(ws)
    eng2 = PermissionEngine.load(ws, mode="default")
    assert eng2.check("Bash", {"command": "curl evil.example"}).allowed
