"""目标契约（GC-1..4）：对账式唤醒 + 反问闭环 + 轮换 anchor 目标置顶。

生产背景（2026-10 两例实证：虚拟币对冲会话 483 轮盯盘循环、worldquant
断档 25 天不自知）：长会话的历史叙事压倒 1KB 唤醒词，模型活在「历史即
指令」里——目标沉底、偏执每 15 分钟自我强化、反问只是聊天气泡会被下一
次唤醒冲掉。平台级修复四件：
  GC-1 scheduled_jobs.goal 列（API roundtrip 见 test_api_goal）
  GC-2 _wrap 对账式唤醒（目标置顶/偏执自检/反问出口）
  GC-3 await 门：行首【等待用户裁决】→ 自动暂停本会话 job，用户回话恢复
  GC-4 轮换 anchor：目标白皮书先于一切过程叙事注入
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from loadn_webui import db as db_mod
from loadn_webui.scheduler import (
    AWAIT_MARK,
    Scheduler,
    check_await_gate,
    goal_ctx_for,
    read_goal_state,
    resume_await_jobs,
)
from loadn_webui.util import iso


def _job_dict(**kw):
    base = {"id": 1, "session_id": "s1", "kind": "message", "label": "测试",
            "prompt": "检查状态并修复", "due_at": iso(), "every_s": None,
            "cron": None, "max_fires": 10, "fires": 0, "status": "active",
            "last_fired_at": None, "title": None, "profile": None,
            "engine": None, "destination": "dashboard", "is_system": 0,
            "goal": None}
    base.update(kw)
    return base


_NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _wrap(job: dict) -> str:
    return Scheduler(engine=None)._wrap(job, _NOW)


# ---------------------------------------------------------------- GC-2 对账式唤醒
async def test_wrap_goal_contract_from_job(client):
    """job.goal 优先注入：目标对账块先于唤醒词，含对账三问与反问出口。"""
    w = _wrap(_job_dict(goal="日化 1%；判据=连续 5 日滚动 ≥5%", fires=4))
    assert w.index("【目标对账") < w.index("【唤醒词】")       # 目标先于唤醒词
    assert "日化 1%" in w
    assert "对账拿不出具体事实" in w                            # 拒空话复读
    assert w.index("【唤醒词】\n检查状态并修复") >= 0
    assert AWAIT_MARK in w                                     # 反问出口常驻
    assert fires_block_absent(w)                               # fires=4 无偏执块


def fires_block_absent(w: str) -> bool:
    return "【偏执自检】" not in w


async def test_wrap_goal_from_state_json(client):
    """无 job.goal 的 message 类 → 回落会话 state.json 三字段（含已证伪路线）。"""
    r = await client.post("/api/sessions", json={"title": "目标契约"})
    sid = r.json()["session"]["id"]
    from loadn_webui import workspace as ws_mod
    ws = ws_mod.ws_of(sid)
    (ws / "state.json").write_text(json.dumps({
        "goal": "恢复 alphas 日提交量 ≥10",
        "success_criteria": "连续 7 天提交量 ≥10",
        "falsified_routes": ["纯修 v4.18 配置：修完提交量仍为 0"],
        "别的字段": "不影响"},), encoding="utf-8")
    ctx = goal_ctx_for(_job_dict(session_id=sid))
    assert ctx["goal"] == "恢复 alphas 日提交量 ≥10"
    assert ctx["falsified_routes"] == ["纯修 v4.18 配置：修完提交量仍为 0"]
    w = _wrap(_job_dict(session_id=sid))
    assert "已证伪路线（禁止重走）" in w and "v4.18" in w


async def test_wrap_no_goal_guides_charter(client):
    """目标缺失：首轮给立标引导（写 state.json 契约+向用户复述），不是空白。"""
    r = await client.post("/api/sessions", json={"title": "无目标"})
    sid = r.json()["session"]["id"]
    w = _wrap(_job_dict(session_id=sid))
    assert "【目标未记录】" in w and "success_criteria" in w


async def test_wrap_obsession_check_threshold():
    """第 5 次唤醒起注入偏执自检：换路或【等待用户裁决】二选一。"""
    w4 = _wrap(_job_dict(fires=3, cron="*/15 * * * *"))   # 本次第 4 次：未到
    w5 = _wrap(_job_dict(fires=4, cron="*/15 * * * *"))   # 本次第 5 次：触发
    assert "【偏执自检】" not in w4 and "【偏执自检】" in w5
    assert "本质不同的路线" in w5 and AWAIT_MARK in w5
    # 单次 job（无 cron/every）不注入
    assert "【偏执自检】" not in _wrap(_job_dict(fires=9))


async def test_wrap_is_system_heartbeat_skips_goal():
    """心跳巡检无战略目标语义：跳过目标块与偏执块，只留唤醒词+尾注。"""
    w = _wrap(_job_dict(is_system=1, fires=30, cron="7,37 * * * *"))
    assert "【目标对账" not in w and "【目标未记录】" not in w
    assert "【偏执自检】" not in w
    assert "【唤醒词】" in w


# ---------------------------------------------------------------- GC-3 await 门
async def test_await_gate_pauses_and_resumes(client):
    """行首命中 → active 非系统 job 全暂停+kv 记账；用户回话恢复+过期 cron 重算。"""
    r = await client.post("/api/sessions", json={"title": "反问闭环"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, session_id=sid, label="盯盘", prompt="检查",
                                due_at="2000-01-01T00:00:00+00:00",
                                cron="*/15 * * * *", max_fires=99, status="active")
        manual = db_mod.create_job(c, session_id=sid, label="手动暂停", prompt="x",
                                   due_at=iso(), max_fires=1, status="paused")
    assert check_await_gate(sid, f"发现问题如下。\n{AWAIT_MARK}日化1%在费率下不可达，选项 A/B") == 1
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "paused"
        assert db_mod.kv_get(c, f"awaitjobs:{sid}")            # 记账在场
    # 正文提及（行中非行首）不触发——防引用/讨论标记文本误判
    assert check_await_gate(sid, f"标记写法是{AWAIT_MARK}开头，注意格式") == 0
    # 用户回话 → 恢复记账那批（手动暂停的不动）；过期 cron 重算 due_at
    assert resume_await_jobs(sid) == 1
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
        assert job["status"] == "active" and job["due_at"] > iso()
        assert db_mod.get_job(c, manual)["status"] == "paused"  # 不误恢复
        assert db_mod.kv_get(c, f"awaitjobs:{sid}") is None     # 账清
    # 无账再调 → 0（幂等）
    assert resume_await_jobs(sid) == 0


async def test_await_gate_no_active_jobs(client):
    """会话无 active job：标记在场也无动作（返 0，不写账）。"""
    r = await client.post("/api/sessions", json={"title": "无 job"})
    sid = r.json()["session"]["id"]
    assert check_await_gate(sid, f"{AWAIT_MARK}需要拍板") == 0
    with db_mod.conn() as c:
        assert db_mod.kv_get(c, f"awaitjobs:{sid}") is None


# ---------------------------------------------------------------- GC-1 API goal
async def test_api_goal_roundtrip(client):
    r = await client.post("/api/sessions", json={"title": "goal api"})
    sid = r.json()["session"]["id"]
    r = await client.post("/api/schedules", json={
        "kind": "message", "session_id": sid, "prompt": "推进",
        "in": "90m", "goal": "日化 1%；判据=5 日滚动 ≥5%"})
    assert r.status_code == 200
    jid = r.json()["job"]["id"]
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["goal"] == "日化 1%；判据=5 日滚动 ≥5%"
    r = await client.patch(f"/api/schedules/{jid}", json={"goal": None})
    assert r.status_code == 200
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["goal"] is None
    # 长度闸
    r = await client.patch(f"/api/schedules/{jid}", json={"goal": "x" * 2001})
    assert r.status_code == 400


# ---------------------------------------------------------------- GC-4 轮换 anchor
async def test_rotation_anchor_goal_first(client):
    """轮换 anchor：目标白皮书先于尾部叙事注入（对抗叙事惯性继承）。"""
    r = await client.post("/api/sessions", json={"title": "anchor"})
    sid = r.json()["session"]["id"]
    from loadn_webui import workspace as ws_mod
    ws = ws_mod.ws_of(sid)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "state.json").write_text(json.dumps(
        {"goal": "日化 1%", "success_criteria": "5 日滚动 ≥5%",
         "falsified_routes": ["网格微调：4 天 -2.4%"]}), encoding="utf-8")
    from loadn_webui.engine import Engine
    spec = SimpleNamespace(name="claude",
                           session_tail=lambda _s: "【用户最后指令】看看挂单成交没")
    a = Engine._rotation_anchor(spec, sid, "old-uuid", "context_inflation")
    assert "【任务目标（对账基准，先于一切过程叙事）】" in a
    assert a.index("日化 1%") < a.index("挂单成交")            # 目标先于叙事
    assert "falsified_routes" in a or "禁止重走" in a
    # 无 goal：不炸，仍有台账指引与「过程≠目标」对账口径
    b = Engine._rotation_anchor(spec, sid + "x", "", "resume_rejected")
    assert "PROGRESS.md 是" in b and "以目标为准" in b


# ---------------------------------------------------------------- read_goal_state 容错
async def test_read_goal_state_tolerant(client):
    """缺文件/坏 JSON/非 dict → {}（契约读取永不炸）。"""
    assert read_goal_state("") == {}
    assert read_goal_state("no-such-sid-zzz") == {}
    r = await client.post("/api/sessions", json={"title": "容错"})
    sid = r.json()["session"]["id"]
    from loadn_webui import workspace as ws_mod
    ws = ws_mod.ws_of(sid)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "state.json").write_text("{bad json", encoding="utf-8")
    assert read_goal_state(sid) == {}
    (ws / "state.json").write_text('["不是对象"]', encoding="utf-8")
    assert read_goal_state(sid) == {}
