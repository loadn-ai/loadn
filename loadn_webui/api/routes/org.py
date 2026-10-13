"""数字员工（DW）：组织架构（部门/员工 CRUD、初始化）+ 临时工池（召集/退役）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import org as org_mod

router = APIRouter(prefix="/api")


def _err(e: Exception) -> HTTPException:
    msg = str(e)
    code = 404 if "不存在" in msg else (409 if "已存在" in msg else 400)
    return HTTPException(code, msg)


@router.get("/org")
def get_org():
    """全架构树：部门（嵌员工）+ 池概况 {idle/busy/retired 计数}。"""
    with db_mod.conn() as c:
        return org_mod.list_org(c)


@router.post("/org/init")
def init_org():
    """一键初始化默认组织架构（8 部门 18 人；已有数据 409）。"""
    from ...security.audit import audit
    with db_mod.conn() as c:
        try:
            n = org_mod.init_org(c)
        except ValueError as e:
            raise _err(e) from None
        audit("org", {"action": "org_init", "employees": n})
        return {"ok": True, "employees": n}


@router.post("/org/departments")
def create_department(body: dict):
    from ...security.audit import audit
    with db_mod.conn() as c:
        try:
            d = org_mod.upsert_department(c, body)
        except ValueError as e:
            raise _err(e) from None
        audit("org", {"action": "dept_create", "name": d["name"]})
        return d


@router.patch("/org/departments/{dept_id}")
def update_department(dept_id: int, body: dict):
    with db_mod.conn() as c:
        try:
            return org_mod.upsert_department(c, body, dept_id)
        except ValueError as e:
            raise _err(e) from None


@router.delete("/org/departments/{dept_id}")
def delete_department(dept_id: int):
    from ...security.audit import audit
    with db_mod.conn() as c:
        try:
            org_mod.delete_department(c, dept_id)
        except ValueError as e:
            raise _err(e) from None
        audit("org", {"action": "dept_delete", "id": dept_id})
        return {"ok": True}


@router.post("/org/employees")
def create_employee(body: dict):
    from ...security.audit import audit
    with db_mod.conn() as c:
        try:
            e = org_mod.upsert_employee(c, body)
        except ValueError as ex:
            raise _err(ex) from None
        audit("org", {"action": "emp_create", "name": e["name"]})
        return e


@router.patch("/org/employees/{emp_id}")
def update_employee(emp_id: int, body: dict):
    """编辑员工（persona/tools/model/status 均走此面；停用=status=disabled）。"""
    from ...security.audit import audit
    with db_mod.conn() as c:
        try:
            e = org_mod.upsert_employee(c, body, emp_id)
        except ValueError as ex:
            raise _err(ex) from None
        audit("org", {"action": "emp_update", "id": emp_id,
                      "fields": sorted(body)})
        return e


@router.delete("/org/employees/{emp_id}")
def delete_employee(emp_id: int):
    from ...security.audit import audit
    with db_mod.conn() as c:
        try:
            org_mod.delete_employee(c, emp_id)
        except ValueError as e:
            raise _err(e) from None
        audit("org", {"action": "emp_delete", "id": emp_id})
        return {"ok": True}


@router.post("/org/recruit")
async def recruit(body: dict):
    """召集 n 个临时工（要求 brief；LLM 生成失败自动本地兜底）→ 入池。"""
    from ...security.audit import audit
    try:
        n = int(body.get("n") or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "n 须为整数") from None
    brief = str(body.get("brief") or "")
    workers = await org_mod.recruit(n, brief)
    audit("org", {"action": "recruit", "n": len(workers), "brief": brief[:80]})
    return {"workers": workers}


@router.get("/org/pool")
def list_pool(state: str = ""):
    """临时工池（state 过滤 idle/busy/retired；缺省全部，retired 殿后）。"""
    q = ("SELECT * FROM temp_pool "
         + ("WHERE state=? " if state else "")
         + "ORDER BY CASE state WHEN 'retired' THEN 1 ELSE 0 END, id DESC")
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in
                c.execute(q, (state,) if state else ())]
        return {"pool": rows}


@router.get("/org/tasks")
def agent_tasks(name: str = "", limit: int = 50):
    """按人名查任务记录（DW-6）：正式员工 subagent_type=名（点名派发）、
    临时工 agent_name=名（出勤取名）双归属；倒序，含产物/文件计数。"""
    if not (name or "").strip():
        raise HTTPException(400, "name 必填（员工名或临时工名）")
    with db_mod.conn() as c:
        return org_mod.list_tasks_by_name(c, name.strip(), limit)


@router.delete("/org/pool/{tid}")
def retire_temp(tid: int):
    """退役临时工（软删除 state=retired，不再被取用）。"""
    with db_mod.conn() as c:
        try:
            org_mod.retire_temp(c, tid)
        except ValueError as e:
            raise _err(e) from None
        return {"ok": True}
