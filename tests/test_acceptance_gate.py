"""AV 验收 gate（来源：2026-11-02 会话 7ce3 虚假完成汇报架构调研）。

调研核心结论（23 VERIFIED 引用）：「Agent 自述完成不可信」是可量化事实
（生产案例自报 95% vs 外部验证 60%，静默失败 35%）；防御共识=确定性
gate 优先于模型判断、验收 channel 在 agent 权限边界外、「Done = 验收绿
不是 agent 停止」。loadn 落地三件：
  AV-1 verify_cmd：会话级平台验收命令（DB 真源），turn done 后独立进程
      组执行，退出码即裁决（pass/fail）+ 失败通知 + 审计
  AV-2 claim_noop：完成声明预检（零 LLM）——自述完成+本轮零写工具 → 标记
  AV-3 续跑联动：上轮验收失败/空转声明 → 自动续跑消息先带修复要求
"""
import asyncio
import json

import pytest_asyncio

from loadn_webui import db as db_mod
from loadn_webui.engine import Engine


@pytest_asyncio.fixture()
async def eng():
    e = Engine()
    yield e
    for t in list(e._workers.values()):
        t.cancel()
    for at in list(e.active.values()):
        at.stop.stop()


async def _wait_turn_done(sid: str, min_turns: int = 1, timeout_s: float = 25):
    """等该会话 turns 全部终态且条数 ≥ min_turns。"""
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.15)
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY id", (sid,)).fetchall()
        if (len(rows) >= min_turns
                and not any(r["status"] in ("queued", "running") for r in rows)):
            return rows
    raise TimeoutError(f"{timeout_s}s 内 turn 未终态（{len(rows)} 条）")


async def _mk_session(client, title="验收", first="开始"):
    r = await client.post("/api/sessions", json={"title": title,
                                                 "first_message": first})
    sid = r.json()["session"]["id"]
    await _wait_turn_done(sid)
    return sid


# ---------------------------------------------------------------- AV-1 执行器
async def test_verify_pass_and_fail(client, eng):
    """退出码即裁决：全过=pass；任一非零=fail（rc/时长/输出尾全记）。"""
    sid = await _mk_session(client)
    r = await client.patch(f"/api/sessions/{sid}", json={
        "verify_cmd": ["test -d .", "echo hello-verify", "false"]})
    assert r.status_code == 200
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "推进"})
    rows = await _wait_turn_done(sid, min_turns=2)
    with db_mod.conn() as c:
        v = json.loads(db_mod.get_turn(c, rows[-1]["id"])["verify_json"])
    assert v["verdict"] == "fail"
    rcs = [i["rc"] for i in v["items"]]
    assert rcs == [0, 0, 1]                       # 退出码忠实记录
    assert any("hello-verify" in (i["tail"] or "") for i in v["items"])


async def test_verify_cwd_is_workspace(client, eng, ws_root):
    """验收命令 cwd=会话工作区根（产物检查语义）。"""
    sid = await _mk_session(client)
    from loadn_webui import workspace as ws_mod
    (ws_mod.ws_of(sid) / "MARKER.txt").write_text("x", encoding="utf-8")
    await client.patch(f"/api/sessions/{sid}", json={
        "verify_cmd": ["test -f MARKER.txt", "test -f no-such-file"]})
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "推进"})
    rows = await _wait_turn_done(sid, min_turns=2)
    with db_mod.conn() as c:
        v = json.loads(db_mod.get_turn(c, rows[-1]["id"])["verify_json"])
    assert v["verdict"] == "fail"                 # 第二条在工作区内必失败
    assert v["items"][0]["rc"] == 0               # 第一条证明 cwd=工作区


async def test_verify_timeout_kills_group(client, eng, monkeypatch):
    """单条超时：杀整进程组，rc=None + 超时标注，verdict=fail。"""
    monkeypatch.setattr(Engine, "VERIFY_CMD_TIMEOUT_S", 1)
    sid = await _mk_session(client)
    await client.patch(f"/api/sessions/{sid}", json={
        "verify_cmd": ["sleep 8"]})
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "推进"})
    rows = await _wait_turn_done(sid, min_turns=2, timeout_s=30)
    with db_mod.conn() as c:
        v = json.loads(db_mod.get_turn(c, rows[-1]["id"])["verify_json"])
    assert v["verdict"] == "fail"
    assert v["items"][0]["rc"] is None and "超时" in v["items"][0]["tail"]


async def test_no_verify_cmd_zero_overhead(client, eng):
    """未配置验收：verify_json 无 pass/fail 产物（claim_noop 也未触发时为 NULL）。"""
    sid = await _mk_session(client)
    from loadn_webui import workspace as ws_mod
    fake = ws_mod.ws_of(sid) / ".fake"
    fake.mkdir(exist_ok=True)
    (fake / "reply").write_text("普通进度汇报，继续中")
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "推进"})
    rows = await _wait_turn_done(sid, min_turns=2)
    with db_mod.conn() as c:
        assert db_mod.get_turn(c, rows[-1]["id"])["verify_json"] is None


