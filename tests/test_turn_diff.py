"""P3-1 turn 级 diff 验收（零 token）。

- 纯函数：小改 unified diff 正确；新建文件粗粒度；超 MAX_DIFF_BYTES
  粗粒度；diff 行数截断；diff_hash 稳定；summary_line +/- 计数
- 工具链：Edit/Write 后 ctx.extras['turn_diff'] 有条目（diff 含变更行）；
  run_turn 的 result 事件带 diffs 字段（path/hash/lines）
- 预算：500 文件并发编辑（各小改）总耗时不失控（粗粒度回退可用性）
"""
from __future__ import annotations

import time

import pytest

from loadn.core import turn_diff as td

pytestmark = [pytest.mark.coverage("engine.turndiff")]


# ---------------------------------------------------------------- 纯函数
def test_compute_small_change(tmp_path):
    f = tmp_path / "a.txt"
    before = b"line1\nline2\nline3\n"
    after = b"line1\nLINE2\nline3\n"
    text, h = td.compute(f, before, after)
    assert "-line2" in text and "+LINE2" in text
    assert str(f) in text
    assert len(h) == 16
    # hash 稳定（同输入同输出）
    _, h2 = td.compute(f, before, after)
    assert h == h2


def test_compute_new_file(tmp_path):
    text, h = td.compute(tmp_path / "new.txt", None, b"hello")
    assert "新建文件" in text and h


def test_compute_coarse_on_huge(tmp_path):
    f = tmp_path / "big.bin"
    before = b"x" * (td.MAX_DIFF_BYTES + 1)
    text, h = td.compute(f, before, b"y" * (td.MAX_DIFF_BYTES + 2))
    assert "粗粒度" in text and h


def test_compute_truncates_long_diff(tmp_path):
    f = tmp_path / "long.txt"
    before = "".join(f"old{i}\n" for i in range(3000)).encode()
    after = "".join(f"new{i}\n" for i in range(3000)).encode()
    text, h = td.compute(f, before, after)
    assert "截断" in text
    assert text.count("\n") < 300               # 远小于全量 diff 行数


def test_summary_line_counts():
    assert td.summary_line("-a\n+b\n--- old\n+++ new\n") == "+1/-1"
    assert td.summary_line("") == ""


# ---------------------------------------------------------------- 工具链
async def test_edit_records_diff(tmp_path):
    from loadn.tools.base import ToolContext
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool
    f = tmp_path / "note.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, ctx)
    out = await EditTool().execute(
        {"file_path": str(f), "old_string": "beta",
         "new_string": "BETA"}, ctx)
    diffs = ctx.extras.get("turn_diff") or []
    assert len(diffs) == 1
    assert "-beta" in diffs[0]["diff"] and "+BETA" in diffs[0]["diff"]
    assert diffs[0]["hash"]


async def test_write_records_diff(tmp_path):
    from loadn.tools.base import ToolContext
    from loadn.tools.write import WriteTool
    f = tmp_path / "fresh" / "cfg.yaml"
    ctx = ToolContext(cwd=tmp_path)
    await WriteTool().execute(
        {"file_path": str(f), "content": "k: v\n"}, ctx)
    diffs = ctx.extras.get("turn_diff") or []
    assert diffs and "新建文件" in diffs[0]["diff"]


async def test_result_event_carries_diffs(tmp_path):
    """run_turn → result 事件 diffs 字段（v2 可选位；v1 消费方忽略）。"""
    import tests.helpers as H
    from loadn.core.loop import AgentCore, LoopSettings
    from loadn.core.session import SessionManager
    from loadn.providers import Chunk
    from loadn.providers.fake import _fake_model, _stop_chunk
    from loadn.tools.base import ToolContext
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool

    f = tmp_path / "code.py"
    f.write_text("x = 1\n", encoding="utf-8")
    provider = H.ScriptedProvider([
        H.tool_round("t1", "Read", {"file_path": str(f)}),
        H.tool_round("t2", "Edit", {"file_path": str(f),
                                    "old_string": "x = 1",
                                    "new_string": "x = 2"}),
        [Chunk(kind="text_delta", text="done"),
         _stop_chunk({"input_tokens": 50, "output_tokens": 5},
                     "end_turn", _fake_model())],
    ])
    session = SessionManager.create(tmp_path, home=tmp_path / "home")
    core = AgentCore(provider=provider,
                     tools={"Read": ReadTool(), "Edit": EditTool()},
                     session=session, cwd=tmp_path,
                     settings=LoopSettings(max_turns=5),
                     ctx=ToolContext(cwd=tmp_path))
    await core.run_turn("改值")
    result_evs = [e for e in session.transcript.read_events()
                  if e["type"] == "result"]
    assert result_evs and result_evs[-1]["payload"].get("diffs")
    d = result_evs[-1]["payload"]["diffs"][0]
    assert d["hash"] and "code.py" in d["path"] and d["lines"] == "+1/-1"


# ---------------------------------------------------------------- 预算
async def test_budget_500_files(tmp_path):
    """500 文件各一小改：总耗时受控（每文件 compute 有界；difflib 小 diff 快）。"""
    from loadn.tools.base import ToolContext
    from loadn.tools.edit import EditTool
    from loadn.tools.read import ReadTool
    ctx = ToolContext(cwd=tmp_path)
    edit, reader = EditTool(), ReadTool()
    t0 = time.perf_counter()
    for i in range(500):
        f = tmp_path / f"f{i}.txt"
        f.write_text("v=0\n", encoding="utf-8")
        await reader.execute({"file_path": str(f)}, ctx)
        # i=0 时 old==new 会被拒——用 1 基
        await edit.execute({"file_path": str(f), "old_string": "v=0",
                            "new_string": f"v={i + 1}"}, ctx)
    dt = time.perf_counter() - t0
    assert len(ctx.extras["turn_diff"]) == 500
    assert dt < 60, f"500 文件耗时 {dt:.1f}s"      # 快照+diff 有界
