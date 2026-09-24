"""T4：loadn/cli/repl.py 指令面单测（原 0%）。

run_repl 是薄壳（input 循环+指令分派+emit 打印）——core/session/compactor
是注入依赖，用最小 stub（不是 mock 被测物：被测的是分派与呈现逻辑）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from loadn.cli.repl import run_repl, trust_preflight


def _bundle(tmp_path: Path, turns=None):
    """最小可用 bundle：run_turn 记录并返回可控 summary。"""
    turns = turns if turns is not None else []

    class _Core:
        cwd = tmp_path
        session = SimpleNamespace(
            session_id="repl-sess", state=SimpleNamespace(todos=[]),
            messages_for_turn=lambda: [],
            last_compact_summary=lambda: "",
            mark_compact=lambda s, tokens_cropped=None: None)
        compactor = None
        settings = SimpleNamespace(context_window=100_000)

        async def run_turn(self, line, emit=None, stop=None):
            turns.append(line)
            return SimpleNamespace(subtype="success", error=None)

    b = SimpleNamespace(core=_Core(), session=_Core.session)
    return b, turns


class _Stop:
    requested = False


def _feed(monkeypatch, lines: list[str]):
    it = iter(lines)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", fake_input)


# ---------------------------------------------------------------- 指令分派
async def test_repl_exit_and_empty_line(tmp_path, monkeypatch, capsys):
    b, turns = _bundle(tmp_path)
    _feed(monkeypatch, ["", "/exit"])
    assert await run_repl(b, None, "text", _Stop()) == 0
    assert turns == []                              # 空行不触发轮


async def test_repl_help_lists_commands(tmp_path, monkeypatch, capsys):
    b, _ = _bundle(tmp_path)
    _feed(monkeypatch, ["/help", "/quit"])
    await run_repl(b, None, "text", _Stop())
    out = capsys.readouterr().out
    for cmd in ("/resume", "/fork", "/compact", "/todos", "/undo"):
        assert cmd in out


async def test_repl_todos_prints_state(tmp_path, monkeypatch, capsys):
    b, _ = _bundle(tmp_path)
    b.core.session.state.todos = [
        SimpleNamespace(status="completed", subject="第一件"),
        SimpleNamespace(status="in_progress", subject="第二件")]
    _feed(monkeypatch, ["/todos", "/exit"])
    await run_repl(b, None, "text", _Stop())
    out = capsys.readouterr().out
    assert "第一件" in out and "第二件" in out


async def test_repl_undo_calls_autocommit(tmp_path, monkeypatch, capsys):
    b, _ = _bundle(tmp_path)
    called = {}
    monkeypatch.setattr(
        "loadn.core.autocommit.undo",
        lambda cwd, sid: called.update(cwd=str(cwd), sid=sid) or "已回滚")
    _feed(monkeypatch, ["/undo", "/exit"])
    await run_repl(b, None, "text", _Stop())
    assert called["sid"] == "repl-sess" and called["cwd"] == str(tmp_path)
    assert "已回滚" in capsys.readouterr().out


async def test_repl_resume_and_fork(tmp_path, monkeypatch, capsys):
    b, _ = _bundle(tmp_path)
    made = {}

    class _FakeSM:
        @staticmethod
        def resume(sid, cwd):
            made["resume"] = (sid, str(cwd))
            return SimpleNamespace(session_id=sid)

        @staticmethod
        def fork(sid, cwd):
            made["fork"] = (sid, str(cwd))
            return SimpleNamespace(session_id=f"fork-of-{sid}")

    monkeypatch.setattr("loadn.core.session.SessionManager", _FakeSM)
    _feed(monkeypatch, ["/resume new-sid", "/fork", "/exit"])
    await run_repl(b, None, "text", _Stop())
    assert made["resume"] == ("new-sid", str(tmp_path))
    assert made["fork"] == ("new-sid", str(tmp_path))
    assert b.session.session_id == "fork-of-new-sid"     # resume 换绑后 fork 当前
    out = capsys.readouterr().out
    assert "已切到会话 new-sid" in out and "已分叉" in out


async def test_repl_compact_with_compactor(tmp_path, monkeypatch, capsys):
    b, _ = _bundle(tmp_path)
    compacted = {}

    class _FakeCompactor:
        last_summary = "压缩摘要"

        async def compact(self, msgs, context_window=0, prev_summary=""):
            compacted.update(n=len(msgs), cw=context_window)
            return [], True

    b.core.compactor = _FakeCompactor()
    _feed(monkeypatch, ["/compact", "/exit"])
    await run_repl(b, None, "text", _Stop())
    assert compacted["cw"] == 100_000 and "已压缩" in capsys.readouterr().out


async def test_repl_compact_without_compactor_skipped(
        tmp_path, monkeypatch, capsys):
    b, turns = _bundle(tmp_path)
    _feed(monkeypatch, ["/compact", "正常轮", "/exit"])
    await run_repl(b, None, "text", _Stop())
    assert turns == ["正常轮"]                      # 无 compactor 不炸不挡


async def test_repl_normal_turn_and_error_path(tmp_path, monkeypatch, capsys):
    class _ErrCore:
        cwd = tmp_path
        session = SimpleNamespace(session_id="s", state=SimpleNamespace(todos=[]))
        compactor = None

        async def run_turn(self, line, emit=None, stop=None):
            return SimpleNamespace(subtype="error_max_turns", error="达到上限")

    b = SimpleNamespace(core=_ErrCore(), session=_ErrCore.session)
    _feed(monkeypatch, ["跑任务", "/exit"])
    assert await run_repl(b, None, "text", _Stop()) == 0
    out = capsys.readouterr().out
    assert "error_max_turns" in out and "达到上限" in out


async def test_repl_turn_exception_continues(tmp_path, monkeypatch, capsys):
    b, turns = _bundle(tmp_path)

    async def boom(self, line, emit=None, stop=None):
        turns.append(line)
        if line == "炸":
            raise RuntimeError("轮异常")
        return SimpleNamespace(subtype="success", error=None)

    b.core.run_turn = boom.__get__(b.core)
    _feed(monkeypatch, ["炸", "再来", "/exit"])
    assert await run_repl(b, None, "text", _Stop()) == 0
    assert turns == ["炸", "再来"]                   # 异常轮不杀 REPL
    assert "轮异常" in capsys.readouterr().out


async def test_repl_stop_flag_reset(tmp_path, monkeypatch):
    """Ctrl-C 打断当轮后 stop.requested 复位（下一轮可跑）。"""
    b, turns = _bundle(tmp_path)

    class _Stop:
        requested = True

    stop = _Stop()

    class _Core(b.core.__class__):
        async def run_turn(self, line, emit=None, stop=None):
            turns.append(line)
            return SimpleNamespace(subtype="success", error=None)

    _feed(monkeypatch, ["第一轮", "/exit"])
    assert await run_repl(b, None, "text", stop) == 0
    assert stop.requested is False


# ---------------------------------------------------------------- 信任门
def test_trust_preflight_pass(monkeypatch):
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate", lambda cwd: (True, ""))
    trust_preflight("/x")                        # 直通不问不打扰


def test_trust_preflight_admit_on_yes(tmp_path, monkeypatch, capsys):
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate",
                        lambda cwd: (False, "unconfirmed-resources"))
    monkeypatch.setattr(trust, "project_root", lambda cwd: tmp_path)
    monkeypatch.setattr(trust, "_resource_files", lambda root: [])
    admitted = {}
    monkeypatch.setattr(trust, "admit",
                        lambda root: admitted.update(root=str(root)))
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    trust_preflight(tmp_path)
    assert admitted["root"] == str(tmp_path)
    assert "已信任" in capsys.readouterr().out


def test_trust_preflight_revoke_on_no(tmp_path, monkeypatch, capsys):
    from loadn.core import trust
    monkeypatch.setattr(trust, "gate",
                        lambda cwd: (False, "unconfirmed-resources"))
    monkeypatch.setattr(trust, "project_root", lambda cwd: tmp_path)
    monkeypatch.setattr(trust, "_resource_files", lambda root: [])
    revoked = {}
    monkeypatch.setattr(trust, "revoke",
                        lambda root: revoked.update(root=str(root)))
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    trust_preflight(tmp_path)
    assert revoked["root"] == str(tmp_path)
    assert "不信任" in capsys.readouterr().out
