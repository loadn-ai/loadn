"""项目与侧栏自定义分区（projects → 子任务，共享工作区）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import profile as profile_mod
from ... import workspace as ws_mod
from ...engine import ENGINE

router = APIRouter(prefix="/api")

from ._common import _apply_partition_mutex


@router.post("/projects")
def create_project(body: dict):
    """建项目：scaffold 全套（宪法/settings/skills 属项目）+ DB 行。
    category_id 可选：直接落进侧栏自定义分类。"""
    title = (body.get("title") or "").strip()[:80]
    if not title:
        raise HTTPException(400, "title 不能为空")
    category_id = body.get("category_id")
    if category_id is not None:
        with db_mod.conn() as c:
            if not isinstance(category_id, int) or db_mod.get_category(c, category_id) is None:
                raise HTTPException(404, f"category 不存在: {category_id}")
    prof_name = body.get("profile") or "auto"
    prof = (profile_mod.get(prof_name) if prof_name != "auto"
            else profile_mod.auto_match(title))
    pid, ws = ws_mod.create_project(title, prof, body.get("skills"), body.get("mcp") or None)
    if category_id is not None:
        with db_mod.conn() as c:
            db_mod.update_project(c, pid, touch=False, category_id=category_id)
    with db_mod.conn() as c:
        proj = db_mod.to_dict(db_mod.get_project(c, pid))
    return {"project": proj}
@router.get("/projects")
def list_projects():
    """项目列表 + 每项目子任务计数（一条 GROUP BY）。"""
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_projects(c, include_archived=True)]
        counts = db_mod.project_session_counts(c)
    for r in rows:
        r["n_sessions"] = counts.get(r["id"], {}).get("active", 0)
    return {"projects": rows}
@router.patch("/projects/{pid}")
def patch_project(pid: str, body: dict):
    """项目改名（重渲染项目宪法）+ 侧栏分区标记（置顶/收藏/分类，任务同款语义）。"""
    with db_mod.conn() as c:
        proj = db_mod.get_project(c, pid)
        if proj is None:
            raise HTTPException(404, f"project 不存在: {pid}")
    updates: dict = {}
    if "title" in body:
        title = (body.get("title") or "").strip()[:80]
        if not title:
            raise HTTPException(400, "title 不能为空")
        updates["title"] = title
    for k in ("starred", "pinned"):
        if k in body:
            updates[k] = 1 if bool(body[k]) else 0
    if "category_id" in body:
        cid = body.get("category_id")
        if cid is not None:
            with db_mod.conn() as c:
                if not isinstance(cid, int) or db_mod.get_category(c, cid) is None:
                    raise HTTPException(404, f"category 不存在: {cid}")
        updates["category_id"] = cid
    _apply_partition_mutex(body, updates)
    pure_partition = {"starred", "pinned", "category_id"}
    with db_mod.conn() as c:
        db_mod.update_project(c, pid, touch=not set(updates) <= pure_partition, **updates)
        proj = db_mod.get_project(c, pid)
    if "title" in updates:
        ws_mod.render_project_constitution(pid, updates["title"],
                                           profile_mod.get(proj["profile"]),
                                           json.loads(proj["skills_json"] or "[]"))
    with db_mod.conn() as c:
        return {"ok": True, "project": db_mod.to_dict(db_mod.get_project(c, pid))}
@router.delete("/projects/{pid}")
def delete_project(pid: str, purge: bool = False):
    """归档项目 = 项目 + 全部子任务连坐 archived（恢复反向连坐）；
    purge=true = 物理删除（任一子任务有 active turn → 409）。"""
    with db_mod.conn() as c:
        proj = db_mod.get_project(c, pid)
        if proj is None:
            raise HTTPException(404, f"project 不存在: {pid}")
        kids = [r["id"] for r in c.execute(
            "SELECT id FROM sessions WHERE project_id=?", (pid,)).fetchall()]
        if not purge:
            db_mod.update_project(c, pid, status="archived")
            for kid in kids:
                db_mod.update_session(c, kid, status="archived")
            return {"ok": True, "archived": pid, "sessions": kids}
        for sid in kids:
            for t in ENGINE.active.values():
                if t.session_id == sid:
                    raise HTTPException(409, f"子任务 {sid} 有 turn 在跑，先停止")
        for sid in kids:
            db_mod.delete_session(c, sid)
        db_mod.delete_project(c, pid)
    import shutil
    shutil.rmtree(ws_mod.ws_of(pid), ignore_errors=True)
    return {"ok": True, "deleted": pid, "sessions": kids}
@router.patch("/projects/{pid}/restore")
def restore_project(pid: str):
    with db_mod.conn() as c:
        if db_mod.get_project(c, pid) is None:
            raise HTTPException(404, f"project 不存在: {pid}")
        db_mod.update_project(c, pid, status="active")
        c.execute("UPDATE sessions SET status='active' WHERE project_id=?", (pid,))
    return {"ok": True}
@router.get("/categories")
def list_categories():
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_categories(c)]
    return {"categories": rows}
@router.post("/categories")
def create_category(body: dict):
    name = (body.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "name 不能为空")
    with db_mod.conn() as c:
        if db_mod.category_name_taken(c, name):
            raise HTTPException(409, f"分类已存在：{name}")
        cid = db_mod.create_category(c, name)
        row = db_mod.get_category(c, cid)
    return {"category": db_mod.to_dict(row)}
@router.patch("/categories/{cid}")
def rename_category(cid: int, body: dict):
    name = (body.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "name 不能为空")
    with db_mod.conn() as c:
        if db_mod.get_category(c, cid) is None:
            raise HTTPException(404, f"category 不存在: {cid}")
        if db_mod.category_name_taken(c, name):
            raise HTTPException(409, f"分类已存在：{name}")
        db_mod.rename_category(c, cid, name)
        row = db_mod.get_category(c, cid)
    return {"category": db_mod.to_dict(row)}
@router.delete("/categories/{cid}")
def delete_category(cid: int):
    """删分类：成员回「最近」（category_id 置空），任务/项目本身不动。"""
    with db_mod.conn() as c:
        if db_mod.get_category(c, cid) is None:
            raise HTTPException(404, f"category 不存在: {cid}")
        db_mod.delete_category(c, cid)
    return {"ok": True}
