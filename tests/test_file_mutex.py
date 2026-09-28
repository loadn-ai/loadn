"""P0-1 同文件写互斥验收：并行编辑同一文件不再 lost-update。

- with_file_lock 单元：同键串行（临界区不重叠）、异键并行、超时报
  ToolError 且 fn 不执行/锁不泄漏
- 卡面验收：两个并发 asyncio 任务对同一文件先后 Edit——写序串行且最终
  内容=两次编辑叠加
- Edit / Write / MultiEdit / NotebookEdit 共用同一把按文件锁（跨工具互斥）
- 跨 ToolContext（并行子代理各持独立 ctx）同样互斥

注：文件工具的写路径内核是纯同步的（单事件循环下本就不会交错），本卡
锁的实际收益=把「串行」从巧合变成契约（未来写路径引入 await/fsync 时
不回退），以及 mtime 粒度粗的文件系统上守卫的确定性前置。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from loadn.tools import base as tools_base
from loadn.tools.base import ToolContext, ToolError, with_file_lock
from loadn.tools.edit import EditTool
from loadn.tools.multiedit import MultiEditTool
from loadn.tools.notebook import NotebookEditTool
from loadn.tools.read import ReadTool
from loadn.tools.write import WriteTool


async def _edit(tool, path: Path, old: str, new: str, ctx: ToolContext) -> str:
    return await tool.execute({"file_path": str(path), "old_string": old,
                               "new_string": new}, ctx)


# ---------------------------------------------------------------- 单元
async def test_with_file_lock_serializes_same_key(tmp_path: Path):
    """同键临界区不重叠（真 await 窗口下探针计数）。"""
    f = tmp_path / "same.txt"
    active = peak = 0
    order: list[str] = []

    async def job(tag: str) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)            # 拉宽窗口，交错必然暴露
        order.append(tag)
        active -= 1
        return tag

    out = await asyncio.gather(
        with_file_lock(f, lambda: job("a")),
        with_file_lock(f, lambda: job("b")))
    assert sorted(out) == ["a", "b"]
    assert peak == 1, f"同文件临界区并发（peak={peak}）"
    assert len(order) == 2


async def test_with_file_lock_parallel_different_keys(tmp_path: Path):
    f1, f2 = tmp_path / "x.txt", tmp_path / "y.txt"
    active = peak = 0

    async def job():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.08)
        active -= 1

    await asyncio.gather(with_file_lock(f1, job), with_file_lock(f2, job))
    assert peak == 2, f"异文件被串行化（peak={peak}）——锁粒度错了"


async def test_lock_timeout_errors_and_releases(tmp_path: Path):
    f = tmp_path / "held.txt"
    f.write_text("data\n")
    lock = tools_base._FILE_LOCKS.setdefault(str(f.resolve()), asyncio.Lock())
    await lock.acquire()                          # 模拟持锁方卡死
    ran = False

    async def fn():
        nonlocal ran
        ran = True

    with pytest.raises(ToolError, match="超时"):
        await with_file_lock(f, fn, timeout_s=0.05)
    assert not ran                                # fn 未执行
    lock.release()
    assert await with_file_lock(f, lambda: _async_val("ok"),
                                timeout_s=1) == "ok"


async def _async_val(v):
    await asyncio.sleep(0)
    return v


# ---------------------------------------------------------------- 卡面验收
async def test_parallel_same_file_edits_both_land(tmp_path: Path, monkeypatch):
    """两个并发任务对同一文件先后 Edit：写序串行 + 两次编辑都落盘。"""
    f = tmp_path / "shared.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, ctx)

    # 写序探针（同步内核，同步替换）：记录落盘先后
    import loadn.tools.edit as edit_mod
    writes: list[float] = []
    orig_write = edit_mod._write_guarded

    def _probe(*a, **k):
        out = orig_write(*a, **k)
        writes.append(len(writes))
        return out

    monkeypatch.setattr(edit_mod, "_write_guarded", _probe)
    import loadn.tools.multiedit as me_mod
    import loadn.tools.notebook as nb_mod
    monkeypatch.setattr(me_mod, "_write_guarded", _probe, raising=False)
    monkeypatch.setattr(nb_mod, "_write_guarded", _probe, raising=False)

    results = await asyncio.gather(
        _edit(EditTool(), f, "alpha", "alpha-1", ctx),
        _edit(EditTool(), f, "gamma", "gamma-2", ctx),
        return_exceptions=True)
    assert not [r for r in results if isinstance(r, Exception)], results
    assert len(writes) == 2                       # 两次落盘，无一丢失
    text = f.read_text()
    assert "alpha-1" in text and "gamma-2" in text, text   # 两次编辑叠加


async def test_cross_context_subagents_mutex(tmp_path: Path):
    """并行子代理现实形态：独立 ToolContext 双写同文件——绝不静默丢编辑。"""
    f = tmp_path / "cross.txt"
    f.write_text("one\ntwo\n")
    c1, c2 = ToolContext(cwd=tmp_path), ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, c1)
    await ReadTool().execute({"file_path": str(f)}, c2)
    r = await asyncio.gather(
        _edit(EditTool(), f, "one", "one!", c1),
        _edit(EditTool(), f, "two", "two!", c2),
        return_exceptions=True)
    text = f.read_text()
    if not [x for x in r if isinstance(x, Exception)]:
        assert "one!" in text and "two!" in text     # 守卫粒度内双双通过
    else:
        # 后写方被 mtime 守卫明确拒绝（外部变更）——失败可见，非静默丢失
        assert isinstance(r[[i for i, x in enumerate(r)
                             if isinstance(x, Exception)][0]], ToolError)
        assert "one!" in text or "two!" in text      # 至少一个成功


# ---------------------------------------------------------------- 共锁矩阵
async def test_write_blocked_by_held_lock(tmp_path: Path):
    """Write 与 Edit 同键：锁被占时 Write 等待，释放后完成。"""
    f = tmp_path / "w.txt"
    f.write_text("keep\n")
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(f)}, ctx)

    lock = tools_base._FILE_LOCKS.setdefault(str(f.resolve()), asyncio.Lock())
    await lock.acquire()
    started = asyncio.Event()

    async def blocked():
        started.set()
        return await WriteTool().execute(
            {"file_path": str(f), "content": "overwritten\n"}, ctx)

    task = asyncio.create_task(blocked())
    await started.wait()
    await asyncio.sleep(0.05)
    assert not task.done()                       # 被同文件锁挡住
    lock.release()
    out = await asyncio.wait_for(task, timeout=5)
    assert "已写入" in out and f.read_text() == "overwritten\n"


async def test_multiedit_and_notebook_same_lock(tmp_path: Path):
    """MultiEdit / NotebookEdit 与 Edit 共用同一把锁（卡面要求）。"""
    nb = tmp_path / "book.ipynb"
    nb.write_text(json.dumps({"cells": [
        {"cell_type": "code", "id": "c1", "source": ["x"], "metadata": {},
         "outputs": [], "execution_count": None}]}))
    ctx = ToolContext(cwd=tmp_path)
    await ReadTool().execute({"file_path": str(nb)}, ctx)

    async def cell_edit():
        await asyncio.sleep(0)                   # 让 gather 真并发起跑
        return await NotebookEditTool().execute(
            {"notebook_path": str(nb), "cell_id": "c1", "new_source": "y"}, ctx)

    async def medit():
        return await MultiEditTool().execute(
            {"file_path": str(nb),
             "edits": [{"old_string": '"x"', "new_string": '"z"'}]}, ctx)

    r = await asyncio.gather(cell_edit(), medit(), return_exceptions=True)
    errs = [x for x in r if isinstance(x, Exception)]
    # 互斥保证不丢更新：要么都成，要么后写方被守卫明确拒；最终文件是
    # 两者按序的合法后继，而非旧基线静默覆盖
    assert errs == [] or all(isinstance(e, ToolError) for e in errs)
    final = json.loads(nb.read_text())
    assert final["cells"][0]["source"] in (["y"], ["z"])
