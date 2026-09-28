"""CLI 端到端（子进程 -p 全链，LOADN_PROVIDER=fake 零 token）。

argv 契约与 stream-json 事件形状在这里整体校验——宿主引擎接缝
（spawn 的就是这组 argv）以此为准。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _run_cli(args: list[str], cwd: Path, home: Path, fake_dir: Path | None = None,
             timeout: float = 30) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "LOADN_HOME": str(home),
        "LOADN_PROVIDER": "fake",
    }
    if fake_dir is not None:
        env["LOADN_FAKE_DIR"] = str(fake_dir)
    return subprocess.run([sys.executable, "-m", "loadn", *args],
                          cwd=str(cwd), env=env, capture_output=True,
                          text=True, timeout=timeout)


def _events(cp: subprocess.CompletedProcess) -> list[dict]:
    return [json.loads(ln) for ln in cp.stdout.splitlines()
            if ln.strip().startswith("{")]


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / ".fake").mkdir(parents=True)
    return w


def test_cli_version_fast():
    cp = _run_cli(["--version"], cwd=REPO, home=REPO / ".hh_tmp_home")
    assert cp.returncode == 0
    assert "loadn" in cp.stdout


def test_cli_stream_json_happy_path(tmp_path, ws):
    (ws / ".fake" / "reply").write_text("CLI 回复")
    sid = str(uuid.uuid4())
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--session-id", sid, "你好"], cwd=ws, home=tmp_path,
                  fake_dir=ws / ".fake")
    assert cp.returncode == 0, cp.stderr[-500:]
    evs = _events(cp)
    # plan/stream_event 是观测性事件，不计入主契约序列
    types = [e["type"] for e in evs if e["type"] not in ("plan", "stream_event")]
    assert types == ["system", "assistant", "result"]
    assert evs[0]["subtype"] == "init" and evs[0]["session_id"] == sid
    a1 = next(e for e in evs if e["type"] == "assistant")
    assert a1["message"]["content"][0]["text"].startswith("CLI 回复")
    r = evs[-1]
    assert r["subtype"] == "success" and r["num_turns"] == 1
    assert r["usage"]["input_tokens"] > 0
    assert r["modelUsage"] and "inputTokens" in next(iter(r["modelUsage"].values()))
    # transcript 落盘
    ts = tmp_path / "sessions" / sid / "transcript.jsonl"
    assert ts.exists()


def test_cli_prompt_is_last_argv(tmp_path, ws):
    """PROMPT 恒为 argv[-1]（fake-CLI 契约）。"""
    (ws / ".fake" / "reply").write_text("ok")
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--session-id", str(uuid.uuid4()), "末位提示词"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 0
    # fake provider 不校验 argv；契约由本测试的存在 + 宿主侧 e2e 断言保证


def test_cli_session_id_in_use(tmp_path, ws):
    """transcript 已存在的 --session-id → already in use 秒拒（宿主平台
    fresh→resume 翻转分支依赖该文案）。"""
    sid = str(uuid.uuid4())
    (ws / ".fake" / "reply").write_text("第一轮")
    cp1 = _run_cli(["-p", "--output-format", "text",
                    "--session-id", sid, "第一问"],
                   cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp1.returncode == 0
    cp2 = _run_cli(["-p", "--output-format", "text",
                    "--session-id", sid, "再开"],
                   cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp2.returncode == 1
    assert "Session ID already in use" in cp2.stderr


def test_cli_resume_continues(tmp_path, ws):
    sid = str(uuid.uuid4())
    (ws / ".fake" / "reply").write_text("续会话回复")
    cp = _run_cli(["-p", "--output-format", "json", "--resume", sid, "接着说"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 0
    out = json.loads(cp.stdout.strip().splitlines()[-1])
    assert out["type"] == "result" and out["subtype"] == "success"
    assert out["session_id"] == sid


def test_cli_fastfail_exit1(tmp_path, ws):
    (ws / ".fake" / "fastfail").write_text("")
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--session-id", str(uuid.uuid4()), "跑挂"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 1
    assert "Session not found" in cp.stderr + cp.stdout


def test_cli_tools_roundtrip_stream(tmp_path, ws):
    """tools 旋钮：tool_use → tool_result → 最终 text 全链事件。"""
    (ws / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "echo hi"}}))
    (ws / ".fake" / "reply").write_text("执行完了")
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--dangerously-skip-permissions",
                   "--session-id", str(uuid.uuid4()), "跑命令"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 0, cp.stderr[-400:]
    evs = _events(cp)
    types = [e["type"] for e in evs if e["type"] not in ("plan", "stream_event", "todos")]
    assert types == ["system", "assistant", "user", "assistant", "result"]
    a1 = next(e for e in evs if e["type"] == "assistant")
    tu = [b for b in a1["message"]["content"] if b["type"] == "tool_use"][0]
    assert tu["name"] == "Bash" and tu["input"]["command"] == "echo hi"
    u1 = next(e for e in evs if e["type"] == "user")
    tr = u1["message"]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == tu["id"]
    assert "hi" in tr["content"] and not tr["is_error"]   # 真执行了 echo


def test_cli_stream_events_with_verbose(tmp_path, ws):
    """--verbose：stream_event 逐 delta 外发，text 拼接与 assistant 整块一致。"""
    (ws / ".fake" / "reply").write_text("逐字流的回复")
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--session-id", str(uuid.uuid4()), "x"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 0, cp.stderr[-400:]
    evs = _events(cp)
    sevs = [e for e in evs if e["type"] == "stream_event"]
    assert sevs, "应有 stream_event 行"
    assert sevs[0]["parent_tool_use_id"] is None
    assert sevs[0]["event"]["type"] == "message_start"
    assert sevs[-1]["event"]["type"] == "message_stop"
    deltas = "".join(e["event"]["delta"]["text"] for e in sevs
                     if e["event"]["type"] == "content_block_delta"
                     and e["event"]["delta"]["type"] == "text_delta")
    a = next(e for e in evs if e["type"] == "assistant")
    assert deltas == a["message"]["content"][0]["text"] == "逐字流的回复"
    # stream_event 全部位于 assistant 整块之前（增量先行、整块收口）
    assert max(i for i, e in enumerate(evs) if e["type"] == "stream_event") \
        < next(i for i, e in enumerate(evs) if e["type"] == "assistant")


def test_cli_no_stream_events_without_verbose(tmp_path, ws):
    """无 --verbose：不外发 stream_event（与 claude CLI 同位语义）。"""
    (ws / ".fake" / "reply").write_text("普通回复")
    cp = _run_cli(["-p", "--output-format", "stream-json",
                   "--session-id", str(uuid.uuid4()), "x"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 0, cp.stderr[-400:]
    assert not [e for e in _events(cp) if e["type"] == "stream_event"]


def test_cli_todos_event_stream(tmp_path, ws):
    """todos 旋钮：TodoWrite 执行后外发 todos 事件（紧随 tool_result 的 user 行）。"""
    (ws / ".fake" / "todos").write_text(json.dumps(
        [{"content": "检索来源", "status": "pending", "activeForm": "检索来源中"}]))
    (ws / ".fake" / "reply").write_text("清单已建")
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--dangerously-skip-permissions",
                   "--session-id", str(uuid.uuid4()), "列清单"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    assert cp.returncode == 0, cp.stderr[-400:]
    evs = _events(cp)
    types = [e["type"] for e in evs]
    assert "todos" in types
    todo_ev = next(e for e in evs if e["type"] == "todos")
    assert todo_ev["todos"][0]["subject"] == "检索来源"
    assert todo_ev["todos"][0]["status"] == "pending"
    assert types.index("todos") == types.index("user") + 1


def test_cli_bigusage_result(tmp_path, ws):
    (ws / ".fake" / "bigusage").write_text("")
    (ws / ".fake" / "reply").write_text("大用量")
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--session-id", str(uuid.uuid4()), "x"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    r = _events(cp)[-1]
    assert r["usage"]["input_tokens"] == 600_000   # 大上下文轮换阈值测试用


def test_cli_max_turns_gate(tmp_path, ws):
    """--max-turns 1 + tools 旋钮 → error_max_turns。"""
    (ws / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "true"}}))
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--dangerously-skip-permissions",
                   "--max-turns", "1", "--session-id", str(uuid.uuid4()), "x"],
                  cwd=ws, home=tmp_path, fake_dir=ws / ".fake")
    r = _events(cp)[-1]
    assert r["subtype"] == "error_max_turns"
    assert cp.returncode == 1
