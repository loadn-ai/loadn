"""定时调度器（P0-1）：到期投递 / 递归推进 / 防跑飞 / API CRUD。

tick 在测试自己的 loop 里跑（独立 Engine 实例，db 共享）——服务线程的
全局 SCHEDULER 20s 才扫一轮且测试 job 即建即触发，不与其竞速。
"""
import asyncio

import pytest
import pytest_asyncio

from loadn_webui import db as db_mod
from loadn_webui.scheduler import Scheduler, parse_when


@pytest_asyncio.fixture()
async def sched():
    from loadn_webui.engine import Engine
    eng = Engine()
    s = Scheduler(eng)
    yield s
    for t in list(eng._workers.values()):
        t.cancel()
    # 在跑 turn 的 stop 句柄也收掉，防子进程残留
    for at in list(eng.active.values()):
        at.stop.stop()


async def _wait_jobs_done(sched, sid: str, n_turns: int, timeout_s: float = 15):
    """等投递的 turn 全部到终态（fake claude 秒级完成）。"""
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < timeout_s:
        await asyncio.sleep(0.15)
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY id", (sid,)).fetchall()
            done = [r for r in rows if r["status"] in ("done", "error", "stopped")]
        if len(done) >= n_turns and not any(r["status"] in ("queued", "running")
                                            for r in rows):
            return rows
    raise TimeoutError(f"{timeout_s}s 内 turn 未全部终态（{len(rows)} 条）")


def _make_job(sid: str, due_at: str, prompt="继续任务", every_s=None, max_fires=1,
              status="active", **extra):
    with db_mod.conn() as c:
        return db_mod.create_job(c, session_id=sid, label="测试唤醒", prompt=prompt,
                                 due_at=due_at, every_s=every_s, max_fires=max_fires,
                                 status=status, **extra)


async def test_fire_once(sched, client):
    r = await client.post("/api/sessions", json={"title": "调度测试", "first_message": "开始"})
    sid = r.json()["session"]["id"]
    from loadn_webui.util import iso
    jid = _make_job(sid, iso())          # 已到期
    assert await sched.tick() == 1
    rows = await _wait_jobs_done(sched, sid, n_turns=2)   # 首条消息 + 调度投递
    # 投递的 user 消息带【定时唤醒】包装与确认纪律提示
    with db_mod.conn() as c:
        msgs = [m["content"] for m in db_mod.list_messages(c, sid) if m["role"] == "user"]
        job = db_mod.get_job(c, jid)
    assert any("【定时唤醒】测试唤醒" in m and "确认纪律" in m for m in msgs)
    assert job["status"] == "done" and job["fires"] == 1


async def test_recurring_advances_and_caps(sched, client):
    r = await client.post("/api/sessions", json={"title": "递归调度"})
    sid = r.json()["session"]["id"]
    from loadn_webui.util import iso
    jid = _make_job(sid, iso(), every_s=3600, max_fires=2)
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
    assert job["fires"] == 1 and job["status"] == "active"
    assert job["due_at"] > iso()          # due_at 推进 1h，不再到期
    assert await sched.tick() == 0         # 不追帧：同一 job 一轮只触发一次
    # 手动回拨到过去 → 第二次触发 → 达 max_fires 置 done
    with db_mod.conn() as c:
        db_mod.update_job(c, jid, due_at="2000-01-01T00:00:00+00:00")
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
    assert job["fires"] == 2 and job["status"] == "done"
    await _wait_jobs_done(sched, sid, n_turns=2)


async def test_paused_and_archived(sched, client):
    r = await client.post("/api/sessions", json={"title": "暂停调度"})
    sid = r.json()["session"]["id"]
    from loadn_webui.util import iso
    # paused 不触发
    _make_job(sid, iso(), status="paused")
    assert await sched.tick() == 0
    # 会话归档 → active job 自动 paused、不投递
    jid = _make_job(sid, iso())
    await client.patch(f"/api/sessions/{sid}", json={"status": "archived"})
    assert await sched.tick() == 0
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "paused"


