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
        n = 0
        for job in jobs:
            try:
                if await self.fire(job):
                    n += 1
            except Exception:
                log.exception("job %s 触发异常", job["id"])
        return n

    async def fire(self, job) -> bool:
        # W6.4 全局熔断标记：KILL_ALL 存在时调度器不投递
        try:
            from .config import PATHS as _P
            if (_P["run"] / "KILL_ALL").exists():
                log.warning("KILL_ALL 生效中，调度投递暂停（job %s）", job.get("id"))
                return False
        except Exception:                              # noqa: BLE001
            pass
        """触发单个 job：按 kind 分流 + 记账（fires/due_at/终态）。返回是否投递成功。"""
        now = datetime.now(timezone.utc)
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
        log.info("job %s「%s」触发（%d/%d，%s）→ %s", job["id"], label, fires,
                 job["max_fires"], "插话" if steered else "排队", new_sid or sid)
        return True


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
