"""P11 heartbeat + routine 模板包（fake notify/引擎）：验收三件。

①三档 destination（dashboard 零改 / notify 推送 / notify+artifact 目录）
②三防（忙跳过 / 空输出不投递 / 连续 3 轮无产出降频）
③一键安装生成正确 schedule（复制非引用，is_system=0）
"""
from __future__ import annotations

from loadn_webui.scheduler import Scheduler


def _get(jid):
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:
        return db_mod.to_dict(db_mod.get_job(c, jid))


class FakeEngine:
    def __init__(self):
        self.submitted = []
        self.published = []

    async def submit(self, sid, text, mode="foreground", attachments=None):
        self.submitted.append((sid, text))
        return 1

    def steer_if_running(self, sid, text):
        return None

    def publish(self, sid, type_, data, turn_id=None):
        self.published.append((sid, type_, data))


def _job(**over):
    base = {"id": 1, "kind": "new_session", "label": "测试例程", "prompt": "干活",
            "fires": 0, "max_fires": 100, "cron": "0 8 * * *",
            "every_s": None, "session_id": None, "profile": "auto",
            "engine": "", "title": None, "destination": "dashboard",
            "last_fired_at": None, "status": "active"}
    base.update(over)
    return base


# ---------------------------------------------------------------- ① destination
async def test_destinations(monkeypatch, tmp_path):
    eng = FakeEngine()
    sched = Scheduler(eng)
    # dashboard（默认）：不推不建目录
    sent = []
    monkeypatch.setattr("loadn_webui.integrations.notify.fire",
                        lambda *a, **k: sent.append((a, k)))
    await sched._deliver_destination(_job(destination="dashboard"), "s1", "L")
    assert not sent
    # notify：推送带会话 id
    await sched._deliver_destination(_job(destination="notify"), "s1", "L")
    assert sent and "s1" in sent[-1][0][1]
    # notify+artifact：推送 + artifacts 目录建在真实 workspace
    import loadn_webui.profile as profile_mod
    import loadn_webui.workspace as ws_mod
    sid, ws = ws_mod.create_session("P11 产物", profile_mod.get("assistant"),
                                    None, None)
    n0 = len(sent)
    await sched._deliver_destination(_job(destination="notify+artifact"), sid, "L")
    assert len(sent) == n0 + 1
    assert (ws / "artifacts").is_dir()
    assert str(ws / "artifacts") in sent[-1][0][1]


# ---------------------------------------------------------------- ② 三防
async def test_heartbeat_three_guards(client, monkeypatch, tmp_path):
    from loadn_webui import db as db_mod
    eng = FakeEngine()
    sched = Scheduler(eng)
    # 防忙：造 running turn → 跳过
    r = await client.post("/api/sessions", json={"title": "忙"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        c.execute("INSERT INTO turns(session_id,status,mode) VALUES(?,"
                  "'running','background')", (sid,))
        hb = db_mod.create_job(c, kind="new_session", label="🫀 心跳巡检",
                               prompt="x", cron="7/30 * * * *",
                               due_at="2000-01-01T00:00:00",
                               profile="assistant", is_system=1)
    job = _get(hb)
    fired = await sched.fire_heartbeat(job)
    assert fired is False                      # 忙跳过
    job2 = _get(hb)
    assert "×1" in job2["label"]               # 空轮计数起步
    # 防连续空转：凑满 ×3 标记 → 降频（cron→2h + 🫀·low 前缀）
    with db_mod.conn() as c:
        db_mod.update_job(c, hb, label="🫀 心跳巡检 ×3",
                          due_at="2000-01-01T00:00:00")
    job3 = _get(hb)
    with db_mod.conn() as c:
        c.execute("UPDATE turns SET status='done' WHERE session_id=?", (sid,))
    fired = await sched.fire_heartbeat(job3)
    assert fired is False                      # 降频路径不投递
    job4 = _get(hb)
    assert "🫀·low" in job4["label"]
    assert job4["cron"].split()[1].startswith("*/2") or "*/2" in job4["cron"]
    # 已降频后：再触发不重复降频、不投递（计数继续）
    label_before = job4["label"]
    fired = await sched.fire_heartbeat(job4)
    assert fired is False
    job5 = _get(hb)
    assert job5["label"].startswith(label_before.split(" ×")[0])
    # 正常态（无 running turn、无 ×3）：实投（new_session 真路径）
    with db_mod.conn() as c:
        db_mod.update_job(c, hb, label="🫀 心跳巡检",
                          due_at="2000-01-01T00:00:00")
    job6 = _get(hb)
    fired = await sched.fire_heartbeat(job6)
    assert fired is True and eng.submitted     # 真投递（wrap 含巡检模板）


# ---------------------------------------------------------------- ③ 安装
async def test_install_creates_user_schedule(client):
    r = await client.get("/api/routines")
    assert r.status_code == 200
    rs = r.json()["routines"]
    assert len(rs) == 5 and {x["key"] for x in rs} >= {
        "morning_brief", "news_watch", "cred_budget_check",
        "day_reminder", "repo_daily"}
    ready_map = {x["key"]: x["ready"] for x in rs}
    assert ready_map["morning_brief"] is True      # 无资源声明恒 Ready
    # 一键安装 → 用户 schedule（is_system=0、cron/prompt 来自模板）
    r = await client.post("/api/routines/morning_brief/install")
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["kind"] == "new_session" and job["cron"] == "0 8 * * *"
    assert job["is_system"] == 0 and job["label"] == "晨报"
    assert "晨报" in job["prompt"] or "定时" in job["prompt"] or job["prompt"]
    assert job["destination"] == "notify"
    assert job["due_at"] > "2000"
    # 复制语义：模板改动不影响已装实例（job.prompt 是独立文本）
    assert (await client.post("/api/routines/no-such/install")).status_code == 404
    assert (await client.post("/api/routines/morning_brief/install",
                              json={"cron": "bad cron"})).status_code == 400