async def test_api_schedule_crud(client):
    r = await client.post("/api/sessions", json={"title": "API 调度"})
    sid = r.json()["session"]["id"]

    # 校验分支：无 at/in → 400
    r = await client.post(f"/api/sessions/{sid}/schedules",
                          json={"prompt": "x"})
    assert r.status_code == 400
    # every_s 越界 → 400
    r = await client.post(f"/api/sessions/{sid}/schedules",
                          json={"prompt": "x", "in": "5m", "every_s": 30})
    assert r.status_code == 400

    r = await client.post(f"/api/sessions/{sid}/schedules",
                          json={"label": "到期巡检", "prompt": "巡检凭证",
                                "in": "1h", "every_s": 86400, "max_fires": 30})
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["max_fires"] == 30 and job["every_s"] == 86400
    assert job["status"] == "active" and job["due_at"] > parse_when(at="2000-01-01")

    # session_detail 带 schedules + next_wake
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["next_wake"]["label"] == "到期巡检" and len(d["schedules"]) == 1

    # pause → next_wake 消失（无 active job）
    r = await client.patch(f"/api/schedules/{job['id']}", json={"status": "paused"})
    assert r.status_code == 200
    d = (await client.get(f"/api/sessions/{sid}")).json()
    assert d["next_wake"] is None
    # 全局列表 + 删除
    lst = (await client.get("/api/schedules", params={"sid": sid})).json()["schedules"]
    assert len(lst) == 1
    assert (await client.delete(f"/api/schedules/{job['id']}")).status_code == 200
    assert (await client.delete(f"/api/schedules/{job['id']}")).status_code == 404


async def test_parse_when_roundtrip():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 15, 8, 0, tzinfo=timezone.utc)
    assert parse_when(in_="90m", base=now) == "2026-09-15T09:30:00+00:00"
    assert parse_when(in_="1h30m", base=now) == "2026-09-15T09:30:00+00:00"
    # UTC ISO 直收（前端传的就是这个口径）
    assert parse_when(at="2026-09-15T10:00:00+00:00") == "2026-09-15T10:00:00+00:00"
    with pytest.raises(ValueError):
        parse_when(at="not-a-time", base=now)
    with pytest.raises(ValueError):
        parse_when(in_="0m", base=now)


# ---------------------------------------------------------------- cron 触发
async def test_cron_job_advances(sched, client):
    """cron job 触发后 due_at 重算为下一个允许点（严格 > now）；达限 done。"""
    from loadn_webui.cron import next_run_iso
    from loadn_webui.util import iso
    r = await client.post("/api/sessions", json={"title": "cron 调度"})
    sid = r.json()["session"]["id"]
    jid = _make_job(sid, iso(), cron="*/1 * * * *", every_s=None, max_fires=2)
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
    assert job["fires"] == 1 and job["status"] == "active"
    # due_at 推进到下一分钟整点，且严格大于现在
    assert job["due_at"].endswith(":00+00:00") and job["due_at"] > iso()
    assert job["due_at"] >= next_run_iso("*/1 * * * *")  # 与真源重算一致
    # 回拨再触发一次 → 达 max_fires 置 done
    with db_mod.conn() as c:
        db_mod.update_job(c, jid, due_at="2000-01-01T00:00:00+00:00")
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        assert db_mod.get_job(c, jid)["status"] == "done"
    await _wait_jobs_done(sched, sid, n_turns=2)


# ---------------------------------------------------------------- 新会话动作
async def test_new_session_job(sched, client):
    """new_session：到点新建会话 + 投递【定时唤醒】；job 行 session_id 恒空。"""
    from loadn_webui.util import iso
    jid = _make_job(None, iso(), kind="new_session", title="每日巡检报告",
                    profile=None, engine=None, max_fires=1,
                    prompt="跑一遍全平台巡检并出报告")
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
        row = c.execute("SELECT id FROM sessions WHERE title=?", ("每日巡检报告",)).fetchone()
        assert row is not None
        sid = row["id"]
        msgs = [m["content"] for m in db_mod.list_messages(c, sid) if m["role"] == "user"]
    assert job["fires"] == 1 and job["status"] == "done"
    assert job["session_id"] is None            # 不回填：递归时每次建全新会话
    assert any("【定时唤醒】" in m for m in msgs)
    await _wait_jobs_done(sched, sid, n_turns=1)


