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
import json
from datetime import datetime, timezone

from . import db as db_mod
from .util import get_logger, iso

log = get_logger(__name__)

CHECK_INTERVAL = 20.0          # 扫描周期：分钟级唤醒场景足够，误差可忽略

# ---- GC（Goal Contract）目标契约：对账式唤醒 + 反问闭环 ----
# 背景（生产实证 2026-10 虚拟币对冲/worldquant 两例）：几百轮历史叙事压倒
# 1KB 唤醒词，模型活在「历史即指令」里——盯盘叙事每 15 分钟自我强化一次，
# 目标沉底、路线偏执。修复思路：唤醒不再是「重复同一句话+读盘续作」,
# 而是每次唤醒先注入结构化目标对账（目标/成功判据/已证伪路线，真源=
# job.goal 或会话 state.json），并给 agent 一个平台级反问出口：
AWAIT_MARK = "【等待用户裁决】"   # 回复行首出现 → 暂停本会话定时唤醒，用户回话即恢复


def read_goal_state(sid: str) -> dict:
    """读会话工作区 state.json 的目标契约三字段（宪法 §2.8 约定 agent 维护）：
    goal（目标一句话）/ success_criteria（可判定成功判据）/ falsified_routes
    （已证伪路线列表，此后不许重走）。无文件/坏 JSON/无字段 → {}。"""
    if not sid:
        return {}
    try:
        from . import workspace as ws_mod
        p = ws_mod.ws_of(sid) / "state.json"
        if not p.exists():
            return {}
        data = json.loads(p.read_text("utf-8"))
        if not isinstance(data, dict):
            return {}
        out: dict = {}
        for k in ("goal", "success_criteria"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
        fr = data.get("falsified_routes")
        if isinstance(fr, list) and fr:
            out["falsified_routes"] = [str(x)[:200] for x in fr if str(x).strip()][:8]
        return out
    except Exception:                                # noqa: BLE001 — 契约读取永不炸
        return {}


def goal_ctx_for(job: dict) -> dict:
    """唤醒时的目标对账上下文：job.goal（定时任务定义，最稳定）优先，
    回落目标会话 state.json（message 类 job 的会话 agent 可自维护）。"""
    goal = str(job.get("goal") or "").strip()
    if goal:
        return {"goal": goal[:2000], "source": "定时任务定义"}
    if (job.get("kind") or "message") == "message" and job.get("session_id"):
        return read_goal_state(job["session_id"])
    return {}


def _await_hit(content: str) -> bool:
    """行首严格匹配 AWAIT_MARK（防正文引用/讨论标记文本时误触发）。"""
    return any(ln.strip().startswith(AWAIT_MARK)
               for ln in (content or "").splitlines())


def check_await_gate(sid: str, content: str) -> int:
    """turn 收尾检测【等待用户裁决】（engine._finish 调）：命中 → 暂停该
    会话全部 active 的非系统 job（kv awaitjobs:{sid} 记账 job id 列表——
    用户手动暂停的不在账上不会被误恢复）+ 审计 + 推送通知。返回暂停数。

    这是「反问」的平台化牙齿：无头环境里 agent 的反问若只是聊天气泡，
    15 分钟后下一次唤醒又把它拽回循环——现在反问自动挂起调度，直到
    用户答复（resume_await_jobs）。"""
    if not _await_hit(content):
        return 0
    with db_mod.conn() as c:
        jobs = [j for j in db_mod.list_jobs(c, sid, active_only=True)
                if not j["is_system"]]
        jids = [j["id"] for j in jobs]
        if not jids:
            return 0
        for jid in jids:
            db_mod.update_job(c, jid, status="paused")
        db_mod.kv_set(c, f"awaitjobs:{sid}", json.dumps(jids))
    try:
        from .security.audit import audit
        audit("policy_change", {"action": "await_user_gate",
                                "sid": sid, "paused_jobs": jids}, sid=sid)
    except Exception:                                    # noqa: BLE001
        pass
    try:
        from .integrations import notify
        notify.fire("⏸️ 任务暂停等待你的裁决",
                    f"agent 已提出需要用户拍板的问题（会话 {sid}），"
                    f"已暂停 {len(jids)} 个定时唤醒；去会话答复后自动恢复。",
                    event="on_scheduled")
    except Exception:                                    # noqa: BLE001
        log.exception("await 通知异常")
    log.info("await 门命中：%d 个 job 暂停（sid=%s）", len(jids), sid)
    return len(jids)


def resume_await_jobs(sid: str) -> int:
    """用户回了话（post_message 调）：恢复因「等待裁决」暂停的 job——只动
    kv 记账的那批，用户手动暂停的不动；过期 cron 重算 due_at。返回恢复数。"""
    with db_mod.conn() as c:
        raw = db_mod.kv_get(c, f"awaitjobs:{sid}")
        if not raw:
            return 0
        db_mod.kv_del(c, f"awaitjobs:{sid}")
        try:
            jids = [int(x) for x in json.loads(raw)]
        except (TypeError, ValueError, json.JSONDecodeError):
            return 0
        now = datetime.now(timezone.utc)
        n = 0
        for jid in jids:
            job = db_mod.get_job(c, jid)
            if job is None or job["status"] != "paused" or job["is_system"]:
                continue
            updates: dict = {"status": "active"}
            if job["cron"]:
                from .cron import next_run_iso
                if (job["due_at"] or "") <= iso():
                    updates["due_at"] = next_run_iso(job["cron"], now)
            db_mod.update_job(c, jid, **updates)
            n += 1
    if n:
        try:
            from .security.audit import audit
            audit("policy_change", {"action": "await_user_resumed",
                                    "sid": sid, "resumed_jobs": n}, sid=sid)
        except Exception:                                # noqa: BLE001
            pass
        log.info("用户已答复，恢复 %d 个 await 暂停的 job（sid=%s）", n, sid)
    return n


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
        # 三轮修（backlog 清）：广播事件行清理（'*' 行不挂 turn，prune 的
        # 会话路径永远够不着——生产 2.5 万行无界增长）
        try:
            from .config import CONFIG
            cutoff = (datetime.now(timezone.utc)
                      - _timedelta_s(86400 * (CONFIG.run.events_retain_days
                                              + 1))).isoformat()
            with db_mod.conn() as c:
                gone = db_mod.prune_broadcast(c, cutoff)
            if gone:
                log.info("广播事件清理：%d 行", gone)
        except Exception:                              # noqa: BLE001
            pass
        # PERF-3：每日低峰维护（冷归档+WAL checkpoint+周度 VACUUM）——
        # 窗口判定+kv 游标全在 maybe_daily 内，窗口外一次 kv 读即返回
        try:
            from .maintenance import maybe_daily
            with db_mod.conn() as c:
                if maybe_daily(c):
                    log.info("每日维护已执行（详见 audit system 事件）")
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
            # 六轮修：claim 用 compare-and-set——due_jobs 的 SELECT 与本
            # UPDATE 之间无锁（服务端调度循环与测试/手动 tick 并发扫同一
            # job 时双 fire：fires 计数翻倍+双投递）。占位失败=他方已 claim
            with db_mod.conn() as c:
                cur = c.execute(
                    "UPDATE scheduled_jobs SET due_at=? WHERE id=? AND due_at=?",
                    (claim_due, job["id"], job["due_at"]))
                if cur.rowcount == 0:
                    return False            # 他方 tick 已占位
            job = {**job, "due_at": claim_due}
        except Exception:                              # noqa: BLE001 — claim 失败不投
            log.exception("job %s claim 失败（本轮跳过）", job["id"])
            return False
        # P11：内置 job 走专属三防路径（sqlite3.Row 无 .get——keys 刭列）
        if "is_system" in job.keys() and job["is_system"]:
            return await self.fire_heartbeat(job)
        try:
            if (job["kind"] or "message") == "new_session":
                return await self._fire_new_session(job, now)
            return await self._fire_message(job, now)
        except Exception:
            # 六轮修 B3：单次 job（无 cron/every）claim 已把 due_at 推到
            # +24h——失败若无痕=「明天才补且面板无异常」。置 paused 让
            # 失败可见（用户可手动恢复）；cron/every 类按 claim 节奏自愈
            log.exception("job %s 投递失败", job["id"])
            if not job["cron"] and not job["every_s"]:
                try:
                    with db_mod.conn() as c:
                        db_mod.update_job(c, job["id"], status="paused")
                    from .security.audit import audit
                    audit("policy_change", {
                        "action": "one_shot_job_failed_paused",
                        "job": job["id"]})
                except Exception:                          # noqa: BLE001
                    pass
            return False

    def _wrap(self, job, now: datetime) -> str:
        """对账式唤醒包装（GC-2）：目标契约置顶 → 唤醒词 → 偏执自检（高频
        轮）→ 尾注（过程≠目标 + 反问出口 + 确认纪律）。is_system（心跳巡检）
        无战略目标语义，跳过目标块。"""
        fires = job["fires"] + 1
        parts = [f"【定时唤醒 {fires}/{job['max_fires']}】{job['label'] or '定时任务'}"]
        if not job.get("is_system"):
            gc = goal_ctx_for(job)
            if gc.get("goal"):
                lines = ["【目标对账——先于一切事务动作】", f"目标：{gc['goal']}"]
                if gc.get("success_criteria"):
                    lines.append(f"成功判据：{gc['success_criteria']}")
                if gc.get("falsified_routes"):
                    lines.append("已证伪路线（禁止重走）：")
                    lines += [f"- {x}" for x in gc["falsified_routes"]]
                lines.append("→ 本轮第一段先输出对账：① 目标现状（拿数字说话）"
                             "② 上一轮动作对目标的真实贡献 ③ 本轮做什么才能产生"
                             "增量。对账拿不出具体事实（空话/复读）= 本轮失败。")
                parts.append("\n".join(lines))
            else:
                parts.append(
                    "【目标未记录】本任务还没有目标契约。若这不是纯巡检/通知类"
                    "任务，本轮第一件事：提炼「目标一句话 + 可判定的成功判据 +"
                    "用户已确认的约束」写入 state.json（goal/success_criteria/"
                    "falsified_routes 字段，见宪法 §2.8），并在回复中向用户复述"
                    "请他确认；纯巡检类回复本轮结果即可。")
        parts.append(f"【唤醒词】\n{job['prompt']}")
        if (fires >= 5 and (job["cron"] or job["every_s"])
                and not job.get("is_system")):
            parts.append(
                f"【偏执自检】这已是第 {fires} 次唤醒：若前几轮都在做同类动作"
                "而目标指标没有实质变化，这本身就是路线失效的证据。本轮二选一："
                "① 换一条与已做动作本质不同的路线并迈出第一步；② 用"
                f"{AWAIT_MARK}标记另起一行列出需要用户拍板的问题与选项，结束"
                "本轮等用户答复。禁止再一次重复同质动作。")
        note = f"，第 {fires}/{job['max_fires']} 次触发" \
            if job["max_fires"] > 1 else ""
        parts.append(
            f"（本消息由平台调度器于 {now.strftime('%Y-%m-%d %H:%M UTC')} 自动投递{note}。"
            "PROGRESS.md / state.json 是过程记录，不是目标本身——续作先对账，"
            "过程与目标脱节时以目标为准重新规划，已证伪的路线不再重走。若你"
            "判断任务目标本身有问题（不可达/自相矛盾/风险失控），在回复末尾"
            f"另起一行以{AWAIT_MARK}开头列出问题与选项——平台会自动暂停本会话"
            "的定时唤醒并通知用户，用户答复后自动恢复。对外发送/支付/删除等"
            "操作的确认纪律照常生效——内容或对象不明确就停下来把问题写进回复。）")
        return "\n\n".join(parts)

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
