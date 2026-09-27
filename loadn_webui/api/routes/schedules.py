"""定时调度：cron 任务的 CRUD 与挂载。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import profile as profile_mod
from ...util import iso

router = APIRouter(prefix="/api")

from ._common import _job_fields


@router.get("/schedules")
def list_schedules(sid: str = ""):
    """全局定时任务列表（每行带 session_title/cron_desc，前端直显）。"""
    from ...cron import describe
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_jobs(c, sid or None)]
        titles = {r["id"]: r["title"] for r in c.execute("SELECT id, title FROM sessions")}
    for row in rows:
        row["session_title"] = titles.get(row.get("session_id"))
        row["cron_desc"] = describe(row["cron"]) if row.get("cron") else None
    return {"schedules": rows}
@router.post("/schedules")
def create_schedule_global(body: dict):
    """全局建 job：body 带 kind（message 需 session_id；new_session 到点新建）。"""
    fields = _job_fields(body, None)
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, **fields)
        job = db_mod.to_dict(db_mod.get_job(c, jid))
    return {"ok": True, "job": job}
@router.patch("/schedules/{jid}")
def patch_schedule(jid: int, body: dict):
    """改 label/prompt/max_fires/status/触发时刻（cron|at|in|every_s）与
    new_session 的 title/profile/engine。触发字段改动会重算 due_at（cron
    真源）；done 是终态不复活——要重跑就删了重建。"""
    from datetime import datetime, timezone

    from ...cron import next_run_iso, parse_cron
    from ...scheduler import parse_when
    with db_mod.conn() as c:
        job = db_mod.get_job(c, jid)
        if job is None:
            raise HTTPException(404, f"schedule 不存在: {jid}")
        updates: dict = {}
        if "label" in body:
            updates["label"] = str(body["label"] or "").strip()[:80] or None
        if "prompt" in body:
            prompt = str(body["prompt"] or "").strip()
            if not prompt:
                raise HTTPException(400, "prompt 不能为空")
            updates["prompt"] = prompt
        if "max_fires" in body:
            mf = int(body["max_fires"])
            if not 1 <= mf <= 100000:
                raise HTTPException(400, "max_fires 需在 1-100000")
            updates["max_fires"] = mf
        if (job["kind"] or "message") == "new_session":
            if "title" in body:
                updates["title"] = str(body["title"] or "").strip()[:80] or None
            if "profile" in body:
                prof = str(body["profile"] or "").strip()
                if prof and prof != "auto":
                    if prof not in profile_mod.load_registry():
                        raise HTTPException(400, f"未知 profile：{prof}")
                updates["profile"] = prof or None
            if "engine" in body:
                eng = str(body["engine"] or "").strip()
                if eng:
                    from ... import engines as engines_mod
                    if eng not in engines_mod.ENGINES:
                        raise HTTPException(400, f"未知引擎：{eng}")
                updates["engine"] = eng or None
        # 触发改期矩阵：给 cron → 清 every；给 at/in → 清 cron；单 every_s 只换间隔
        now = datetime.now(timezone.utc)
        touch_trigger = ("cron" in body) or ("at" in body) or ("in" in body)
        if touch_trigger and job["status"] == "done":
            raise HTTPException(400, "job 已完成（done），改触发需删除后重建")
        cron = str(body.get("cron") or "").strip()
        at, in_ = str(body.get("at") or "").strip(), str(body.get("in") or "").strip()
        if cron:
            try:
                parse_cron(cron)
            except ValueError as e:
                raise HTTPException(400, str(e))
            updates.update(cron=cron, every_s=None, due_at=next_run_iso(cron, now))
        elif at or in_:
            try:
                updates.update(cron=None, due_at=parse_when(at=at, in_=in_, base=now))
            except ValueError as e:
                raise HTTPException(400, str(e))
        if "every_s" in body:
            every_s = body["every_s"]
            if every_s is not None:
                every_s = int(every_s)
                if not 60 <= every_s <= 86400 * 30:
                    raise HTTPException(400, "every_s 需在 60s-30天（递归间隔）")
            updates["every_s"] = every_s
        status = body.get("status")
        if status is not None:
            if status not in ("active", "paused"):
                raise HTTPException(400, "status 只能是 active|paused")
            if job["status"] == "done":
                raise HTTPException(400, "job 已完成（done），不能改状态——要重跑就删除后重建")
            if status == "active" and job["status"] != "active":
                # 恢复的 cron job：due_at 已过期 → 从现在重算（跳过错过的时刻，
                # 与停机 misfire 补投一次区分开）；at/in 类不动（保留补投语义）
                cron_val = updates.get("cron", job["cron"])
                due = updates.get("due_at", job["due_at"])
                if cron_val and due and due <= iso():
                    updates["due_at"] = next_run_iso(cron_val, now)
            updates["status"] = status
        db_mod.update_job(c, jid, **updates)
        job = db_mod.to_dict(db_mod.get_job(c, jid))
    return {"ok": True, "job": job}
@router.delete("/schedules/{jid}")
def delete_schedule(jid: int):
    with db_mod.conn() as c:
        if not db_mod.delete_job(c, jid):
            raise HTTPException(404, f"schedule 不存在: {jid}")
    return {"ok": True}
