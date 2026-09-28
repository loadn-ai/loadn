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


# ================================================================ T3 补全
def test_v2_emits_tool_use_failure(tmp_path):
    """T3 真跑对赌：v2 下工具失败 → 原生 tool_use_failure（此前 manifest
    声明但引擎从未发出——补的是实现不是测试）。"""
    out = _deny_tool_run(tmp_path, "v2")
    types = [e["type"] for e in out]
    assert "tool_use_failure" in types
    tf = next(e for e in out if e["type"] == "tool_use_failure")
    assert tf["tool"] == "Bash" and tf["tool_use_id"]
    assert "权限拒绝" in tf["reason"] or tf["reason"]
    # 失败源=权限拒时 permission_request 与 tool_use_failure 成对（先请求后终态）
    assert types.index("permission_request") < types.index("tool_use_failure")
    # tool_result is_error 仍照发（v1 桥语义不因原生行改变）
    users = [e for e in out if e["type"] == "user"]
    assert any(any(b.get("is_error") for b in u["message"]["content"])
               for u in users)


def test_v1_no_tool_use_failure_native(tmp_path):
    """同场景 v1：无原生 tool_use_failure 行（桥=user 的 is_error 回填）。"""
    out = _deny_tool_run(tmp_path, "v1")
    assert all(e["type"] != "tool_use_failure" for e in out)
    users = [e for e in out if e["type"] == "user"]
    assert users and any(
        any(b.get("is_error") for b in u["message"]["content"]) for u in users)


def test_todos_event_shape(tmp_path):
    """todos 事件 shape 对赌：{todos:[{content,status,activeForm}]}——
    TodoWrite 成功即外发（清单变更=事件）。"""
    fake = tmp_path / ".fake"
    fake.mkdir(parents=True)
    (fake / "todos").write_text(json.dumps(
        [{"content": "第一步", "status": "in_progress",
          "activeForm": "做第一步"}]))
    out = _run_engine(
        ["-p", "--output-format", "stream-json", "--dangerously-skip-permissions",
         "--session-id", "tdc0ffee-0000-4000-8000-000000000003", "干活"],
        tmp_path, fake)   # bypass：headless 下 TodoWrite 默认 ask→deny
    # 顺手对赌 plan 事件 shape（planner 判定随首轮外发）
    plans = [e for e in out if e["type"] == "plan"]
    assert plans and isinstance(plans[0].get("parallelizable"), bool)
    todos = [e for e in out if e["type"] == "todos"]
    assert todos, "TodoWrite 成功后必须外发 todos 事件"
    items = todos[0]["todos"]
    assert items and items[0]["subject"] == "第一步"      # Todo.to_dict 形态
    assert items[0]["status"] in ("pending", "in_progress", "completed")
    assert items[0]["id"] and "session_id" in todos[0]


def test_result_event_diffs_shape(tmp_path):
    """turn 级 diff（P3-1）契约面：transcript result 事件可选 diffs 字段
    shape={path,hash,lines}（v1 消费方忽略未知键——存在即须成形）。"""
    fake = tmp_path / ".fake"
    fake.mkdir(parents=True)
    (fake / "tools").write_text(json.dumps(
        [{"name": "Write", "input": {"file_path": str(tmp_path / "w.md"),
                                     "content": "# hi\n"}}] * 2))
    # Write 控制文件形态：list[dict]（fake _read_tool_calls）
    out = _run_engine(
        ["-p", "--output-format", "stream-json", "--dangerously-skip-permissions",
         "--session-id", "dfc0ffee-0000-4000-8000-000000000004", "干活"],
        tmp_path, fake)
    assert any(e["type"] == "result" for e in out)     # turn 真的跑完
    tlog = (tmp_path / "home" / "sessions"
            / "dfc0ffee-0000-4000-8000-000000000004" / "transcript.jsonl")
    results = [json.loads(ln) for ln in tlog.read_text().splitlines()
               if json.loads(ln).get("type") == "result"]
    assert results
    diffs = results[-1]["payload"].get("diffs")
    assert diffs, "写路径 turn 的 result 事件应带 diffs"
    d = diffs[0]
    assert set(d) >= {"path", "hash", "lines"} and d["path"].endswith("w.md")
    assert isinstance(d["lines"], str) and d["lines"]


async def test_platform_consumes_v2_durable(monkeypatch):
    """T3 durable 落盘对赌：Engine._consume 转发 permission_request /
    tool_use_failure → publish（session_events+SSE）——此前被静默丢弃，
    permission_request 的 durable 契约（manifest）落空。"""
    import time as _time

    from loadn_webui.claude_runner import StopHandle
    from loadn_webui.engine import ActiveTurn, Engine
    eng = Engine()
    got: list[tuple[str, dict]] = []

    def fake_publish(sid, type_, data, turn_id=None):
        got.append((type_, data))

    monkeypatch.setattr(eng, "publish", fake_publish)
    at = ActiveTurn(turn_id=7, session_id="sid-x", stop=StopHandle(),
                    started_at=_time.time())
    await eng._consume("sid-x", 7, at, {
        "type": "permission_request", "session_id": "sid-x",
        "tool": "Bash", "input": {"command": "x"},
        "reason": "权限拒绝：deny 规则", "params_hash": "ab12"})
    await eng._consume("sid-x", 7, at, {
        "type": "tool_use_failure", "session_id": "sid-x",
        "tool": "Bash", "tool_use_id": "tu_1", "reason": "权限拒绝"})
    kinds = [t for t, _ in got]
    assert kinds == ["permission_request", "tool_use_failure"]
    assert got[0][1]["turn_id"] == 7 and got[0][1]["params_hash"] == "ab12"
    assert got[1][1]["tool_use_id"] == "tu_1"
