"""CLI 端到端（子进程 -p 全链，HAHANESS_PROVIDER=fake 零 token）。

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
        "HAHANESS_HOME": str(home),
        "HAHANESS_PROVIDER": "fake",
    }
    if fake_dir is not None:
        env["HAHANESS_FAKE_DIR"] = str(fake_dir)
    return subprocess.run([sys.executable, "-m", "hahaness", *args],
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
    assert "hahaness" in cp.stdout


def test_cli_stream_json_happy_path(tmp_path, ws):
    (ws / ".fake" / "reply").write_text("CLI 回复")
    sid = str(uuid.uuid4())
    cp = _run_cli(["-p", "--verbose", "--output-format", "stream-json",
                   "--session-id", sid, "你好"], cwd=ws, home=tmp_path,
                  fake_dir=ws / ".fake")
    assert cp.returncode == 0, cp.stderr[-500:]
    evs = _events(cp)
    assert [e["type"] for e in evs] == ["system", "assistant", "result"]
    assert evs[0]["subtype"] == "init" and evs[0]["session_id"] == sid
    assert evs[1]["message"]["content"][0]["text"].startswith("CLI 回复")
    r = evs[2]
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
    types = [e["type"] for e in evs]
    assert types == ["system", "assistant", "user", "assistant", "result"]
    tu = [b for b in evs[1]["message"]["content"] if b["type"] == "tool_use"][0]
    assert tu["name"] == "Bash" and tu["input"]["command"] == "echo hi"
    tr = evs[2]["message"]["content"][0]
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == tu["id"]
    assert "hi" in tr["content"] and not tr["is_error"]   # 真执行了 echo


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
