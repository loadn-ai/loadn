"""T4：loadn/cli/main.py 进程内单测（原 0%——test_e2e_cli 走子进程
coverage 不追，这里补解析矩阵/子命令分派/守卫/-p 主链进程内真跑）。
"""
from __future__ import annotations

import json

import pytest

from loadn.cli.main import build_parser, main


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LOADN_PROVIDER", "fake")
    monkeypatch.chdir(tmp_path)
    # 进程内跑 main 的收尾清扫会按 ppid 杀 pytest 的任何子进程——
    # 它是 CLI 进程的进程卫生，不是被测行为
    monkeypatch.setattr("loadn.cli.main._kill_my_children", lambda: None)
    return monkeypatch


# ---------------------------------------------------------------- 解析矩阵
def test_parser_matrix():
    a = build_parser().parse_args([
        "-p", "--output-format", "stream-json", "--protocol", "v2",
        "--model", "glm-5.3", "--effort", "high", "--max-turns", "7",
        "--resume", "sid-1", "--fork", "--yolo", "--no-compact",
        "--grind", "--budget-minutes", "30", "提示词"])
    assert a.print_mode and a.output_format == "stream-json"
    assert a.protocol == "v2" and a.model == "glm-5.3"
    assert a.effort == "high" and a.max_turns == 7
    assert a.resume_id == "sid-1" and a.fork and a.yolo
    assert a.no_compact and a.grind and a.budget_minutes == 30
    assert a.prompt == "提示词"
    # 别名与默认
    b = build_parser().parse_args(["--variant", "low", "x"])
    assert b.effort == "low"                       # --variant → effort
    c = build_parser().parse_args(["x"])
    assert c.protocol == "v1" and c.permission_mode is None


def test_parser_disallowed_tools_repeatable():
    """--disallowedTools（claude CLI 同名同义）：可重复累积；缺省 None——
    build_agent 侧 disallow=None 与空清单同形（旧调用零改动）。"""
    a = build_parser().parse_args([
        "--disallowedTools", "mcp__sandbox__sandbox_execute_bash",
        "--disallowedTools", "WebSearch", "x"])
    assert a.disallowed_tools == ["mcp__sandbox__sandbox_execute_bash",
                                  "WebSearch"]
    assert build_parser().parse_args(["x"]).disallowed_tools is None


def test_parser_choices_reject_bad():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--output-format", "yaml", "x"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--protocol", "v9", "x"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--permission-mode", "yolo", "x"])


def test_version_flag(capsys):
    from loadn import __version__
    with pytest.raises(SystemExit) as ei:
        build_parser().parse_args(["--version"])
    assert ei.value.code == 0
    assert __version__ in capsys.readouterr().out


# ---------------------------------------------------------------- 子命令分派
def test_subcommand_dispatch(tmp_path, _env):
    calls = {}

    def _stub(key, rc):
        def f(*a):
            calls[key] = a
            return rc
        return f

    _env.setattr("loadn.cli.skills_lock.main", _stub("skills", 3))
    _env.setattr("loadn.transport.daemon.main", _stub("daemon", 4))
    _env.setattr("loadn.transport.bridge.main", _stub("bridge", 5))
    _env.setattr("loadn.cli.auth.main", _stub("auth", 6))
    assert main(["skills", "lock", "--check"]) == 3
    assert calls["skills"] == (["lock", "--check"],)    # argv[1:] 转发
    assert main(["daemon"]) == 4 and calls["daemon"] == ()
    assert main(["daemon-bridge"]) == 5 and calls["bridge"] == ()
    assert main(["auth", "status"]) == 6
    assert calls["auth"] == (["status"],)


def test_guard_stream_json_needs_print(_env, capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("x"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--output-format", "stream-json"]) == 2
    assert "-p" in capsys.readouterr().err


# ---------------------------------------------------------------- -p 主链进程内
def test_main_print_json_roundtrip(tmp_path, _env, capsys):
    sid = "t4a00000-0000-4000-8000-000000000001"
    rc = main(["-p", "--output-format", "json", "--session-id", sid, "干活"])
    assert rc == 0
    out = capsys.readouterr().out
    d = json.loads(out.strip().splitlines()[-1])     # json 模式=单行结果
    assert d["type"] == "result" and d["subtype"] == "success"
    assert d["session_id"] == sid


def test_main_stdin_prompt_when_not_tty(tmp_path, _env, monkeypatch, capsys):
    """无 prompt 参数且 stdin 非 tty → 读 stdin 全文当提示词。"""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("从标准输入来的任务"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    sid = "t4b00000-0000-4000-8000-000000000002"
    rc = main(["-p", "--output-format", "json", "--session-id", sid])
    assert rc == 0
    d = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert d["type"] == "result"


def test_main_session_lock_conflict(tmp_path, _env, capsys):
    """会话锁：他进程持锁 → 秒拒（Session ID already in use——契约 §3）。"""
    import subprocess
    holder = subprocess.Popen(["sleep", "30"])     # 真活进程持锁
    try:
        sid = "t4c00000-0000-4000-8000-000000000003"
        (tmp_path / "home" / "sessions" / sid).mkdir(parents=True)
        (tmp_path / "home" / "sessions" / sid / "lock").write_text(str(holder.pid))
        rc = main(["-p", "--output-format", "json", "--session-id", sid, "干活"])
        assert rc == 1
        assert "already in use" in capsys.readouterr().err
    finally:
        holder.terminate()
        holder.wait()


def test_main_stale_lock_reclaimed(tmp_path, _env, capsys):
    """死进程的残留锁 → 视为陈旧回收（不自锁死会话）。"""
    sid = "t4d00000-0000-4000-8000-000000000004"
    (tmp_path / "home" / "sessions" / sid).mkdir(parents=True)
    (tmp_path / "home" / "sessions" / sid / "lock").write_text("999999")
    rc = main(["-p", "--output-format", "json", "--session-id", sid, "干活"])
    assert rc == 0                                  # 陈旧锁不挡新跑
