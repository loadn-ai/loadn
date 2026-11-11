"""值守轮平台兜底（DORMANT）：定时唤醒连续极短回复 → 达限自动暂停+通知。

生产背景（c5cb 2026-11-03 实证）：GC-2 偏执自检注入生效后 agent 仍输出
「## 唤醒 405 · 候守轮 / **零交易**。」（25 字）+ python3 -c 更新
state.json round 字段式假动作——400 轮行为先例压倒 2KB 软指令，且 agent
早年列的 A/B/C 裁定菜单沉在值守海洋里用户从未被通知。平台确定性兜底：
回复长度主轴（工具轴可被自我记账假动作绕过）+ kv 连击 + 达限暂停复用
await 暂停/恢复通道（用户回话自动恢复）。
"""
import json
from datetime import datetime, timezone

from loadn_webui import db as db_mod
from loadn_webui.scheduler import (
    DORMANT_MAX,
    DORMANT_TEXT_MAX,
    Scheduler,
    check_dormant_gate,
    dormant_streak,
    resume_await_jobs,
)
from loadn_webui.util import iso

_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def _job_dict(**kw):
    base = {"id": 1, "session_id": "s1", "kind": "message", "label": "盯盘",
            "prompt": "检查状态", "due_at": iso(), "every_s": None,
            "cron": None, "max_fires": 2000, "fires": 400, "status": "active",
            "last_fired_at": None, "title": None, "profile": None,
            "engine": None, "destination": "dashboard", "is_system": 0,
            "goal": None}
    base.update(kw)
    return base


async def _mk_wake_turn(sid: str, wake: bool = True, n: int = 1) -> int:
    """建一个 turn + 其触发 user 消息（唤醒轮默认带【定时唤醒头）。"""
    with db_mod.conn() as c:
        tid = db_mod.create_turn(c, session_id=sid, status="done")
        content = (f"【定时唤醒 {n}/2000】盯盘\n\n【唤醒词】\n检查状态"
                   if wake else "用户主动发的消息")
        db_mod.add_message(c, session_id=sid, turn_id=tid, role="user",
                           content=content)
    return tid


async def test_dormant_streak_accumulates_and_resets(client, monkeypatch):
    """唤醒轮短回复逐轮 +1；实质长回复清零；非唤醒轮不参与。"""
    r = await client.post("/api/sessions", json={"title": "值守检测"})
    sid = r.json()["session"]["id"]
    # 连续 4 轮值守（25 字形态——c5cb 生产实证样本）
    for i in range(4):
        tid = await _mk_wake_turn(sid, n=i + 1)
        assert check_dormant_gate(sid, tid, "## 唤醒 · 值守轮\n\n**零交易**。") == i + 1
    assert dormant_streak(sid) == 4
    # 实质轮（≥240 字：对账+数字+决策）→ 清零
    tid = await _mk_wake_turn(sid, n=5)
    solid = ("**对账**：权益 151.78（日内 +0.10%）｜BTC 77,234｜PREDICT 1.256。\n"
             "上轮 S2 配对 +1.6%；本轮：选举日波动率压缩，维持 DOGE 网格并"
             "上移止损到 0.0740，BTC 跌破 76,800 减半 PREDICT 空单。") * 2
    assert len(solid) >= DORMANT_TEXT_MAX
    assert check_dormant_gate(sid, tid, solid) == 0
    assert dormant_streak(sid) == 0
    # 非唤醒轮（用户主动消息）短回复：不动计数（既不累计也不清零）
    tid = await _mk_wake_turn(sid, wake=False)
    check_dormant_gate(sid, tid, "嗯好的")
    check_dormant_gate(sid, tid, "嗯好的")
    assert dormant_streak(sid) == 0
    # 唤醒轮再值守 → 从 1 重新累计
    tid = await _mk_wake_turn(sid, n=6)
    assert check_dormant_gate(sid, tid, "值守") == 1