async def test_new_session_recurring_new_each_fire(sched, client):
    """递归 new_session：两次 fire 建两个不同会话（每天一个干净上下文）。"""
    from loadn_webui.util import iso
    jid = _make_job(None, iso(), kind="new_session", title="日更任务",
                    every_s=3600, max_fires=2, prompt="干活")
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        db_mod.update_job(c, jid, due_at="2000-01-01T00:00:00+00:00")
    assert await sched.tick() == 1
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
        rows = c.execute("SELECT id FROM sessions WHERE title=?", ("日更任务",)).fetchall()
    assert job["fires"] == 2 and job["status"] == "done"
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]


# ---------------------------------------------------------------- 撞车插话
async def test_fire_message_steers_running_turn(client, ws_root, tmp_path, monkeypatch):
    """运行中的 hahaness turn 撞上定时唤醒 → 插话实时注入（.steer.jsonl 有
    条目、不排新 turn）；无运行 turn 时回落 submit（turns +1）。"""
    from loadn_webui.config import CONFIG
    from loadn_webui.engine import ENGINE
    monkeypatch.setattr(CONFIG.engines, "default", "hahaness")
    monkeypatch.setenv("HAHANESS_HOME", str(tmp_path / "hh_home"))
    monkeypatch.setenv("HAHANESS_PROVIDER", "fake")
    fake = ws_root / ".sched-steer-test" / ".fake"
    # 用未建会话路径直接占位不行——走真实会话
    r = await client.post("/api/sessions", json={"title": "撞车插话"})
    sid = r.json()["session"]["id"]
    fake = ws_root / sid / ".fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "tools").write_text('{"name": "Bash", "input": {"command": "sleep 3"}}')

    r = await client.post(f"/api/sessions/{sid}/messages", json={"text": "跑着"})
    tid = r.json()["turn"]["id"]
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < 10:
        if any(b.get("type") == "tool" for b in ENGINE.active[tid].blocks):
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("首轮工具未出现")

    # 到点触发 message job（打在运行中的 hahaness turn 上）
    from loadn_webui.scheduler import Scheduler
    from loadn_webui.util import iso
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, session_id=sid, label="比赛开赛", prompt="参赛",
                                due_at=iso(), max_fires=1)
    assert await Scheduler(ENGINE).tick() == 1
    steer_file = ws_root / sid / f".steer.{sid}.jsonl"
    assert "比赛开赛" in steer_file.read_text()      # 插话写入
    with db_mod.conn() as c:
        n_turns = len(c.execute(
            "SELECT id FROM turns WHERE session_id=?", (sid,)).fetchall())
    assert n_turns == 1                              # 没排新 turn

    # 等 turn 结束后（插话未消费会回队列，这里工具轮后会消费）再触发一次：
    # 无 running turn → 回落 submit
    t0 = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() - t0 < 30:
        with db_mod.conn() as c:
            if c.execute("SELECT status FROM turns WHERE id=?", (tid,)).fetchone()[
                    "status"] in ("done", "error", "stopped"):
                break
        await asyncio.sleep(0.3)
    with db_mod.conn() as c:
        db_mod.update_job(c, jid, status="active", due_at=iso(), fires=0)
    assert await Scheduler(ENGINE).tick() == 1
    with db_mod.conn() as c:
        n_turns = len(c.execute(
            "SELECT id FROM turns WHERE session_id=?", (sid,)).fetchall())
    assert n_turns >= 2                              # 排队路径生效


