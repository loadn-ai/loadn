"""端到端全栈测试（可重复——替代生产手动验证）。

一个测试跑完用户完整旅程，全栈组件真实在场：
  uvicorn 服务 + EgressProxy(tcp+uds) + bwrap 沙箱 + fake 引擎
  + policy hooks + approvals + canary + 快照回滚 + 审计链

旅程：建会话（自动布置 canary+物化 hooks+快照就绪）
  → 消息→沙箱内 turn（uds 桥断网形态）→ done+产物落 workspace
  → hook 拦红线命令（引擎内真拦截）→ 审批确认码门（API 全链）
  → 快照回滚 → kill/unlock → 审计 verify 健康。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from loadn_webui import sandbox
from loadn_webui.config import CONFIG

pytestmark = [
    pytest.mark.skipif(not sandbox.bwrap_available(), reason="bwrap 不可用"),
]


@pytest.fixture()
def full_stack(client, monkeypatch):
    """沙箱 bwrap + 断网 uds 形态（生产同构）。"""
    monkeypatch.setattr(CONFIG.security, "sandbox", "bwrap")
    monkeypatch.setattr(CONFIG.engines, "default", "loadn")
    monkeypatch.setenv("LOADN_PROVIDER", "fake")
    return client


async def _wait_turn(client, sid, tid, timeout_s=60):
    t0 = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - t0 < timeout_s:
        await asyncio.sleep(1)
        t = next((x for x in
                  (await client.get(f"/api/sessions/{sid}")).json()["turns"]
                  if x["id"] == tid), None)
        if t and t["status"] in ("done", "error", "stopped"):
            return t
    pytest.fail("turn 超时")


async def test_full_stack_user_journey(full_stack, ws_root):
    c = full_stack

    # ── 1. 建会话：canary 布置 + hooks 物化 + 能力底座 ──
    sid = (await c.post("/api/sessions", json={"title": "E2E 全栈旅程"})
           ).json()["session"]["id"]
    ws = ws_root / sid
    assert (ws / "notes" / ".canary_tokens.md").exists()
    hooks = json.loads((ws / ".loadn" / "settings.json").read_text())["hooks"]
    assert "policy-check" in hooks["PreToolUse"][0]["command"]

    # ── 2. turn：沙箱内跑通（spawn→事件流→记账→档案）──
    (ws / ".fake").mkdir()
    (ws / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "echo e2e-ok"}}))
    tid = (await c.post(f"/api/sessions/{sid}/messages",
                        json={"text": "干活"})).json()["turn"]["id"]
    t = await _wait_turn(c, sid, tid)
    assert t["status"] == "done", t.get("error")
    assert t["cost_usd"] is not None and t["num_turns"] >= 1

    # ── 3. hook 红线拦截（引擎内真拦截，deny 回模型）──
    sid2 = (await c.post("/api/sessions", json={"title": "E2E 红线"})).json()["session"]["id"]
    ws2 = ws_root / sid2
    (ws2 / ".fake").mkdir()
    (ws2 / ".fake" / "tools").write_text(json.dumps(
        {"name": "Bash", "input": {"command": "rm -rf /"}}))
    tid2 = (await c.post(f"/api/sessions/{sid2}/messages",
                         json={"text": "坏命令"})).json()["turn"]["id"]
    t2 = await _wait_turn(c, sid2, tid2)
    assert t2["status"] == "done"
    from loadn_webui import db as db_mod
    with db_mod.conn() as conn:
        hh = db_mod.get_session(conn, sid2)["claude_session_id"]
    ts_file = (Path(__import__("os").environ.get("LOADN_HOME")
                    or Path.home() / ".loadn")
               / "sessions" / hh / "transcript.jsonl")
    assert "policy:block" in ts_file.read_text(encoding="utf-8")

    # ── 4. 审批确认码门全链（create→approve 拿码→consume→拒重放）──
    from loadn_webui import approve
    a = approve.create(sid, "mail_send", {"to": "a@b.c", "subject": "s"})
    d = approve.decide(a["id"], True)
    code = d["code"]
    assert approve.consume(sid, "mail_send",
                           {"to": "a@b.c", "subject": "s"}, code)["ok"]
    assert not approve.consume(sid, "mail_send",
                               {"to": "a@b.c", "subject": "s"}, code)["ok"]

    # ── 5. 快照回滚（写前快照→改坏→API 回滚恢复）──
    (ws / "notes" / "draft.md").write_text("正文 v1")
    import os

    from loadn_webui import policy
    old = os.getcwd(); os.chdir(ws)
    try:
        policy._snapshot_before_write(str(ws / "notes" / "draft.md"))
    finally:
        os.chdir(old)
    (ws / "notes" / "draft.md").write_text("坏内容")
    pts = (await c.get(f"/api/sessions/{sid}/snapshots")).json()["snapshots"]
    assert pts
    rb = (await c.post(f"/api/sessions/{sid}/rollback",
                       json={"point": pts[-1]["point"]})).json()
    assert rb["ok"] and (ws / "notes" / "draft.md").read_text() == "正文 v1"

    # ── 6. kill/unlock 熔断 ──
    assert (await c.post(f"/api/sessions/{sid}/kill")).json()["locked"]
    r = await c.post(f"/api/sessions/{sid}/messages", json={"text": "x"})
    assert r.status_code == 403
    await c.post(f"/api/sessions/{sid}/unlock")

    # ── 7. 审计链 verify（本旅程全部事件在账且链一致）──
    from loadn_webui import audit as audit_mod
    assert audit_mod.verify() == []
    for kind in ("permission_decision", "approval_request",
                 "approval_decision", "rollback", "kill_switch"):
        assert audit_mod.tail(5, kind), f"旅程事件缺失: {kind}"
