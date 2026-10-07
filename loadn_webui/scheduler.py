"""定时调度器：到点唤醒 agent（时间性等待的平台化）。

证书项目的教训：考试冷却 2h/24h/7 天、发证同步"最长 1 天"、证书 2027 年
到期——全靠 PROGRESS.md 手记 + 人肉回访。本模块把「等 X 后继续」变成
平台原语：scheduled_jobs 表驱动（durable，服务重启后停机期间到期的补投
一次），asyncio 循环扫描 due_at。

两种动作（kind）：
- message：投递到现有会话——撞上运行中的 loadn turn 走插话实时注入
  （比赛 20:00 开赛不能排队等），否则 engine.submit（FIFO 队列天然排队）
- new_session：到点新建会话投递（定时启新任务；递归每次建全新会话）

三种触发：at/in 一次性、every_s 间隔递归、cron 5 段表达式（cron 串是
真源，due_at 是算出的下次值缓存——fire 推进/创建/PATCH 三处经
cron.next_run_iso 重算）。

防跑飞三闸：max_fires 触发上限（达限置 done）；单次 job 触发即 done；
归档会话上的 job 自动 paused。

红线不绕过：调度器只负责唤醒——投递的 prompt 带确认纪律提示，宪法 §2.6
（对外发送先问）对调度触发的 turn 照常生效。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from . import db as db_mod
from .util import get_logger, iso

log = get_logger(__name__)

CHECK_INTERVAL = 20.0          # 扫描周期：分钟级唤醒场景足够，误差可忽略

# 投递 prompt 的包装：标明来源，重申确认纪律（防自动化绕过宪法）
_WRAP = ("【定时唤醒】{label}\n\n{prompt}\n\n"
         "（本消息由平台调度器于 {now} 自动投递"
         "{note}。凭 PROGRESS.md / state.json 无损续作；"
         "若本步涉及对外发送/支付/删除等操作，宪法确认纪律照常生效——"
         "内容或对象不明确就停下来把问题写进回复，等用户下一条消息。）")


def parse_when(at: str = "", in_: str = "", base: datetime | None = None) -> str:
    """CLI/API 的两种时间写法 → due_at（iso() 同口径 UTC）。

    at: "2026-09-15 20:30" / "2026-09-15T20:30"（本地时区）/ 绝对 UTC iso
    in_: "90m" / "2h" / "3d" / "45s" / 组合 "1h30m"
    """
    now = base or datetime.now(timezone.utc)
    if in_:
        total = 0.0
        import re
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([smhd])", in_.lower()):
            mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
            total += float(m.group(1)) * mult
        if total <= 0:
            raise ValueError(f"无法解析 --in {in_!r}（如 90m / 2h / 3d / 1h30m）")
        return (now + _timedelta_s(total)).isoformat(timespec="seconds")
    if at:
        s = at.strip().replace("T", " ")
        dt = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            # 已是 ISO 带时区（如前端 toISOString 产物）直接收；Z 后缀是
            # 3.11 语法（本机 3.10 fromisoformat 不认）——归一成 +00:00，
            # 毫秒截到秒（调度精度不需要）
            import re
            iso = at.strip()
            if iso.endswith("Z"):
                iso = iso[:-1] + "+00:00"
            iso = re.sub(r"\.\d+", "", iso, count=1)
            try:
                dt = datetime.fromisoformat(iso)
            except ValueError as e:
                raise ValueError(f"无法解析 --at {at!r}（如 2026-09-15 20:30）") from e
        if dt.tzinfo is None:
            dt = dt.astimezone()          # 本地时区（CLI 用户口径）
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    raise ValueError("需要 --at 或 --in 之一")


def _timedelta_s(sec: float):
    from datetime import timedelta
    return timedelta(seconds=sec)


def _next_due(job, now: datetime) -> str | None:
    """触发后的下一个 due_at：cron 模式从 now 重算（停机欠的不追帧）；
    every 递归 now+every_s；单次 → None（置 done）。"""
    if job["cron"]:
        from . import cron as cron_mod
        return cron_mod.next_run_iso(job["cron"], after=now)
    if not job["every_s"]:
        return None
    return (now + _timedelta_s(job["every_s"])).isoformat(timespec="seconds")


class Scheduler:
    """engine 生命周期内常驻的扫描循环。进程内单例（SCHEDULER）。"""

    def __init__(self, engine) -> None:
        self.engine = engine
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            log.info("调度器启动（扫描周期 %.0fs）", CHECK_INTERVAL)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(CHECK_INTERVAL)
            try:
                fired = await self.tick()
                if fired:
                    log.info("调度扫描：%d 个 job 触发", fired)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("调度扫描异常")

    async def tick(self) -> int:
        """扫一轮 due job 并逐个触发。返回触发数（测试直接调它省 20s 周期）。"""
        jobs = []
        with db_mod.conn() as c:
            jobs = db_mod.due_jobs(c, iso())
        # 三轮修：顺带过期审批清扫（原纯惰性过期——会话结束后无人触发，
        # 生产 23 行 pending 全部超 TTL 僵尸；一条 UPDATE 量级，20s 一轮
        # 无感）。失败静默（sweep 内部兜底）
        try:
            from .security.approve import sweep_expired
            swept = sweep_expired()
            if swept:
                log.info("审批过期清扫：%d 行 pending → expired", swept)
        except Exception:                              # noqa: BLE001
            pass
        n = 0
        for job in jobs:
            try:
                if await self.fire(job):
                    n += 1
            except Exception:
                log.exception("job %s 触发异常", job["id"])
        return n

    async def fire(self, job) -> bool:
        # 二轮修#1：Row/dict 双态统一在**入口**（此前 to_dict 插在 KILL_ALL
        # 检查之后——:155 的 job.get("id") 对 Row 抛 AttributeError 被外层
        # pass 吞掉，熔断反转 fail-open；接线级对赌抓回）
        if not isinstance(job, dict):
            job = db_mod.to_dict(job)
        # W6.4 全局熔断标记：KILL_ALL 存在时调度器不投递
        try:
            from .config import PATHS as _P
            if (_P["run"] / "KILL_ALL").exists():
                log.warning("KILL_ALL 生效中，调度投递暂停（job %s）", job["id"])
                return False
        except (OSError, ImportError):
            pass
        # 三轮修：先 claim 再投递——把 due_at 预推一档缓冲（cron 重算/
        # every+间隔；单次推到远未来）。副作用（建会话/submit/steer）成功
        # 后 _settle 正常记账覆盖；**半途失败**（submit 抛/settle 写失败）
        # 时 due_at 已离开过去——原形态下 job 每 20s 被 due_jobs 重新选中，
        # new_session 类每轮造一个孤儿会话+整套 workspace，IO 降级持续
        # 多久堆多久（故障注入侦查实证）。claim 自身的失败原样上抛（tick
        # 记日志，下轮再试——不投递不放大）。
        now = datetime.now(timezone.utc)
        try:
            claim_due = _next_due(job, now) or (now + _timedelta_s(86400)
                                                ).isoformat(timespec="seconds")
            with db_mod.conn() as c:
                db_mod.update_job(c, job["id"], due_at=claim_due)
            job = {**job, "due_at": claim_due}
        except Exception:                              # noqa: BLE001 — claim 失败不投
            log.exception("job %s claim 失败（本轮跳过）", job["id"])
            return False
        # P11：内置 job 走专属三防路径（sqlite3.Row 无 .get——keys 判列）
        if "is_system" in job.keys() and job["is_system"]:
            return await self.fire_heartbeat(job)
        if (job["kind"] or "message") == "new_session":
            return await self._fire_new_session(job, now)
        return await self._fire_message(job, now)

    def _wrap(self, job, now: datetime) -> str:
        label = job["label"] or "定时任务"
        return _WRAP.format(label=label, prompt=job["prompt"],
                            now=now.strftime("%Y-%m-%d %H:%M UTC"),
                            note=f"，第 {job['fires'] + 1}/{job['max_fires']} 次触发"
                                 if job["max_fires"] > 1 else "")

    async def _fire_message(self, job, now: datetime) -> bool:
        """投递到现有会话。撞上运行中的 loadn turn → 插话实时注入
        （steer_if_running；比赛开赛不能排队等），否则 submit 排队。"""
        sid = job["session_id"]
        with db_mod.conn() as c:
            sess = db_mod.get_session(c, sid)
            if sess is None:
                db_mod.update_job(c, job["id"], status="done")
                log.warning("job %s 会话 %s 已不存在，置 done", job["id"], sid)
                return False
            if sess["status"] == "archived":
                db_mod.update_job(c, job["id"], status="paused")
                log.info("job %s 会话 %s 已归档，自动 paused", job["id"], sid)
                return False

        prompt = self._wrap(job, now)
        steered = self.engine.steer_if_running(sid, prompt) is not None
        if not steered:
            await self.engine.submit(sid, prompt, mode="background")
        return await self._settle(job, now, sid, steered=steered)

    async def _fire_new_session(self, job, now: datetime) -> bool:
        """到点新建会话并投递（定时启新任务）。递归 job 每次 fire 建全新
        会话不回填 sid（每天一个干净上下文）；无归档/存在闸（无 sid 可查），
        max_fires 防跑飞照常生效。"""
        from . import profile as profile_mod
        from . import workspace as ws_mod
        prof_name = job["profile"] or "auto"
        prof = (profile_mod.auto_match(job["prompt"] or job["title"] or "定时任务")
                if prof_name == "auto" else profile_mod.get(prof_name))
        title = (job["title"] or job["label"] or "定时任务")[:80]
        sid, _ = ws_mod.create_session(title, prof, None, None)
        if job["engine"]:
            with db_mod.conn() as c:
                db_mod.update_session(c, sid, engine_override=job["engine"])
        await self.engine.submit(sid, self._wrap(job, now), mode="background")
        log.info("job %s 新建会话 %s（%s%s）", job["id"], sid, prof.name,
                 f"/{job['engine']}" if job["engine"] else "")
        # 三轮修：回填 session_id——heartbeat 的空转判定（上一轮会话有无
        # 产出）靠它；不回填则 new_session 恒无据可查（三防②③的原死因
        # 之二）。普通 new_session job 回填同样合理（列表可点进最新实例）
        with db_mod.conn() as c:
            db_mod.update_job(c, job["id"], session_id=sid)
        return await self._settle(job, now, sid, new_sid=sid)

    async def _settle(self, job, now: datetime, sid: str | None, *,
                      steered: bool = False, new_sid: str | None = None) -> bool:
        """投递后的记账：fires 递增、due_at 推进/终态、SSE、notify。"""
        fires = job["fires"] + 1
        next_due = _next_due(job, now)
        with db_mod.conn() as c:
            if next_due is None or fires >= job["max_fires"]:
                db_mod.update_job(c, job["id"], fires=fires, status="done",
                                  last_fired_at=iso())
            else:
                db_mod.update_job(c, job["id"], fires=fires, due_at=next_due,
                                  last_fired_at=iso())
        label = job["label"] or "定时任务"
        evt_sid = new_sid or sid
        if evt_sid:
            self.engine.publish(evt_sid, "job_fired",
                                {"job_id": job["id"], "label": label,
                                 "fires": fires, "steered": steered,
                                 "new_session": bool(new_sid)})
        try:    # 运维通知：调度唤醒用户应被告知（配置可关）
            from .integrations import notify
            how = ("插话注入运行中任务" if steered
                   else "新建会话投递" if new_sid else "投递排队")
            target = new_sid or sid
            notify.fire(f"⏰ 定时唤醒 · {label}",
                        f"已{how} → {target}（第 {fires} 次）",
                        event="on_scheduled")
        except Exception:
            log.exception("调度通知异常")
        await self._deliver_destination(job, new_sid or sid, label)
        log.info("job %s「%s」触发（%d/%d，%s）→ %s", job["id"], label, fires,
                 job["max_fires"], "插话" if steered else "排队", new_sid or sid)
        return True

    async def _deliver_destination(self, job, sid: str | None, label: str) -> None:
        """P11 destination：dashboard（默认，现状零改）/ notify（推送提醒）/
        notify+artifact（推送 + 巡检产物写会话 artifacts/ 并附路径）。
        推送在投递时发「已触发+去向」；产物落盘=给 agent 的下一轮指示由
        _wrap 附言承担（结果产物由 turn 内产出，此处只保证目录与提醒）。"""
        dest = job["destination"] or "dashboard"
        if dest == "dashboard" or not sid:
            return
        from . import workspace as ws_mod
        from .integrations import notify
        extra = ""
        if "artifact" in dest:
            try:
                ws = ws_mod.ws_of(sid)
                art_dir = ws / "artifacts"
                art_dir.mkdir(parents=True, exist_ok=True)
                extra = f"（产物目录 {art_dir}，turn 内落盘自动入产物面板）"
            except Exception:                             # noqa: BLE001
                pass
        if "notify" in dest:
            notify.fire(f"🔄 例行触发 · {label}",
                        f"会话 {sid} 已投递{extra}", event="on_scheduled")

    # ------------------------------------------------------------ P11 heartbeat
    HEARTBEAT_EMPTY_SKIP = "heartbeat-empty"      # 空/忙跳过（不计数）
    HEARTBEAT_LOW_FREQ_MARK = "🫀·low"

    def heartbeat_busy(self) -> bool:
        """三防①：主队列忙（有 running/queued turn）跳过本轮。"""
        with db_mod.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM turns WHERE status IN "
                "('running','queued')").fetchone()
        return bool(row and row["n"] > 0)

    async def fire_heartbeat(self, job) -> bool:
        """heartbeat 专用触发：三防（忙跳过 / 空输出不投递不落账 /
        连续 3 轮无产出自动降频）。返回是否实际投递。"""
        from .routines import heartbeat_prompt
        if self.heartbeat_busy():
            log.info("heartbeat：主队列忙，跳过本轮")
            # 二轮修#2：忙跳过不计数（×N 语义=「连续无产出」，忙≠无产出；
            # 原实现在 3 次忙跳过后永久停投——_hb_advance(fired=True) 是
            # 死代码，实投从不清零）
            self._hb_advance(job, fired=False, count=False)
            return False
        # 三轮修：空转计数接线（原 _heartbeat_last_empty 返回值被丢弃且
        # _hb_advance 无 count=True 调用点——×N 恒 0，三防②③全是死代码，
        # 测试靠手工改 label 才绿）。语义：本轮 fire 时查**上一轮** heartbeat
        # 会话产出——空 → ×N+1 且本轮不投（三防②省钱）；连续 3 次 → 降频
        # 2h（三防③）；低频档空转**照投**（2h 探针自愈，否则 ×3 后永久
        # 停投）；实投有产出 → 清 ×N 并恢复高频档。
        low = self.HEARTBEAT_LOW_FREQ_MARK in (job["label"] or "")
        last_empty = self._heartbeat_last_empty(job)
        empties = 0
        if "×" in (job["label"] or ""):
            try:
                empties = int(job["label"].rsplit("×", 1)[-1].strip())
            except ValueError:
                empties = 0
        if last_empty and not low:
            self._hb_advance(job, fired=False, count=True)   # ×N+1（推 due）
            empties += 1
            if empties >= 3:                   # 三防③：连续 3 轮无产出
                with db_mod.conn() as c:
                    db_mod.update_job(
                        c, job["id"],
                        label=f"{self.HEARTBEAT_LOW_FREQ_MARK}{job['label']}",
                        cron="5 */2 * * *")     # 30min → 2h 降频
                from .security.audit import audit
                audit("policy_change", {"action": "heartbeat_lowfreq",
                                        "job": job["id"]})
                log.warning("heartbeat：连续 3 轮无产出，降频为 2h（面板可查）")
            return False                       # 三防②：空转轮不投递
        # 投递（new_session 复用现有路径，prompt 用巡检模板）
        now = datetime.now(timezone.utc)
        fired = await self._fire_new_session(
            {**job, "prompt": heartbeat_prompt(),
             "profile": job["profile"] or "assistant",
             "label": job["label"] or "🫀 心跳巡检"}, now)
        if fired:
            with db_mod.conn() as c:
                updates = {}
                if "×" in (job["label"] or ""):
                    # 二轮修#2：实投成功清零连续无产出计数（×N=真·连续）
                    updates["label"] = job["label"].split(" ×")[0]
                if low:
                    # 三轮修：低频档实投（上一轮有产出才走到这——空转分支
                    # 已 return）→ 恢复高频 30min 档
                    base = (updates.get("label") or job["label"]).replace(
                        self.HEARTBEAT_LOW_FREQ_MARK, "")
                    updates["label"] = base
                    updates["cron"] = "7,37 * * * *"
                    from .security.audit import audit
                    audit("policy_change", {"action": "heartbeat_highfreq",
                                            "job": job["id"]})
                if updates:
                    db_mod.update_job(c, job["id"], **updates)
        return fired

    def _heartbeat_last_empty(self, job) -> bool:
        """上一轮 heartbeat 会话最后 turn 是否无文本（无产出形态）。"""
        if not job.get("last_fired_at") or not job.get("session_id"):
            # new_session 递归不回填 sid——空转计数经 label 记录（fires 只计
            # 实投；连续空轮数编码在 label 尾标 ×N，见 _hb_advance）
            return "×" in (job["label"] or "")
        with db_mod.conn() as c:
            row = c.execute(
                "SELECT t.id, (SELECT COUNT(*) FROM messages m WHERE "
                "m.turn_id=t.id AND m.role='assistant' AND "
                "length(m.content)>20) AS has_text FROM turns t "
                "WHERE t.session_id=? ORDER BY t.id DESC LIMIT 1",
                (job["session_id"],)).fetchone()
        return bool(row) and not row["has_text"]

    def _hb_advance(self, job, *, fired: bool, count: bool = True) -> None:
        """推进 due_at；连续空轮计数编在 label 尾标（×1/×2/×3）。

        count=False（忙跳过/降频跳过）只推 due_at 不动 label——被忙挡住
        的那轮没有产出机会，不该计数。label 解析带 try（用户改出非数字
        尾标不炸——否则 20s 热循环）。"""
        now = datetime.now(timezone.utc)
        next_due = _next_due(job, now)
        if not count:
            with db_mod.conn() as c:
                if next_due is not None:
                    db_mod.update_job(c, job["id"], due_at=next_due)
            return
        label = job["label"] or "🫀 心跳巡检"
        try:
            n = int(label.rsplit("×", 1)[-1].strip()) if "×" in label else 0
        except ValueError:
            n = 0                                     # 非数字尾标从头计
        label = f"{label.split(' ×')[0]} ×{n + 1}"
        with db_mod.conn() as c:
            updates = {"label": label}
            if next_due is not None:
                updates["due_at"] = next_due
            db_mod.update_job(c, job["id"], **updates)


def ensure_heartbeat(engine) -> None:
    """P11：内置 heartbeat schedule（幂等创建）。

    二轮修#13：显式删除 = 永久关闭（kv 哨兵 heartbeat_disabled——原「删
    即关」重启就复活）；去重键=恒定形态（is_system=1 AND kind=new_session）
    而非 label LIKE（用户曾可改 label）。"""
    from .cron import next_run_iso
    try:
        with db_mod.conn() as _c:
            _off = _c.execute(
                "SELECT value FROM kv WHERE key='heartbeat_disabled'").fetchone()
        if _off and _off["value"] == "1":
            return
    except Exception:                                  # noqa: BLE001 — kv 缺表
        pass
    with db_mod.conn() as c:
        row = c.execute(
            "SELECT id FROM scheduled_jobs WHERE is_system=1 "
            "AND kind='new_session'").fetchone()
        if row is not None:
            return
        now = datetime.now(timezone.utc)
        db_mod.create_job(
            c, kind="new_session", label="🫀 心跳巡检",
            prompt=__import__("loadn_webui.routines", fromlist=["heartbeat_prompt"]).heartbeat_prompt(),
            # 二轮修#7：7/30 在本仓 cron 解析=单值 7（每小时一次）——
            # 30min 档须显式双点
            cron="7,37 * * * *", due_at=next_run_iso("7,37 * * * *", now),
            profile="assistant", title="🫀 心跳巡检", is_system=1,
            destination="notify+artifact", max_fires=100000)



def next_wake(sid: str) -> dict | None:
    """会话最近的 active job（API/前端「下次自动唤醒」用）。"""
    with db_mod.conn() as c:
        jobs = db_mod.list_jobs(c, sid, active_only=True)
    if not jobs:
        return None
    j = jobs[0]
    return {"id": j["id"], "label": j["label"], "due_at": j["due_at"],
            "every_s": j["every_s"], "cron": j["cron"],
            "fires": j["fires"], "max_fires": j["max_fires"]}


def get_scheduler(engine) -> Scheduler:
    """进程内单例（engine 全局唯一；测试可直接构造独立 Scheduler）。"""
    global SCHEDULER
    if SCHEDULER is None:
        SCHEDULER = Scheduler(engine)
    return SCHEDULER


SCHEDULER: Scheduler | None = None
