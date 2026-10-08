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
    # 二轮修#2：忙跳过不计数（×N=连续无产出，忙≠无产出）——label 不动
    assert "×" not in job2["label"]
    assert job2["due_at"] > "2000"             # due_at 照常推进（不热循环）
    # 防连续空转：凑满 ×3 标记 → 降频（cron→2h + 🫀·low 前缀）
    with db_mod.conn() as c:
        db_mod.update_job(c, hb, label="🫀 心跳巡检 ×3",
                          due_at="2000-01-01T00:00:00")
    job3 = _get(hb)
    with db_mod.conn() as c:
        # 隔离：busy 检查是全局的——其他测试文件留下的 running/queued turn
        # 会让本步走 busy 分支（真实语义），清场后再验降频分支
        c.execute("UPDATE turns SET status='done'")
    fired = await sched.fire_heartbeat(job3)
    assert fired is False                      # 降频路径不投递
    job4 = _get(hb)
    assert "🫀·low" in job4["label"]
    assert job4["cron"].split()[1].startswith("*/2") or "*/2" in job4["cron"]
    # 三轮修新语义：低频档空转**照投**（2h 探针自愈——否则 ×3 后永久停投）
    fired = await sched.fire_heartbeat(job4)
    assert fired is True
    job5 = _get(hb)
    assert "×" not in job5["label"]            # 实投清零计数
    assert "🫀·low" not in job5["label"]       # 探针投出 → 恢复高频档
    assert job5["cron"] == "7,37 * * * *"
    # 垫高 ×2 → 上一轮有产出（造真 assistant 文本）→ 不计数照投 → 清零
    with db_mod.conn() as c:
        c.execute("UPDATE turns SET status='done'")
        db_mod.update_job(c, hb, label="🫀 心跳巡检 ×2",
                          due_at="2000-01-01T00:00:00")
        sid5 = _get(hb)["session_id"]
        assert sid5                             # 三轮修：实投回填 sid
        tid = c.execute("INSERT INTO turns(session_id,status,mode) "
                        "VALUES(?, 'done','background')", (sid5,)).lastrowid
        c.execute("INSERT INTO messages(session_id,turn_id,role,content) "
                  "VALUES(?,?, 'assistant', ?)",
                  (sid5, tid, "巡检完成：三服务健康，无新增告警项。" * 2))
    job_x = _get(hb)
    fired = await sched.fire_heartbeat(job_x)
    assert fired is True
    assert _get(hb)["label"] == "🫀 心跳巡检"      # 实投清零
    # 正常态（无 running turn、上轮有产出）：实投（new_session 真路径）
    with db_mod.conn() as c:
        c.execute("UPDATE turns SET status='done'")
        db_mod.update_job(c, hb, label="🫀 心跳巡检",
                          due_at="2000-01-01T00:00:00")
    job6 = _get(hb)
    fired = await sched.fire_heartbeat(job6)
    assert fired is True and eng.submitted     # 真投递（wrap 含巡检模板）


# ---------------------------------------------------------------- ③ 安装
async def test_install_creates_user_schedule(client, monkeypatch):
    r = await client.get("/api/routines")
    assert r.status_code == 200
    rs = r.json()["routines"]
    assert len(rs) == 5 and {x["key"] for x in rs} >= {
        "morning_brief", "news_watch", "cred_budget_check",
        "day_reminder", "repo_daily"}
    ready_map = {x["key"]: x["ready"] for x in rs}
    assert ready_map["morning_brief"] is True      # 无资源声明恒 Ready
    # Needs setup 语义对赌：notify 通道未配 → day_reminder 报缺项
    from loadn_webui.config import CONFIG
    monkeypatch.setattr(CONFIG.notify, "provider", "")
    r = await client.get("/api/routines")
    day = next(x for x in r.json()["routines"] if x["key"] == "day_reminder")
    assert day["ready"] is False and day["missing"] == ["通知通道（设置→通知）"]
    monkeypatch.setattr(CONFIG.notify, "provider", "bark")
    r = await client.get("/api/routines")
    day = next(x for x in r.json()["routines"] if x["key"] == "day_reminder")
    assert day["ready"] is True
    monkeypatch.setattr(CONFIG.notify, "provider", "")
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