async def test_verify_audit_recorded(client, eng):
    """验收裁决落审计（type=verify，detail_json 里带 sid/verdict）。"""
    from loadn_webui.security import audit as audit_mod
    sid = await _mk_session(client)
    await client.patch(f"/api/sessions/{sid}", json={"verify_cmd": ["true"]})
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "推进"})
    await _wait_turn_done(sid, min_turns=2)
    for e in audit_mod.tail(n=40):
        if e.get("type") != "verify":
            continue
        d = json.loads(e.get("detail_json") or "{}")
        if d.get("sid") == sid and d.get("verdict") == "pass":
            return
    raise AssertionError("audit type=verify 未落账")


# ---------------------------------------------------------------- AV-2 预检
async def test_claim_noop_marks_and_tool_writes_exempt(client, eng):
    """自述完成+零写工具 → claim_noop；有写工具不标；无完成声明不标。"""
    from loadn_webui import workspace as ws_mod
    sid = await _mk_session(client)
    fake = ws_mod.ws_of(sid) / ".fake"
    fake.mkdir(exist_ok=True)
    # ① 自述完成 + 零写工具 → 标记
    (fake / "reply").write_text("调研收尾：任务完成，全部交付。", encoding="utf-8")
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "进展"})
    rows = await _wait_turn_done(sid, min_turns=2)
    with db_mod.conn() as c:
        v = json.loads(db_mod.get_turn(c, rows[-1]["id"])["verify_json"])
    assert v.get("claim_noop") is True
    # ② 普通汇报 → 不标
    (fake / "reply").write_text("还在推进中，已完成 3/7 步。", encoding="utf-8")
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "继续"})
    rows = await _wait_turn_done(sid, min_turns=3)
    with db_mod.conn() as c:
        assert db_mod.get_turn(c, rows[-1]["id"])["verify_json"] is None
    # ③ 有写工具（fake tools 场景=Write）+ 完成声明 → 不标
    (fake / "tools").write_text("1", encoding="utf-8")
    (fake / "reply").unlink()
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "干活"})
    rows = await _wait_turn_done(sid, min_turns=4)
    with db_mod.conn() as c:
        vj = db_mod.get_turn(c, rows[-1]["id"])["verify_json"]
    assert vj is None or "claim_noop" not in (vj or "{}")


# ---------------------------------------------------------------- AV-3 续跑联动
async def test_auto_continue_prefix_on_verify_fail(client, eng):
    """验收失败 + 自动续跑：续跑指令先带「先修复验收」——防虚假完成循环。"""
    sid = await _mk_session(client)
    await client.patch(f"/api/sessions/{sid}", json={
        "auto_continue": True, "verify_cmd": ["false"]})
    with db_mod.conn() as c:                      # 跨 turn todos 快照（v0.7.56 口径）
        db_mod.kv_set(c, f"todos:{sid}", json.dumps(
            [{"subject": "推进任务", "status": "pending"}]))
    await client.post(f"/api/sessions/{sid}/messages", json={"text": "推进"})
    await _wait_turn_done(sid, min_turns=2, timeout_s=30)   # 原 turn + 续跑 turn
    with db_mod.conn() as c:
        msgs = c.execute(
            "SELECT content FROM messages WHERE session_id=? AND role='user'",
            (sid,)).fetchall()
    cont = [m["content"] for m in msgs if "自动续跑" in m["content"]]
    assert cont and "平台验收未通过" in cont[0] and "false" in cont[0]
    await client.patch(f"/api/sessions/{sid}", json={"auto_continue": False})


# ---------------------------------------------------------------- API 闸
async def test_verify_cmd_api_gates(client):
    """≤5 条×300 字闸 + null/[] 清空语义。"""
    sid = await _mk_session(client)
    r = await client.patch(f"/api/sessions/{sid}", json={
        "verify_cmd": ["a"] * 6})
    assert r.status_code == 400
    r = await client.patch(f"/api/sessions/{sid}", json={
        "verify_cmd": ["x" * 301]})
    assert r.status_code == 400
    r = await client.patch(f"/api/sessions/{sid}", json={
        "verify_cmd": [" ok ", "", "true"]})      # 空行被滤
    assert r.status_code == 200
    with db_mod.conn() as c:
        assert json.loads(db_mod.get_session(c, sid)["verify_cmd"]) == ["ok", "true"]
    r = await client.patch(f"/api/sessions/{sid}", json={"verify_cmd": None})
    assert r.status_code == 200
    with db_mod.conn() as c:
        assert db_mod.get_session(c, sid)["verify_cmd"] is None