# ---------------------------------------------------------------- API v2：全局端点 + PATCH 增强
async def test_api_global_endpoints(client):
    """POST /api/schedules：message 需 session_id、new_session 校验、cron 优先；
    GET 带 session_title/cron_desc。"""
    r = await client.post("/api/sessions", json={"title": "全局调度"})
    sid = r.json()["session"]["id"]

    # message 缺 session_id → 400
    r = await client.post("/api/schedules", json={"prompt": "x", "in": "5m"})
    assert r.status_code == 400
    # bad cron / bad engine / bad profile → 400
    r = await client.post("/api/schedules",
                          json={"prompt": "x", "cron": "99 * * * *"})
    assert r.status_code == 400
    r = await client.post("/api/schedules",
                          json={"prompt": "x", "cron": "0 20 * * *",
                                "kind": "new_session", "engine": "nope"})
    assert r.status_code == 400
    r = await client.post("/api/schedules",
                          json={"prompt": "x", "cron": "0 20 * * *",
                                "kind": "new_session", "profile": "nope"})
    assert r.status_code == 400

    # cron new_session：字段落库 + due_at 是 cron 下次
    from loadn_webui.cron import next_run_iso
    r = await client.post("/api/schedules", json={
        "kind": "new_session", "title": "每晚比赛", "engine": "hahaness",
        "label": "参赛", "prompt": "打今晚的比赛", "cron": "0 20 * * *"})
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["kind"] == "new_session" and job["session_id"] is None
    assert job["cron"] == "0 20 * * *" and job["every_s"] is None
    assert job["max_fires"] == 20
    assert job["due_at"] == next_run_iso("0 20 * * *")

    # cron message（走全局端点带 session_id）
    r = await client.post("/api/schedules", json={
        "session_id": sid, "prompt": "到点干活", "cron": "*/5 * * * *"})
    assert r.status_code == 200
    mid = r.json()["job"]["id"]

    # GET 列表带 session_title/cron_desc
    rows = (await client.get("/api/schedules")).json()["schedules"]
    mine = [x for x in rows if x["id"] == mid][0]
    assert mine["session_title"] == "全局调度"
    assert mine["cron_desc"] == "每 5 分钟"
    newones = [x for x in rows if x["kind"] == "new_session"]
    assert all(x["session_title"] is None for x in newones)
    assert any(x["cron_desc"] == "每天 20:00" for x in newones)


async def test_api_patch_reschedule(client):
    """PATCH：改 at→due_at 变；every 改 cron→every_s 清空+重算；paused cron
    过期 resume→due_at 未来；done 改触发→400。"""
    from loadn_webui.util import iso
    r = await client.post("/api/sessions", json={"title": "改期"})
    sid = r.json()["session"]["id"]

    r = await client.post(f"/api/sessions/{sid}/schedules",
                          json={"prompt": "x", "at": "2030-01-01 08:00"})
    jid = r.json()["job"]["id"]
    # 改 label/prompt/max_fires
    r = await client.patch(f"/api/schedules/{jid}",
                           json={"label": "改名", "prompt": "新指令", "max_fires": 5})
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["label"] == "改名" and job["prompt"] == "新指令" and job["max_fires"] == 5
    # 改 at → due_at 变
    r = await client.patch(f"/api/schedules/{jid}", json={"at": "2031-06-01 09:30"})
    assert r.json()["job"]["due_at"].startswith("2031-06-01")
    # 换成 cron → every_s 清空、due_at 重算
    r = await client.patch(f"/api/schedules/{jid}", json={"cron": "30 21 * * *"})
    job = r.json()["job"]
    assert job["cron"] == "30 21 * * *" and job["every_s"] is None
    assert job["due_at"] > iso()

    # paused 的 cron job due_at 已过期 → resume 重算到未来（跳过错过的时刻）
    with db_mod.conn() as c:
        db_mod.update_job(c, jid, status="paused", due_at="2000-01-01T00:00:00+00:00")
    r = await client.patch(f"/api/schedules/{jid}", json={"status": "active"})
    assert r.json()["job"]["due_at"] > iso()

    # done 是终态：改触发 → 400；改 status → 400（active 非法值）
    with db_mod.conn() as c:
        db_mod.update_job(c, jid, status="done")
    r = await client.patch(f"/api/schedules/{jid}", json={"at": "2030-01-01 08:00"})
    assert r.status_code == 400
    r = await client.patch(f"/api/schedules/{jid}", json={"status": "active"})
    assert r.status_code == 400


