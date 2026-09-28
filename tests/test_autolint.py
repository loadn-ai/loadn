"""P3-9 auto-lint 验证环验收。

- 坏代码编辑→ctx.extras 有失败摘要（行号）；下一 turn messages 头部
  注入 system-reminder（run_turn 全链）
- 好代码→无积压；非代码后缀→跳过；显式关（auto_lint false）→跳过
- 配置面：显式 auto_lint_cmd 命中（echo 伪装）；无 ruff/pyflakes 环境
  → 静默跳过（monkeypatch which→None）
- auto_test 默认关；显式开+配置才跑
- 失败不 block 工具结果（Edit 输出照常成功）
"""
from __future__ import annotations

import json
from pathlib import Path

from loadn.core import autolint as al
from loadn.tools.base import ToolContext
from loadn.tools.edit import EditTool
from loadn.tools.read import ReadTool
from loadn.tools.write import WriteTool


def _cfg(cwd: Path, **kv) -> None:
    (cwd / ".loadn").mkdir(exist_ok=True)
    (cwd / ".loadn" / "settings.json").write_text(json.dumps(kv),
                                                    encoding="utf-8")


async def _edit(path: Path, old: str, new: str, ctx) -> str:
    await ReadTool().execute({"file_path": str(path)}, ctx)
    return await EditTool().execute(
        {"file_path": str(path), "old_string": old, "new_string": new}, ctx)


# ---------------------------------------------------------------- 主链
async def test_bad_edit_produces_reminder(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("x = 1\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    out = await _edit(f, "x = 1", "def broken(:\n    pass", ctx)
    assert "已编辑" in out                          # 失败不 block 工具结果
    pending = ctx.extras.get("auto_lint") or []
    assert pending and "code.py" in pending[0]["output"]   # ruff 指出文件
    assert pending[0]["path"].endswith("code.py")


async def test_good_edit_no_reminder(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await _edit(f, "x = 1", "x = 2", ctx)
    assert not ctx.extras.get("auto_lint")


async def test_non_code_and_disabled(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("a\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await _edit(f, "a", "b", ctx)
    assert not ctx.extras.get("auto_lint")          # 非 .py 跳过
    _cfg(tmp_path, auto_lint=False)
    g = tmp_path / "g.py"
    g.write_text("y = 1\n", encoding="utf-8")
    await _edit(g, "y = 1", "def bad(:", ctx)
    assert not ctx.extras.get("auto_lint")          # 显式关


async def test_explicit_command_used(tmp_path):
    script = tmp_path / "failint.sh"
    script.write_text('#!/bin/sh\necho "LINT-FAIL $1"\nexit 1\n',
                      encoding="utf-8")
    script.chmod(0o755)
    _cfg(tmp_path, auto_lint_cmd=str(script))
    f = tmp_path / "z.py"
    f.write_text("z = 1\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await _edit(f, "z = 1", "z = 2", ctx)
    pending = ctx.extras.get("auto_lint") or []
    assert pending and "LINT-FAIL" in pending[0]["output"]
    assert "z.py" in pending[0]["output"]          # 变更文件作为参数传入


async def test_no_tool_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "_find", lambda n: None)
    _cfg_rm = tmp_path / ".loadn" / "settings.json"
    _cfg_rm.unlink(missing_ok=True)
    f = tmp_path / "q.py"
    f.write_text("q = 1\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await _edit(f, "q = 1", "def bad(:", ctx)
    assert not ctx.extras.get("auto_lint")          # 无工具静默


async def test_auto_test_off_by_default(tmp_path):
    """auto_test 默认关：坏编辑不触发 test（显式开+配置才跑）。"""
    _cfg(tmp_path, auto_test_cmd="false", auto_test=False)
    f = tmp_path / "t.py"
    f.write_text("t = 1\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await _edit(f, "t = 1", "def bad(:", ctx)
    tests = [p for p in ctx.extras.get("auto_lint") or []
             if p["path"] == "(test)"]
    assert not tests                                 # 默认关
    # 显式开：跑（false 命令必失败 → (test) 条目出现）
    _cfg(tmp_path, auto_test_cmd="false", auto_test=True)
    g = tmp_path / "t2.py"
    g.write_text("g = 1\n", encoding="utf-8")
    await _edit(g, "g = 1", "def bad(:", ctx)
    tests2 = [p for p in ctx.extras.get("auto_lint") or []
              if p["path"] == "(test)"]
    assert tests2


async def test_write_path_also_lints(tmp_path):
    """Write 路径同钩子（P3-1 turn_diff 同点挂载）。"""
    f = tmp_path / "w.py"
    ctx = ToolContext(cwd=tmp_path)
    out = await WriteTool().execute(
        {"file_path": str(f), "content": "def broken(:\n"}, ctx)
    assert "已写入" in out                                   # 失败不 block 写入
    pending = ctx.extras.get("auto_lint") or []
    assert pending and "w.py" in pending[0]["output"]


# ---------------------------------------------------------------- 全链
async def test_run_turn_injects_reminder(tmp_path, monkeypatch):
    """turn1 坏编辑 → turn2 messages 头部有 system-reminder。"""
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk

    monkeypatch.setenv("LOADN_HOME", str(tmp_path / "home"))
    f = tmp_path / "app.py"
    f.write_text("v = 1\n", encoding="utf-8")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Read", {"file_path": str(f)}),
        H.tool_round("t2", "Edit", {"file_path": str(f),
                                     "old_string": "v = 1",
                                     "new_string": "def broken(:"}),
        H.tool_round("t3", "Read", {"file_path": str(f)}),
        H.tool_round("t4", "Edit", {"file_path": str(f),
                                     "old_string": "def broken(:",
                                     "new_string": "v = 2"}),
        [Chunk(kind="text_delta", text="fixed"),
         _stop_chunk({"input_tokens": 40, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider,
                     tools={"Read": ReadTool(), "Edit": EditTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=8),
                     ctx=ToolContext(cwd=tmp_path))
    await core.run_turn("改坏它")
    await core.run_turn("修好它")
    # turn2 的 provider 输入头部含 reminder（lint 失败回喂）
    second = provider.calls[-1]
    text = " ".join(getattr(b, "text", "") for m in second
                    for b in m.content)
    assert "system-reminder" in text
    assert "检查发现问题" in text
    # transcript 也留痕
    ts = (tmp_path / "home" / "sessions" / session.session_id
          / "transcript.jsonl").read_text()
    assert "system-reminder" in ts
