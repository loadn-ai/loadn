"""T9：session(resume/fork/rotate) / sse(回放/断线补发/降级) /
repomap(权重/mentioned/预算) / task_tools / util 双侧 单测（65-77% 低覆盖面）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ================================================================ session
async def _mk(tmp_path, sid=None):
    from loadn.core.session import SessionManager
    return (SessionManager.create(tmp_path, home=tmp_path / "home")
            if sid is None
            else SessionManager(sid, cwd=tmp_path, home=tmp_path / "home"))


async def test_session_resume_fresh_and_existing(tmp_path):
    """resume 不存在 transcript → init 补记 resumed_fresh；存在 → 沿用。"""
    from loadn.core.session import SessionManager
    s1 = await _mk(tmp_path)
    s1.append_user("第一轮")
    s2 = SessionManager.resume(s1.session_id, tmp_path,
                               home=tmp_path / "home")
    assert s2.session_id == s1.session_id
    evs = s2.transcript.read_events()
    assert evs[0]["type"] == "init" and not evs[0]["payload"].get(
        "resumed_fresh")                       # create 的 init 无 fresh 标记
    # 全新 id：resume 视为新会话（resumed_fresh 标记）
    s3 = SessionManager.resume("newsid-0001", tmp_path,
                               home=tmp_path / "home")
    evs3 = s3.transcript.read_events()
    assert evs3[0]["type"] == "init" and evs3[0]["payload"]["resumed_fresh"]


async def test_session_fork_copies_history(tmp_path):
    from loadn.core.session import SessionManager
    s1 = await _mk(tmp_path)
    s1.append_user("原始任务")
    s1.append_event("assistant", {"role": "assistant"})
    fk = SessionManager.fork(s1.session_id, tmp_path,
                             home=tmp_path / "home")
    assert fk.session_id != s1.session_id
    evs = fk.transcript.read_events()
    types = [e["type"] for e in evs]
    assert "user" in types and "assistant" in types
    # init 只有自己的一条（源 init 被跳过）
    assert types.count("init") == 1
    # fork 后两线独立：源再写不进 fork
    s1.append_user("源线新消息")
    assert "源线新消息" not in json.dumps(
        [e.get("payload") for e in fk.transcript.read_events()],
        ensure_ascii=False)


async def test_session_rotate_new_id(tmp_path):
    s1 = await _mk(tmp_path)
    s1.append_user("旧档内容")
    new_id = s1.rotate()
    assert new_id != s1.session_id
    assert (tmp_path / "home" / "sessions" / new_id).exists()   # 新档已建
    assert s1.transcript.path.exists()              # 旧档保留


# ================================================================ sse
async def test_sse_resync_snapshot_unknown_sid(client):
    """不存在的会话：快照 session=None（路由不 404——SSE 面静默空）。"""
    from loadn_webui.api.sse import _resync_snapshot
    snap = _resync_snapshot("no-such-sid-for-sse")
    assert snap == {"session": None}


async def test_sse_resync_snapshot_full(client):
    """真会话：session/messages/turns/recent_files 齐备。"""
    r = await client.post("/api/sessions", json={"title": "sse 快照"})
    sid = r.json()["session"]["id"]
    from loadn_webui.api.sse import _resync_snapshot
    snap = _resync_snapshot(sid)
    assert snap["session"]["id"] == sid
    assert isinstance(snap["messages"], list)
    assert isinstance(snap["turns"], list)
    assert isinstance(snap["recent_files"], list)


async def test_sse_stream_fresh_and_last_event_id(client):
    """SSE 真流：fresh connect 收 ping/事件帧；Last-Event-ID 之后事件才推。"""
    import asyncio
    r = await client.post("/api/sessions", json={"title": "sse 流"})
    sid = r.json()["session"]["id"]
    from loadn_webui.engine import ENGINE
    eid1 = ENGINE.publish(sid, "text", {"turn_id": 1, "text": "第一条"})
    lines: list[str] = []

    async def collect(stop_after: str) -> None:
        async with client.stream(
                "GET", f"/api/sessions/{sid}/events?last_event_id={eid1}"
        ) as resp:
            async for ln in resp.aiter_lines():
                lines.append(ln)
                if stop_after in ln:
                    return

    task = asyncio.get_event_loop().create_task(collect("新事件"))
    await asyncio.sleep(0.5)              # 让订阅建立（先订阅再回放语义）
    ENGINE.publish(sid, "text", {"turn_id": 2, "text": "新事件"})
    await asyncio.wait_for(task, timeout=15)
    body = "\n".join(lines)
    assert "event: text" in body and "新事件" in body
    assert "第一条" not in body                     # last_event_id 之后才推


# ================================================================ repomap
def test_repomap_mentioned_promotion(tmp_path):
    """mentioned 文件提权：被摸过的文件排前（aider 语义）。"""
    from loadn.core import repomap as rm
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n",
                                   encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    pass\n"
                                   "\nalpha()\n", encoding="utf-8")
    out = rm.get_repo_map(tmp_path, mentioned={str(tmp_path / "a.py")})
    assert "alpha" in out and "beta" in out
    assert out.index("a.py") < out.index("b.py")   # mentioned 提权排前


def test_repomap_budget_and_empty(tmp_path):
    from loadn.core import repomap as rm
    assert rm.get_repo_map(tmp_path) == ""          # 空目录 → 空地图
    for i in range(30):                             # 大仓超预算 → 截断标注
        (tmp_path / f"f{i:02}.py").write_text(
            f"def fn{i}():\n    pass\n" + "x = 1\n" * 200, encoding="utf-8")
    import os
    old = os.environ.get("LOADN_REPOMAP_TOKENS")
    os.environ["LOADN_REPOMAP_TOKENS"] = "50"
    try:
        out = rm.get_repo_map(tmp_path)
        assert out == "" or "截断" in out or len(out) < 4000
    finally:
        if old is None:
            os.environ.pop("LOADN_REPOMAP_TOKENS", None)
        else:
            os.environ["LOADN_REPOMAP_TOKENS"] = old


def test_repomap_regex_tags_and_unreadable(tmp_path):
    from loadn.core.repomap import _regex_tags
    tags = _regex_tags("class Foo:\n    def bar(self):\n        pass\n")
    names = [n for n, _ in tags]
    assert "Foo" in names and "bar" in names
    assert _regex_tags("plain text no defs") == []


# ================================================================ task_tools
async def test_task_tools_list_and_cancel():
    """TaskList/TaskCancel 对注册表的行为（登记→列→取消→无注册表降级）。"""
    import asyncio

    from loadn.core.task_tools import TaskCancelTool, TaskListTool
    from loadn.core.tasks import TaskRegistry
    from loadn.tools.base import ToolContext, ToolError
    reg = TaskRegistry()
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    tid = reg.register("timer", "定时唤醒任务", cancel_handle=fut)
    ctx = ToolContext(cwd=Path("."), extras={"task_registry": reg})
    listing = await TaskListTool().execute({}, ctx)
    assert tid in listing and "定时唤醒任务" in listing
    cancel = await TaskCancelTool().execute({"task_id": tid}, ctx)
    assert "已取消" in cancel and "task-cancel" in cancel
    assert await TaskListTool().execute({}, ctx) == "（无在跑任务）"
    # 取消已结束/不存在 → ToolError 可读
    with pytest.raises(ToolError, match="不存在"):
        await TaskCancelTool().execute({"task_id": tid}, ctx)
    # 无注册表降级文案
    bare = ToolContext(cwd=Path("."))
    assert await TaskListTool().execute({}, bare) == "（无注册表）"


# ================================================================ util
def test_loadn_util_frontmatter():
    from loadn.util import parse_frontmatter
    meta, body = parse_frontmatter(
        "---\nname: demo\ntags: [a, b]\n---\n正文内容\n")
    assert meta["name"] == "demo" and meta["tags"] == ["a", "b"]
    assert body.startswith("正文内容")
    # 无 frontmatter → 原文即正文
    meta2, body2 = parse_frontmatter("没有分节头")
    assert meta2 == {} and body2 == "没有分节头"


def test_webui_util_iso_and_slug():
    from loadn_webui.util import iso, slugify
    assert "T" in iso() and "+" in iso()          # ISO8601 带时区
    assert slugify("你好 World 123") .endswith("123")
    assert "world" in slugify("你好 World 123").lower()