async def test_parse_when_accepts_js_iso():
    """前端 toISOString 产物（毫秒+Z 后缀，Python 3.10 fromisoformat 不认 Z）
    直接可用——间隔递归创建路径回归（曾 400）。"""
    assert parse_when(at="2026-09-18T17:20:51.441Z") == "2026-09-18T17:20:51+00:00"
    assert parse_when(at="2026-09-18T17:20:51Z") == "2026-09-18T17:20:51+00:00"
    with pytest.raises(ValueError):
        parse_when(at="not-a-time-Z")


async def test_r2_system_job_guard_and_sentinel(client):
    """二轮修#7/#13 对赌：内置心跳 cron=7,37 双点（30min 档——"7,30" 在
    本仓解析=单值每小时一次）；PATCH is_system → 403（守卫不可绕过）；
    DELETE → kv 哨兵写入 + ensure_heartbeat 不复活（删即关≠重启复活）。"""
    from loadn_webui.scheduler import ensure_heartbeat
    # 全量序自净：前置测试可能已建 system job / 写过关闭哨兵——清出干净
    # 现场（本测试自带全部前置，不依赖全局状态）
    with db_mod.conn() as c:
        c.execute("DELETE FROM scheduled_jobs WHERE is_system=1")
        c.execute("DELETE FROM kv WHERE key='heartbeat_disabled'")
    ensure_heartbeat(None)
    with db_mod.conn() as c:
        row = c.execute("SELECT id, cron FROM scheduled_jobs "
                        "WHERE is_system=1 AND kind='new_session'").fetchone()
    assert row is not None
    assert row["cron"] == "7,37 * * * *", "30min 档须双分钟点（7,30=单值 hourly）"
    jid = row["id"]

    # 守卫否定路径：档位字段 PATCH 都 403（fail-closed，不是部分字段白名单）
    r = await client.patch(f"/api/schedules/{jid}", json={"label": "hack"})
    assert r.status_code == 403 and "不可编辑" in r.json()["detail"]
    r = await client.patch(f"/api/schedules/{jid}", json={"cron": "* * * * *"})
    assert r.status_code == 403
    # status-only 放行（「停用走暂停」的兑现）：暂停 → 恢复往返
    r = await client.patch(f"/api/schedules/{jid}", json={"status": "paused"})
    assert r.status_code == 200 and r.json()["job"]["status"] == "paused"
    r = await client.patch(f"/api/schedules/{jid}", json={"status": "active"})
    assert r.status_code == 200 and r.json()["job"]["status"] == "active"
    # 夹带私货：status+label 混合 → 仍 403（不是「含 status 即放行」）
    r = await client.patch(f"/api/schedules/{jid}",
                           json={"status": "paused", "label": "hack"})
    assert r.status_code == 403
    with db_mod.conn() as c:      # 守卫反转对赌：DB 里真没被改
        assert c.execute("SELECT cron FROM scheduled_jobs WHERE id=?",
                         (jid,)).fetchone()["cron"] == "7,37 * * * *"

    # DELETE → 哨兵 + 不复活
    r = await client.delete(f"/api/schedules/{jid}")
    assert r.status_code == 200
    with db_mod.conn() as c:
        v = c.execute("SELECT value FROM kv WHERE "
                      "key='heartbeat_disabled'").fetchone()
        assert v is not None and v["value"] == "1"
    ensure_heartbeat(None)
    with db_mod.conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM scheduled_jobs WHERE "
                      "is_system=1").fetchone()["n"]
    assert n == 0, "哨兵生效：重启不重建（永久关闭语义）"
    # 清哨兵 → 重建（幂等创建路径不受污染）
    with db_mod.conn() as c:
        c.execute("DELETE FROM kv WHERE key='heartbeat_disabled'")
    ensure_heartbeat(None)
    with db_mod.conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM scheduled_jobs WHERE "
                         "is_system=1").fetchone()["n"] == 1