async def test_dormant_gate_pauses_at_limit(client, monkeypatch):
    """达限（连续 DORMANT_MAX 轮）：暂停全部 active 非系统 job + await 账
    记账 + 推送通知 + 连击清零；用户回话自动恢复。"""
    fired = []
    monkeypatch.setattr("loadn_webui.integrations.notify.fire",
                        lambda *a, **k: fired.append(a))
    r = await client.post("/api/sessions", json={"title": "达限暂停"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, session_id=sid, label="盯盘", prompt="检查",
                                due_at=iso(), every_s=900, max_fires=2000,
                                status="active")
        db_mod.create_job(c, session_id=sid, label="手动暂停的", prompt="x",
                          due_at=iso(), max_fires=1, status="paused")
    for i in range(DORMANT_MAX - 1):
        tid = await _mk_wake_turn(sid, n=i + 1)
        check_dormant_gate(sid, tid, "零交易")
    assert dormant_streak(sid) == DORMANT_MAX - 1
    assert not fired                                            # 未达限不通知
    # 第 DORMANT_MAX 轮 → 触发
    tid = await _mk_wake_turn(sid, n=DORMANT_MAX)
    assert check_dormant_gate(sid, tid, "零交易") == 0
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "paused"     # active 的被暂停
        assert json.loads(db_mod.kv_get(c, f"awaitjobs:{sid}")) == [jid]
    assert dormant_streak(sid) == 0                             # 连击清零
    assert len(fired) == 1 and "自动暂停" in fired[0][0]          # 推送送达
    # 用户回话 → 恢复（复用 await 通道）
    assert resume_await_jobs(sid) == 1
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "active"


async def test_wrap_injects_dormant_warning(client):
    """_wrap 值守预警：连击 >0 时注入轮数+剩余额度+出路（agent 自救窗口）。"""
    r = await client.post("/api/sessions", json={"title": "预警注入"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        db_mod.kv_set(c, f"dormant:{sid}", "3")
    w = Scheduler(engine=None)._wrap(_job_dict(session_id=sid, fires=99), _NOW)
    assert "【值守预警】" in w
    assert "已连续 3 轮" in w and f"再值守 {DORMANT_MAX - 3} 轮" in w
    assert "【等待用户裁决】" in w                               # 出路指明
    # 无连击 → 不注入（零开销）
    with db_mod.conn() as c:
        db_mod.kv_del(c, f"dormant:{sid}")
    w2 = Scheduler(engine=None)._wrap(_job_dict(session_id=sid, fires=99), _NOW)
    assert "【值守预警】" not in w2


async def test_dormant_exempt_job_not_paused(client, monkeypatch):
    """>>> EX 豁免（rev23）：exempt_dormant=1 的 job 达限不暂停不推送——
    生产实证 c5cb 虚拟币 7×24 轻值守：无行情短回复是正常形态，v0.7.72
    「宁可误暂停」对它每 75 分钟复发一次误伤。全豁免 → 静默（无暂停无推送）。"""
    fired = []
    monkeypatch.setattr("loadn_webui.integrations.notify.fire",
                        lambda *a, **k: fired.append(a))
    r = await client.post("/api/sessions", json={"title": "豁免值守"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, session_id=sid, label="虚拟币", prompt="盯盘",
                                due_at=iso(), every_s=900, max_fires=2000,
                                status="active")
        c.execute("UPDATE scheduled_jobs SET exempt_dormant=1 WHERE id=?", (jid,))
    # 连满 DORMANT_MAX 轮值守短回复
    for i in range(DORMANT_MAX):
        tid = await _mk_wake_turn(sid, n=i + 1)
        check_dormant_gate(sid, tid, "零交易")
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "active", "豁免 job 不被暂停"
        assert db_mod.kv_get(c, f"awaitjobs:{sid}") is None       # 无 await 账
        assert db_mod.kv_get(c, f"dormant:{sid}") is None or \
            db_mod.kv_get(c, f"dormant:{sid}") == "0"             # 连击清零
    assert not fired, "全豁免：不推送"


async def test_dormant_mixed_jobs_partial_pause(client, monkeypatch):
    """>>> EX 混合：豁免+非豁免并存 → 只暂停非豁免的（豁免的继续跑）。"""
    fired = []
    monkeypatch.setattr("loadn_webui.integrations.notify.fire",
                        lambda *a, **k: fired.append(a))
    r = await client.post("/api/sessions", json={"title": "混合豁免"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        jid_a = db_mod.create_job(c, session_id=sid, label="盯盘豁免", prompt="x",
                                  due_at=iso(), every_s=900, max_fires=2000,
                                  status="active")
        jid_b = db_mod.create_job(c, session_id=sid, label="普通任务", prompt="y",
                                  due_at=iso(), every_s=900, max_fires=2000,
                                  status="active")
        c.execute("UPDATE scheduled_jobs SET exempt_dormant=1 WHERE id=?", (jid_a,))
    for i in range(DORMANT_MAX):
        tid = await _mk_wake_turn(sid, n=i + 1)
        check_dormant_gate(sid, tid, "零交易")
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid_a)["status"] == "active", "豁免的继续跑"
        assert db_mod.get_job(c, jid_b)["status"] == "paused", "非豁免照旧暂停"
        assert json.loads(db_mod.kv_get(c, f"awaitjobs:{sid}")) == [jid_b]


async def test_schedule_patch_exempt_roundtrip(client):
    """>>> EX API：PATCH /api/schedules/{id} exempt_dormant 开关 roundtrip。"""
    r = await client.post("/api/sessions", json={"title": "豁免API"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, session_id=sid, label="开关", prompt="x",
                                due_at=iso(), every_s=900, max_fires=100,
                                status="active")
    r = await client.patch(f"/api/schedules/{jid}",
                           json={"exempt_dormant": True})
    assert r.status_code == 200, r.text
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["exempt_dormant"] == 1
    r = await client.patch(f"/api/schedules/{jid}",
                           json={"exempt_dormant": False})
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["exempt_dormant"] == 0
