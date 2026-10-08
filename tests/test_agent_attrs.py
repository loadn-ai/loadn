"""子代理归属字段（PROTOCOL §2.1 宿主派生面）：Task 卡/子N· 转发块在
detail blocks_json、SSE payload、live 快照三条链路都带 agent 字段。

fake_claude 的 .fake/subagent 场景 emit：Task sub_1（马洛）→ 子1·Write →
Task sub_2（波洛）→ sub_1/sub_2 结束卡 → 子1·Bash（验证人名学习表跨事件存活）。
"""
import asyncio
import json

from tests.conftest import wait_turn


async def _create(client, **body):
    resp = await client.post("/api/sessions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


async def _drain_sse(client, sid, events, want, timeout_s=20):
    """收集 SSE 事件直到出现 want 类型（对齐 test_e2e._drain_sse）。"""
    async with client.stream("GET", f"/api/sessions/{sid}/events") as r:
        buf = ""
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line.startswith("event:"):
                    ty = line[6:].strip()
                elif line.startswith("data:") and ty:
                    events.append((ty, json.loads(line[5:].strip())))
                    if ty == want:
                        return


async def test_agent_fields_in_blocks_and_sse(client, ws_root):
    sess = await _create(client, title="子代理归属")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "subagent").touch()

    events: list[tuple[str, dict]] = []
    sse_task = asyncio.create_task(_drain_sse(client, sid, events, want="turn_done"))
    await asyncio.sleep(0.3)
    t = (await client.post(f"/api/sessions/{sid}/messages",
                           json={"text": "派两个子代理"})).json()["turn"]
    await asyncio.wait_for(sse_task, timeout=20)
    await wait_turn(client, sid, t["id"])

    # ---- SSE payload：Task 卡带 agent_id/name/role/type；子N· 带 agent_n
    tu = [e for ty, e in events if ty == "tool_use"]
    task_cards = [e for e in tu if e["name"] == "Task"]
    assert {c["agent_name"] for c in task_cards} == {"马洛", "波洛"}
    m1 = next(c for c in task_cards if c["agent_name"] == "马洛")
    assert m1["agent_id"] == "sub_1" and m1["agent_role"] == "子课题调研"
    assert m1["subagent_type"] == "general"
    w = next(e for e in tu if (e.get("name") or "").startswith("子1·Write"))
    assert w["agent_id"] == "sub_1" and w["agent_n"] == 1 and w["agent_name"] == "马洛"
    b = next(e for e in tu if (e.get("name") or "").startswith("子1·Bash"))
    assert b["agent_name"] == "马洛"       # 人名学习表跨事件存活
    # 普通工具不带 agent 字段
    plain = [e for e in tu if e["name"] not in ("Task",)
             and not (e.get("name") or "").startswith("子")]
    for e in plain:
        assert "agent_id" not in e

    # ---- 结束卡：tool_result id=sub_N 可配对置 done（前端状态机依赖）
    tr = [e for ty, e in events if ty == "tool_result" and e.get("id") == "sub_1"]
    assert tr and tr[0]["name"] == "Task" and not tr[0]["is_error"]

    # ---- blocks_json（历史回放）：同字段持久化
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    am = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    blocks = json.loads(am["blocks_json"])
    tb_task = [b for b in blocks if b.get("name") == "Task"]
    assert {b.get("agent_name") for b in tb_task} == {"马洛", "波洛"}
    tb_w = next(b for b in blocks if b.get("name") == "子1·Write")
    assert tb_w["agent_id"] == "sub_1" and tb_w["agent_n"] == 1
    assert tb_w.get("agent_name") == "马洛"
    tb_bash = next(b for b in blocks if b.get("name") == "子1·Bash")
    assert tb_bash.get("agent_name") == "马洛"


async def test_agent_fields_in_live_snapshot(client, ws_root, monkeypatch):
    """运行中 /live 快照：items 白名单扩键后 agent 字段不丢。"""
    monkeypatch.setenv("LOADN_FAKE_SUB_PAUSE_S", "3")
    sess = await _create(client, title="live 快照归属")
    sid = sess["id"]
    ctrl = ws_root / sid / ".fake"
    ctrl.mkdir(parents=True, exist_ok=True)
    (ctrl / "subagent").touch()

    t = (await client.post(f"/api/sessions/{sid}/messages",
                           json={"text": "跑"})).json()["turn"]
    # 停顿窗口内轮询 /live，直到 Task 卡出现（子1·Write 及其结果已入 blocks）
    items = None
    for _ in range(40):
        await asyncio.sleep(0.1)
        live = (await client.get(f"/api/sessions/{sid}/live")).json()["live"]
        if live and any(i.get("name") == "Task" for i in live["items"]):
            items = live["items"]
            break
    assert items, "live 快照未捕获 running 窗口"
    task_item = next(i for i in items if i["kind"] == "tool" and i["name"] == "Task")
    assert task_item["agent_name"] == "马洛" and task_item["agent_id"] == "sub_1"
    w_item = next(i for i in items if i.get("name") == "子1·Write")
    assert w_item["agent_n"] == 1 and w_item["agent_name"] == "马洛"
    await wait_turn(client, sid, t["id"])


async def test_old_blocks_without_agent_keys_ok(client):
    """旧数据回退：blocks_json 无 agent 键的会话 detail 正常返回。"""
    from loadn_webui import db as db_mod

    sess = await _create(client, title="旧数据")
    sid = sess["id"]
    with db_mod.conn() as c:
        c.execute(
            "INSERT INTO messages(session_id, turn_id, role, content, blocks_json)"
            " VALUES(?, 999, 'assistant', ?, ?)",
            (sid, "旧回复",
             json.dumps([{"type": "tool", "id": "tu_x", "name": "子1·Bash",
                          "brief": "echo", "input": {}}])))
    detail = (await client.get(f"/api/sessions/{sid}")).json()
    am = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    blocks = json.loads(am["blocks_json"])
    assert blocks[0]["name"] == "子1·Bash"
    assert "agent_id" not in blocks[0]      # 旧块无键——前端回退解析 name