async def test_fix_heartbeat_routes_via_fire(client, monkeypatch):
    """复查修#1 对赌：经 fire()（生产路由）触发 is_system job 走三防——
    原实现 fire 只按 kind 分流，三防在生产中是死代码。"""
    from loadn_webui import db as db_mod
    eng = FakeEngine()
    sched = Scheduler(eng)
    r = await client.post("/api/sessions", json={"title": "fire 路由"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        c.execute("DELETE FROM turns WHERE status IN ('running','queued')")  # 清他测残留
        c.execute("INSERT INTO turns(session_id,status,mode) VALUES(?,"
                  "'running','background')", (sid,))
        hb = db_mod.create_job(c, kind="new_session", label="🫀 心跳巡检",
                               prompt="x", cron="7/30 * * * *",
                               due_at="2000-01-01T00:00:00",
                               profile="assistant", is_system=1)
    job = _get(hb)
    fired = await sched.fire(job)                 # ← 生产路由（非直调）
    assert fired is False                          # 忙 → 三防跳过
    j2 = _get(hb)
    assert "×" not in j2["label"]                 # 忙跳过不计数（新语义）
    assert j2["due_at"] > "2000"
    # 对照：非 system 的 new_session job 同条件走普通路径（实投不计数）
    with db_mod.conn() as c:
        nb = db_mod.create_job(c, kind="new_session", label="普通任务",
                               prompt="干活", cron="0 8 * * *",
                               due_at="2000-01-01T00:00:00", is_system=0)
    nj = _get(nb)
    fired2 = await sched.fire(nj)
    assert fired2 is True                          # 忙不挡普通 job（原语义）
    assert "×" not in _get(nb)["label"]


async def test_r2_row_path_and_killall(client, monkeypatch):
    """二轮修#1 对赌：tick（sqlite3.Row 生产路径）触发 system job 不炸、
    KILL_ALL 熔断不再被 AttributeError 反转（fail-open）。"""
    import sqlite3

    from loadn_webui import db as db_mod
    from loadn_webui.config import PATHS
    eng = FakeEngine()
    sched = Scheduler(eng)
    r = await client.post("/api/sessions", json={"title": "Row 路径"})
    sid = r.json()["session"]["id"]
    with db_mod.conn() as c:
        c.execute("DELETE FROM turns WHERE status IN ('running','queued')")
        hb = db_mod.create_job(c, kind="new_session", label="🫀 心跳巡检",
                               prompt="x", cron="7/30 * * * *",
                               due_at="2000-01-01T00:00:00",
                               profile="assistant", is_system=1)
    # Row 直传（tick 形态——不经 to_dict）
    with db_mod.conn() as c:
        row = db_mod.get_job(c, hb)
    assert isinstance(row, sqlite3.Row)
    fired = await sched.fire(row)                # Row 进 fire_heartbeat 全链
    assert fired is True                          # 空闲+无×N → 实投（无 AttributeError）
    # KILL_ALL 熔断：标记存在时 Row 路径必须拒投（原被 .get('id') 炸穿）
    with db_mod.conn() as c:
        db_mod.update_job(c, hb, label="🫀 心跳巡检",
                          due_at="2000-01-01T00:00:00")
    (PATHS["run"] / "KILL_ALL").write_text("x")
    try:
        with db_mod.conn() as c:
            row2 = db_mod.get_job(c, hb)
        assert await sched.fire(row2) is False    # 熔断生效（不再 fail-open）
    finally:
        (PATHS["run"] / "KILL_ALL").unlink(missing_ok=True)


async def test_r3_heartbeat_empties_counted_via_real_path(client, monkeypatch):
    """三轮修对赌：空转计数走**真路径**——真投一轮（无产出）→ 下轮 fire
    判定上轮空 → ×1 且不投（原 _heartbeat_last_empty 返回值被丢弃、
    _hb_advance 无 count=True 调用点，×N 恒 0，三防②③全死——旧测试
    手工改 label 垫 ×3 才绿，正是盲区）。"""
    from loadn_webui import db as db_mod
    eng = FakeEngine()
    sched = Scheduler(eng)
    with db_mod.conn() as c:
        c.execute("UPDATE turns SET status='done'")   # 清 busy
        hb = db_mod.create_job(c, kind="new_session", label="🫀 心跳巡检",
                               prompt="x", cron="7,37 * * * *",
                               due_at="2000-01-01T00:00:00",
                               profile="assistant", is_system=1)
    # 第一轮：无 ×、无 sid → 真投（FakeEngine 不产 assistant 消息=空会话）
    fired = await sched.fire_heartbeat(_get(hb))
    assert fired is True
    sid = _get(hb)["session_id"]
    assert sid                                   # 实投回填（判定的数据源）
    # 模拟引擎落账：done turn 无 assistant 文本（空产出形态）
    with db_mod.conn() as c:
        c.execute("INSERT INTO turns(session_id,status,mode) "
                  "VALUES(?, 'done','background')", (sid,))
    # 第二轮：上轮空 → ×1 且不投
    fired = await sched.fire_heartbeat(_get(hb))
    assert fired is False
    job2 = _get(hb)
    assert job2["label"].endswith("×1"), job2["label"]
    assert job2["session_id"] == sid
    # 第三轮：仍空 → ×2；第四轮 ×3 → 降频（cron 2h + 🫀·low）
    await sched.fire_heartbeat(_get(hb))
    assert _get(hb)["label"].endswith("×2")
    await sched.fire_heartbeat(_get(hb))
    job4 = _get(hb)
    assert "🫀·low" in job4["label"] and "*/2" in job4["cron"]


async def test_r3_install_idempotent(client):
    """三轮修对赌：同模板连续 install 两次 → 单行 job（原双击/重发=每天
    双份晨报双会话双推送），返回 existing 标记。"""
    from loadn_webui import db as db_mod
    with db_mod.conn() as c:      # 全量序自净：前序测试可能已装过晨报
        c.execute("DELETE FROM scheduled_jobs WHERE label='晨报' "
                  "AND is_system=0")
    r1 = await client.post("/api/routines/morning_brief/install")
    assert r1.status_code == 200 and r1.json().get("existing") is not True
    r2 = await client.post("/api/routines/morning_brief/install")
    assert r2.status_code == 200 and r2.json().get("existing") is True
    assert r1.json()["job"]["id"] == r2.json()["job"]["id"]
    label = r1.json()["job"]["label"]
    with db_mod.conn() as c:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM scheduled_jobs WHERE label=? "
            "AND is_system=0", (label,)).fetchone()["n"]
    assert n == 1
