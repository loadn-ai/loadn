"""P2-3 PROTOCOL v2 契约测试（v1/v2 双跑；旧宿主行为不变）。

- manifest：v1 事件 8 个 since=1；v2 增量 3 个；result durable；
  桥映射（tool_use_failure→user；permission_*→None）
- v1 默认流不含 v2 事件（旧宿主零感知——现有 webui 不改行为不变）
- --protocol v2：权限拒绝时 permission_request 外发（带 params_hash）
  且 v1 桥不重复外发 permission_*；tool_result is_error 照常（桥语义）
- durable 标记自洽（P2-2 选择性落盘的输入）
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.coverage("proto.v2")]

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from loadn.protocol import MANIFEST, is_v1, load_manifest, v1_bridge


def _run_engine(args: list[str], cwd: Path, fake_dir: Path) -> list[dict]:
    env = {"LOADN_PROVIDER": "fake", "LOADN_FAKE_DIR": str(fake_dir),
           "LOADN_HOME": str(cwd / "home"), "PATH": "/usr/bin:/bin",
           "HOME": str(cwd)}
    p = subprocess.run(
        [sys.executable, "-m", "loadn", *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60)
    out = []
    for ln in p.stdout.splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def _mk_fake(fake_dir: Path):
    fake_dir.mkdir(parents=True, exist_ok=True)
    (fake_dir / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "echo hi"}}))


# ---------------------------------------------------------------- manifest
def test_manifest_shape():
    m = load_manifest()
    assert m["version"] == 2
    evs = m["events"]
    v1_events = [k for k, v in evs.items() if v["since"] == 1]
    v2_events = [k for k, v in evs.items() if v["since"] == 2]
    assert set(v1_events) == {"system", "assistant", "user", "stream_event",
                              "steer", "plan", "todos", "result"}
    assert set(v2_events) == {"permission_request", "permission_result",
                              "tool_use_failure"}
    # durable：v1 仅 result；v2 permission_request（审计）
    assert [k for k, v in evs.items() if v["durable"]] == \
        ["result", "permission_request"]
    assert all(v["latest"] == 2 for v in evs.values())


def test_v1_bridge_map():
    assert v1_bridge("tool_use_failure") == "user"
    assert v1_bridge("permission_request") is None
    assert v1_bridge("permission_result") is None
    assert is_v1("result") and not is_v1("permission_request")
    # manifest.json 与代码内声明一致（落盘版=真源，代码版=缺省）
    assert load_manifest() == MANIFEST


# ---------------------------------------------------------------- v1 默认流
def test_v1_stream_unchanged(tmp_path):
    """默认（无 --protocol）：事件类型集合=v1 白名单——旧宿主零感知。"""
    _mk_fake(tmp_path / ".fake")
    out = _run_engine(
        ["-p", "--output-format", "stream-json", "--session-id",
         "v1c0ffee-0000-4000-8000-000000000001", "干活"],
        tmp_path, tmp_path / ".fake")
    types = {e["type"] for e in out}
    assert types <= {"system", "assistant", "user", "stream_event",
                     "steer", "plan", "todos", "result"}
    assert "result" in types and "permission_request" not in types


# ---------------------------------------------------------------- v2 流
def _deny_tool_run(tmp_path, protocol: str) -> list[dict]:
    """走真权限引擎：deny Bash → permission_request 上抛（v2）。"""
    _mk_fake(tmp_path / ".fake")
    (tmp_path / ".loadn").mkdir(exist_ok=True)
    (tmp_path / ".loadn" / "settings.json").write_text(json.dumps(
        {"permissions": {"deny": ["Bash"]}}))
    import os

    from loadn.core import trust
    os.environ["LOADN_HOME"] = str(tmp_path / "home")
    trust.admit(tmp_path)               # 项目规则过信任门
    return _run_engine(
        ["-p", "--output-format", "stream-json",
         *(["--protocol", protocol] if protocol == "v2" else []),
         "--permission-mode", "default", "--session-id",
         "v2c0ffee-0000-4000-8000-000000000002", "干活"],
        tmp_path, tmp_path / ".fake")


def test_v2_emits_permission_request(tmp_path):
    out = _deny_tool_run(tmp_path, "v2")
    types = [e["type"] for e in out]
    assert "permission_request" in types
    pr = next(e for e in out if e["type"] == "permission_request")
    assert pr["tool"] == "Bash" and pr["params_hash"]
    assert "权限拒绝" in pr["reason"] or pr["reason"]
    # v1 桥：permission_* 不双发；tool_result is_error 照常（user 事件）
    assert types.count("permission_request") == 1
    users = [e for e in out if e["type"] == "user"]
    assert any(any(b.get("is_error") for b in u["message"]["content"])
               for u in users)


def test_v1_same_run_no_permission_events(tmp_path):
    """同场景 v1：无 permission_request（对照——增量只在 v2）。"""
    out = _deny_tool_run(tmp_path, "v1")
    assert all(e["type"] != "permission_request" for e in out)
    # 拒绝语义仍以 tool_result is_error 到达（v1 宿主既有行为）
    users = [e for e in out if e["type"] == "user"]
    assert users and any(
        any(b.get("is_error") for b in u["message"]["content"]) for u in users)
