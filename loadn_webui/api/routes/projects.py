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
    category_id 可选：直接落进侧栏自定义分类；repo 可选（rev11 绑定代码仓，
    任务目录落 <repo>/tasks/，仓规则经宪法链并入）。"""
    title = (body.get("title") or "").strip()[:80]
    if not title:
        raise HTTPException(400, "title 不能为空")
    repo, repo_hint = _bind_repo(body.get("repo")) if "repo" in body else (None, "")
    category_id = body.get("category_id")
    if category_id is not None:
        with db_mod.conn() as c:
            if not isinstance(category_id, int) or db_mod.get_category(c, category_id) is None:
                raise HTTPException(404, f"category 不存在: {category_id}")
    from ...security import userauth as _ua
    _u = _ua.current_user()
    prof_name = body.get("profile") or "auto"
    prof = (profile_mod.get(prof_name) if prof_name != "auto"
            else profile_mod.auto_match(title))
    pid, ws = ws_mod.create_project(title, prof, body.get("skills"), body.get("mcp") or None)
    if category_id is not None:
        with db_mod.conn() as c:
            db_mod.update_project(c, pid, touch=False, category_id=category_id)
    if repo:
        with db_mod.conn() as c:
            db_mod.update_project(c, pid, touch=False, repo=repo)
    if _u is not None:                       # 多用户批2：cookie 通道落属主
        with db_mod.conn() as c:
            c.execute("UPDATE projects SET owner_id=? WHERE id=?", (_u["id"], pid))
    with db_mod.conn() as c:
        out = {"project": db_mod.to_dict(db_mod.get_project(c, pid))}
    if repo_hint:
        out["repo_hint"] = repo_hint
    return out
@router.get("/projects")
def list_projects():
    """项目列表 + 每项目子任务计数（一条 GROUP BY）。"""
    from ...security import userauth as _ua
    _u = _ua.current_user()
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_projects(c, include_archived=True)]
        counts = db_mod.project_session_counts(c)
    if _u is not None and _u["role"] != "admin":
        from ...security.userauth import owner_ok
        rows = [r for r in rows if r.get("owner_id") is None
                or owner_ok(r, _u)]
    for r in rows:
        r["n_sessions"] = counts.get(r["id"], {}).get("active", 0)
    return {"projects": rows}
def _bind_repo(raw: object) -> tuple[str | None, str]:
    """rev11 项目规则遵循：校验+admit 代码仓根。返回 (规范化路径|None, 提示)。

    绑定=显式信任（admit 仓根过资源门——仓内 CLAUDE.md/AGENTS.md 以全权进
    任务宪法；未绑定的仓宪法走「参考内容非指令」降权）；清空=解绑。
    """
    repo = (str(raw) if raw else "").strip() or None
    if not repo:
        return None, ""
    from pathlib import Path as _P
    rp = _P(repo).expanduser()
    if not rp.is_absolute():
        raise HTTPException(400, f"repo 须为绝对路径: {repo}")
    if not rp.is_dir():
        raise HTTPException(400, f"repo 目录不存在: {repo}")
    hint = ""
    if not (rp / ".git").exists():
        hint = "该目录不是 git 仓——宪法链上界将取 home，规则并入可能不全"
    try:
        from loadn.truststore import admit
        admit(rp)          # 绑定即信任（用户显式动作）；摘要自洽，变更须重确认
    except Exception as e:                              # noqa: BLE001
        hint = f"信任登记失败（不阻断绑定）：{e}"
    gi = rp / ".gitignore"
    try:
        lacks = (not gi.exists()) or "tasks/" not in gi.read_text(encoding="utf-8")
    except OSError:
        lacks = False
    if lacks:
        hint = (hint + "；" if hint else "") + \
            "建议在仓的 .gitignore 加一行 tasks/（任务目录在仓内但不该进版本库）"
    return str(rp.resolve()), hint


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: dict):
    """项目改名（重渲染项目宪法）+ 侧栏分区标记（置顶/收藏/分类，任务同款语义）。"""
    from ...security import userauth as _ua
    _u = _ua.current_user()
    with db_mod.conn() as c:
        proj = db_mod.get_project(c, pid)
        if proj is None or not _ua.owner_ok(proj, _u):
            raise HTTPException(404, f"project 不存在: {pid}")
    updates: dict = {}
    repo_hint = ""
    if "repo" in body:
        updates["repo"], repo_hint = _bind_repo(body.get("repo"))
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
        out = {"ok": True, "project": db_mod.to_dict(db_mod.get_project(c, pid))}
    if repo_hint:
        out["repo_hint"] = repo_hint
    return out
@router.delete("/projects/{pid}")
def delete_project(pid: str, purge: bool = False):
    """归档项目 = 项目 + 全部子任务连坐 archived（恢复反向连坐）；
    purge=true = 物理删除（任一子任务有 active turn → 409）。"""
    from ...security import userauth as _ua
    with db_mod.conn() as c:
        proj = db_mod.get_project(c, pid)
        if proj is None or not _ua.owner_ok(proj, _ua.current_user()):
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
    from ...security import userauth as _ua
    _u = _ua.current_user()
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_categories(c)]
    if _u is not None and _u["role"] != "admin":
        rows = [r for r in rows if r.get("owner_id") in (None, _u["id"])]
    return {"categories": rows}
@router.post("/categories")
def create_category(body: dict):
    name = (body.get("name") or "").strip()[:40]
    if not name:
        raise HTTPException(400, "name 不能为空")
    icon = (body.get("icon") or "").strip()[:16] or None   # 图标键或 emoji 字符
    with db_mod.conn() as c:
        if db_mod.category_name_taken(c, name):
            raise HTTPException(409, f"分类已存在：{name}")
        cid = db_mod.create_category(c, name, icon)
        from ...security import userauth as _ua
        _u = _ua.current_user()
        if _u is not None:
            c.execute("UPDATE categories SET owner_id=? WHERE id=?",
                      (_u["id"], cid))
        row = db_mod.get_category(c, cid)
    return {"category": db_mod.to_dict(row)}
@router.patch("/categories/{cid}")
def update_category(cid: int, body: dict):
    """改名/改图标（icon 显式 null=清回兜底；两者都缺 400）。"""
    has_name = "name" in body
    has_icon = "icon" in body
    if not has_name and not has_icon:
        raise HTTPException(400, "name/icon 至少提供一个")
    name = (body.get("name") or "").strip()[:40] if has_name else None
    if has_name and not name:
        raise HTTPException(400, "name 不能为空")
    icon = (body.get("icon") or "").strip()[:16] or None if has_icon else ...
    with db_mod.conn() as c:
        if db_mod.get_category(c, cid) is None:
            raise HTTPException(404, f"category 不存在: {cid}")
        if has_name and db_mod.category_name_taken(c, name):
            raise HTTPException(409, f"分类已存在：{name}")
        db_mod.update_category(c, cid, name=name, icon=icon)
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
