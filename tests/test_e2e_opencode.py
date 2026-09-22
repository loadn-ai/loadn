"""opencode 引擎端到端（fake_opencode 驱动，真 uvicorn）：argv/事件/记账/轮换全链路。

与 test_e2e.py 同方法论：.fake 控制文件驱动假 CLI，WORKDADDY_FAKE_LOG 断言
argv。注入方式：monkeypatch WORKDADDY_OPENCODE_BIN + 内存态翻
CONFIG.engines.default="opencode"（CONFIG 是 import 时载入的单子，直接改对象、
fixture 还原）——会话经 _align_engine 切引擎（旧 uuid 存档，ses_ 域从空串起步）。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from tests.conftest import wait_turn

FAKE_OC = Path(__file__).resolve().parent / "fake_opencode.py"


@pytest.fixture()
def opencode_engine(monkeypatch):
    """默认引擎切 opencode + 假二进制注入（CONFIG 单子 import 时已载入，改内存
    对象本身；monkeypatch 测试后还原默认值）。"""
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.engines, "default", "opencode")
    monkeypatch.setattr(CONFIG.engines.opencode, "bin", None)   # 防外部 config.yaml 残留
    monkeypatch.setenv("WORKDADDY_OPENCODE_BIN", str(FAKE_OC))
    yield


async def _create(client, **body):
    resp = await client.post("/api/sessions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


async def _send(client, sid, text):
    resp = await client.post(f"/api/sessions/{sid}/messages", json={"text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()["turn"]


async def _drain_sse(client, sid, events, want, timeout_s=20):
    import time
    t0 = time.monotonic()
    async with client.stream("GET", f"/api/sessions/{sid}/events") as resp:
        assert resp.status_code == 200
        etype, data = None, None
        async for line in resp.aiter_lines():
            if time.monotonic() - t0 > timeout_s:
                break
            if line.startswith("event: "):
                etype = line[7:]
            elif line.startswith("id: "):
                continue
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif not line.strip() and etype:
                events.append((etype, dict(data or {})))
                if etype == want:
                    return
                etype = data = None
            elif line.startswith(": ping"):
                continue


async def test_happy_path_sse_and_accounting(client, ws_root, fake_calls, opencode_engine):
    """happy path：SSE 收齐 text/thinking/tool_use/tool_result/todos/turn_done；
    turns 行 usage/models_json/num_turns 来自适配器合成的 result 事件。"""
    n0 = len(fake_calls())
    sess = await _create(client, title="opencode工具流")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "tools").touch()
    (ctrl / "todos").touch()

    events: list[tuple[str, dict]] = []
    sse_task = asyncio.create_task(_drain_sse(client, sid, events, want="turn_done"))
    await asyncio.sleep(0.3)                       # 先订阅
    t = await _send(client, sid, "跑一轮带工具的任务")
    await asyncio.wait_for(sse_task, timeout=20)

    types = [e[0] for e in events]
    assert "turn_started" in types
    assert "text" in types and "thinking" in types   # text/thinking 整块下发
    assert types.count("tool_use") == 3              # TodoWrite + Bash + Write
    assert types.count("tool_result") == 3
    assert "todos" in types and "files" in types
    # 工具卡片配对（tool_use_id ↔ tool_result）
    tu = [e for ty, e in events if ty == "tool_use"]
    tr = [e for ty, e in events if ty == "tool_result"]
    assert {x["id"] for x in tu} == {x["id"] for x in tr}
    # TodoWrite input.todos 数组 → todos 面板语义
    todos_ev = [e for ty, e in events if ty == "todos"][-1]
    assert todos_ev["todos"][0]["subject"] == "检索来源"
    bash = next(x for x in tu if x["name"] == "Bash")
    assert bash["input"]["command"] == "echo hello-fake"
    assert next(x for x in tr if x["id"] == bash["id"])["result"] == "hello-fake"

    # argv 面：run 子命令 + NDJSON + --auto；fresh 不传 session flag；PROMPT 末位
    calls = fake_calls()[n0:]
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert argv[0:4] == ["run", "--format", "json", "--auto"]   # 日志不含 bin 本身
    assert "--session" not in argv
    assert argv[-1] == "跑一轮带工具的任务"

    # turns 行记账（result 合成事件 → usage/models_json/num_turns/cost）
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    trow = next(x for x in detail["turns"] if x["id"] == t["id"])
    assert trow["status"] == "done"
    assert trow["cost_usd"] == 0.05
    assert trow["num_turns"] == 1
    assert json.loads(trow["usage_json"]) == {
        "input_tokens": 1000, "output_tokens": 200,
        "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 0}
    assert json.loads(trow["models_json"])["fake"]["inputTokens"] == 1000

    # 会话簿记：引擎自建的 ses_… id 已登记供 resume；engine 字段已切换
    assert detail["engine"] == "opencode"
    assert detail["claude_session_id"].startswith("ses_")
    assert detail["session_fresh"] == 0
    assert detail["usage"]["in"] == 1000
    # 快照流只发整块：assistant 正文里最终文本恰好一次（无增量重复）
    am = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    assert am["content"].count("fake opencode") == 1
    assert any(b.get("type") == "thinking" for b in json.loads(am["blocks_json"]))


async def test_resume_second_message_uses_session_flag(client, ws_root, fake_calls,
                                                       opencode_engine):
    """第二条消息 resume：argv 带 --session <ses_id>（引擎自建 id 的登记回传）。"""
    sess = await _create(client, title="opencode续连")
    sid = sess["id"]
    t1 = await _send(client, sid, "第一条")
    assert (await wait_turn(client, sid, t1["id"]))["status"] == "done"
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    ses_id = detail["claude_session_id"]
    assert ses_id.startswith("ses_") and detail["session_fresh"] == 0

    n0 = len(fake_calls())
    t2 = await _send(client, sid, "第二条")
    assert (await wait_turn(client, sid, t2["id"]))["status"] == "done"
    calls = fake_calls()[n0:]
    assert len(calls) == 1
    assert calls[0]["session"]["flag"] == "--session"
    assert calls[0]["session"]["value"] == ses_id
    # resume 会话 id 恒定（fake 沿用传入 id → 合成 result 带回同一 ses_id）
    detail2 = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail2["claude_session_id"] == ses_id
    assert detail2["session_fresh"] == 0


async def test_fastfail_direct_error_no_rotation(client, ws_root, fake_calls,
                                                 opencode_engine):
    """fresh 秒拒：opencode 无 claude 式 session-id 锁（is_in_use_error=False），
    不翻转不轮换不重试——单次调用直接 error，fresh 位不落 0。"""
    sess = await _create(client, title="opencode秒拒")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "fastfail").touch()
    n0 = len(fake_calls())
    t = await _send(client, sid, "直接失败")
    t = await wait_turn(client, sid, t["id"])
    assert t["status"] == "error"
    assert "exit=1" in t["error"]
    assert len(fake_calls()) - n0 == 1            # 只调一次，无重试/轮换
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    assert detail["session_fresh"] == 1           # fast-fail：引擎会话从未建立
    assert detail["claude_session_id"] == ""


async def test_bigusage_rotates_to_fresh(client, ws_root, fake_calls, opencode_engine):
    """token 越限轮换：new_session_id()="" → 会话 id 清空、下一条消息 fresh 不带
    --session flag（由 opencode 自建新 ses_…）。

    轮换阈值内存态压到 500（fake 常规量 in=1000 即越限）——不去撑真实 600k
    token：那会把共享测试库的 cost_api_usd 推过 cost_cli_usd，打爆
    test_stats_cost 的全局口径断言。第二轮前阈值抬回，验证新 ses_ id 正常登记。
    """
    from loadn_webui import profile as profile_mod
    prof = profile_mod.get("researcher")
    origin = prof.rotate_input_tokens
    prof.rotate_input_tokens = 500                # 内存态生效（registry roundtrip 见 test_admin）
    try:
        sess = await _create(client, title="opencode轮换", profile="researcher")
        sid = sess["id"]
        n0 = len(fake_calls())
        t = await _send(client, sid, "大上下文任务")
        t = await wait_turn(client, sid, t["id"])
        assert t["status"] == "done"
        detail = (await client.get(f"/api/sessions/{sid}")).json()
        assert detail["usage"]["in"] == 1000      # 1000 > 阈值 500 → 轮换
        assert detail["session_fresh"] == 1       # 轮换：下轮 fresh
        assert detail["claude_session_id"] == ""  # 空串占位（引擎自建）

        prof.rotate_input_tokens = 10 ** 9        # 第二轮不再轮换，验证新 id 登记
        t2 = await _send(client, sid, "轮换后的任务")
        assert (await wait_turn(client, sid, t2["id"]))["status"] == "done"
        calls = fake_calls()[n0:]
        assert len(calls) == 2
        assert calls[0]["session"] is None        # 首发 fresh
        assert calls[1]["session"] is None        # 轮换后仍 fresh（无 session flag）
        detail2 = (await client.get(f"/api/sessions/{sid}")).json()
        assert detail2["claude_session_id"].startswith("ses_")   # 新会话 id 已登记
        assert detail2["usage"]["in"] == 2000
    finally:
        prof.rotate_input_tokens = origin


async def test_stop_running_turn(client, ws_root, fake_calls, opencode_engine):
    """stop 流程：hang（无 idle）→ killpg → stopped；pending text 由 finalize 冲出。"""
    sess = await _create(client, title="opencode停止")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "hang").touch()
    t = await _send(client, sid, "长任务")
    await asyncio.sleep(1.0)                      # 让它进入 running
    resp = await client.post(f"/api/turns/{t['id']}/stop")
    assert resp.status_code == 200
    t = await wait_turn(client, sid, t["id"])
    assert t["status"] == "stopped"
    # 流终止 flush：被杀前的 pending text 仍落 assistant 消息
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    am = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert am and "开始长任务" in am[-1]["content"]
