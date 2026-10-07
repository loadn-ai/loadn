"""P11 模板库：例程模板列表（Ready/Needs setup）+ 一键安装（复制为用户
schedule，与平台升级解耦）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import routines as routines_mod

router = APIRouter(prefix="/api")


@router.get("/routines")
def list_routines():
    out = []
    for t in routines_mod.ROUTINES:
        st = routines_mod.template_status(t)
        out.append({**t, **st})
    return {"routines": out}


@router.post("/routines/{key}/install")
def install_routine(key: str, body: dict = None):
    """安装=复制为用户 schedule（is_system=0；模板改版不影响已装实例）。
    body 可覆盖 cron/prompt/destination。"""
    t = next((x for x in routines_mod.ROUTINES if x["key"] == key), None)
    if t is None:
        raise HTTPException(404, f"模板不存在: {key}")
    from datetime import datetime, timezone

    from ...cron import next_run_iso
    body = body or {}
    cron = str(body.get("cron") or t["cron"])
    try:
        due = next_run_iso(cron, datetime.now(timezone.utc))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    with db_mod.conn() as c:
        # 三轮修：幂等——同模板已装未终态（active/paused）→ 返回既有行
        # （原无条件 create：前端双击/网关重发=每天双份晨报双会话双推送）
        row = c.execute(
            "SELECT * FROM scheduled_jobs WHERE label=? AND is_system=0 "
            "AND status IN ('active','paused') ORDER BY id LIMIT 1",
            (t["name"],)).fetchone()
        if row is not None:
            return {"ok": True, "job": db_mod.to_dict(row), "existing": True}
        jid = db_mod.create_job(
            c, kind="new_session", label=t["name"],
            prompt=str(body.get("prompt") or t["prompt"]),
            cron=cron, due_at=due,
            profile=str(body.get("profile") or "auto"),
            title=t["name"],
            destination=str(body.get("destination") or "notify"),
            is_system=0, max_fires=100000)
        job = db_mod.to_dict(db_mod.get_job(c, jid))
    return {"ok": True, "job": job}
