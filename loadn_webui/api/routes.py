"""REST API 面（/api 前缀）。

认证（承袭 papergo webapp 语义）：默认 127.0.0.1 免认证；token 非空时
强制 Bearer / X-Workdaddy-Token / ?token=（query 专为 EventSource 无法设头保留）。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import artifacts as art
from .. import db as db_mod
from ..config import CONFIG
from .. import mcp_admin, settings_admin, skill_zh, skillhub
from .. import profile as profile_mod
from .. import skills as skills_mod
from .. import workspace as ws_mod
from ..config import PATHS
from ..engine import ENGINE
from ..util import iso

router = APIRouter(prefix="/api")


# 管理面异常 → HTTP 状态码（FileExistsError 冲突 409；RuntimeError 网络 502）
_ERR_MAP = ((FileNotFoundError, 404), (KeyError, 404), (FileExistsError, 409),
            (ValueError, 400), (PermissionError, 400), (RuntimeError, 502))


def _http_err(e: Exception) -> HTTPException:
    for ty, code in _ERR_MAP:
        if isinstance(e, ty):
            return HTTPException(code, str(e))
    return HTTPException(500, str(e))


# 侧栏分区三标记位（任务/项目通用）——「移动到」的互斥语义
_PARTITION_KEYS = ("pinned", "starred", "category_id")


def _apply_partition_mutex(body: dict, updates: dict) -> None:
    """分区互斥：本次落位某个桶时，未显式给出的另两位清零（置顶/收藏/分类
    三桶独占，归档走 status 不在此列）。纯标记请求不 touch updated_at。"""
    if any(k in body for k in _PARTITION_KEYS):
        for k in _PARTITION_KEYS:
            if k not in updates:
                updates[k] = None if k == "category_id" else 0


# ---------------------------------------------------------------- profiles / skills
@router.get("/profiles")
def list_profiles():
    reg = profile_mod.load_registry()
    return {"profiles": [{"name": p.name, "description": p.description, "skills": p.skills,
                          "effort": p.effort, "timeout_s": p.timeout_s} for p in reg.values()]}


@router.get("/skills")
def list_skills():
    return {"skills": skills_mod.available()}


@router.post("/skills/translate")
async def translate_skills(body: dict):
    """skill 卡片中文简介：批量 [{name, description}] → [{name, zh}]。
    无汉字的描述才送译（kv 缓存）；未配模型/失败 zh=null，前端回退原文。"""
    items = body.get("items")
    if not isinstance(items, list):
        raise HTTPException(400, "items 须为列表")
    return {"items": await skill_zh.translate_batch(items)}


# ---------------------------------------------------------------- 管理面：skills CRUD / 编辑
@router.get("/skills/{name}")
def skill_detail(name: str):
    d = skills_mod.get(name)
    if d is None:
        raise HTTPException(404, f"skill 不存在: {name}")
    return d


@router.post("/skills")
def create_skill(body: dict):
    try:
        return skills_mod.create(str(body.get("name") or ""), str(body.get("description") or ""))
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.post("/skills/{name}/toggle")
def toggle_skill(name: str, body: dict):
    """启用/禁用 skill：禁用后不进新会话、active 会话下一 turn 失效。"""
    try:
        return skills_mod.set_disabled(name, bool(body.get("disabled")))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.delete("/skills/{name}")
def delete_skill(name: str, force: bool = False):
    try:
        if not force:
            used = skills_mod.sessions_using(name)
            if used:
                raise HTTPException(409, f"被 {len(used)} 个会话挂载"
                                             f"（force=true 强删，会话内失效）: {', '.join(used[:5])}")
        return skills_mod.delete(name)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.get("/skills/{name}/file")
def get_skill_file(name: str, path: str):
    try:
        return {"path": path, "content": skills_mod.read_file(name, path)}
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.put("/skills/{name}/file")
def put_skill_file(name: str, body: dict):
    try:
        return skills_mod.write_file(name, str(body.get("path") or ""),
                                     str(body.get("content") or ""), create=False)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.post("/skills/{name}/file")
def post_skill_file(name: str, body: dict):
    try:
        return skills_mod.write_file(name, str(body.get("path") or ""),
                                     str(body.get("content") or ""), create=True)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.delete("/skills/{name}/file")
def delete_skill_file(name: str, path: str):
    try:
        return skills_mod.delete_file(name, path)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


# ---------------------------------------------------------------- 管理面：安装 / 市场
@router.post("/skills/install")
def install_skill(body: dict):
    try:
        repo = str(body.get("repo") or "")
        subpath = str(body.get("subpath") or "")
        ref = body.get("ref") or None
        if body.get("repo_url"):
            parsed = skills_mod.parse_repo_url(str(body["repo_url"]))
            repo = parsed["repo"]
            subpath = parsed["subpath"]
            ref = parsed["ref"] or ref
        return skills_mod.install_from_github(
            repo, subpath, ref, overwrite=bool(body.get("overwrite")))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.post("/skills/upload")
async def upload_skill_zip(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 100 * 1024 * 1024:
        raise HTTPException(400, "zip 超过 100MB")
    try:
        return skills_mod.install_from_zip(data, overwrite=False)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.get("/skillhub/search")
def skillhub_search(q: str = ""):
    return skillhub.search(q)


@router.get("/skillhub/catalog")
def skillhub_catalog():
    return skillhub.catalog()


# ---------------------------------------------------------------- 管理面：tools（MCP + 内建开关）
@router.get("/tools")
def get_tools():
    out = mcp_admin.list_servers()
    out.update(mcp_admin.tools_overview())
    return out


@router.put("/tools/mcp/{name}")
def put_mcp_server(name: str, body: dict):
    try:
        return mcp_admin.put_server(name, body.get("spec") or body or {})
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.delete("/tools/mcp/{name}")
def delete_mcp_server(name: str):
    try:
        return mcp_admin.delete_server(name)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.put("/tools/profile/{profile}")
def put_profile_tools(profile: str, body: dict):
    try:
        return mcp_admin.put_profile_tools(profile, body.get("disallowed_tools") or [])
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


# ---------------------------------------------------------------- 管理面：平台设置
@router.get("/settings")
def get_settings():
    return settings_admin.get_settings()


@router.put("/settings/titlegen")
def put_titlegen(body: dict):
    try:
        return settings_admin.put_titlegen(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.put("/settings/run")
def put_run(body: dict):
    try:
        return settings_admin.put_run(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.put("/settings/convergence")
def put_convergence(body: dict):
    try:
        return settings_admin.put_convergence(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.post("/settings/titlegen/test")
async def test_titlegen():
    try:
        return await settings_admin.test_titlegen()
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.put("/settings/resources")
def put_resources(body: dict):
    try:
        return settings_admin.put_resources(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.put("/settings/notify")
def put_notify(body: dict):
    try:
        return settings_admin.put_notify(body)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


@router.post("/settings/resources/test")
async def test_resources(only: str = ""):
    try:
        names = [x for x in only.split(",") if x] or None
        return await settings_admin.test_resources(names)
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)


# ---------------------------------------------------------------- sessions
# ---------------------------------------------------------------- projects（项目 → 子任务，共享工作区）
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


# ---------------------------------------------------------------- categories（侧栏自定义分区）
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


@router.post("/sessions")
async def create_session(body: dict):
    title = (body.get("title") or body.get("first_message") or "新任务").strip()[:80]
    prof_name = body.get("profile") or "auto"
    text_probe = f"{title} {body.get('first_message') or ''}"
    prof = (profile_mod.get(prof_name) if prof_name != "auto"
            else profile_mod.auto_match(text_probe))
    skills = body.get("skills")
    if skills is None:
        skills = list(prof.skills)
    mcp = body.get("mcp") or None
    project_id = (body.get("project_id") or "").strip() or None
    category_id = body.get("category_id")   # 分类内直接新建：落位即分类
    if category_id is not None:
        with db_mod.conn() as c:
            if not isinstance(category_id, int) or db_mod.get_category(c, category_id) is None:
                raise HTTPException(404, f"category 不存在: {category_id}")
    if project_id:
        with db_mod.conn() as c:
            proj = db_mod.get_project(c, project_id)
        if proj is None or proj["status"] != "active":
            raise HTTPException(404, f"project 不存在或已归档: {project_id}")
        # 子任务的 skills/mcp 缺省取项目存的（create_session 内处理）
        if body.get("skills") is None:
            skills = None
        if body.get("mcp") is None:
            mcp = None
    sid, ws = ws_mod.create_session(title, prof, skills, mcp, project_id=project_id)
    if category_id is not None:
        with db_mod.conn() as c:
            db_mod.update_session(c, sid, touch=False, category_id=category_id)
    with db_mod.conn() as c:
        # 未显式命名 → 允许自动标题（成功生成或手动改名后清标记）
        if not (body.get("title") or "").strip():
            db_mod.kv_set(c, f"title_auto:{sid}", "1")
        # 零选择创建（auto + 无首条消息）→ 首条真实消息时重新匹配角色
        if prof_name == "auto" and not (body.get("first_message") or "").strip():
            db_mod.kv_set(c, f"profile_auto:{sid}", "1")
    if body.get("first_message"):
        await ENGINE.submit(sid, body["first_message"])
    with db_mod.conn() as c:
        sess = db_mod.to_dict(db_mod.get_session(c, sid))
    return {"session": sess}


@router.get("/sessions")
def list_sessions():
    from ..scheduler import next_wake
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_sessions(c)]
        for r in rows:
            act = db_mod.active_turns(c, r["id"])
            r["active_turn"] = db_mod.to_dict(act[-1]) if act else None
            r["usage"] = db_mod.usage_totals(c, r["id"])
            # 聊天框模式下拉读列表（5s 轮询），这里带上自动角色标记
            r["profile_auto"] = bool(db_mod.kv_get(c, f"profile_auto:{r['id']}"))
            r["next_wake"] = next_wake(r["id"])
    return {"sessions": rows}


def _get_session_or_404(sid: str) -> dict:
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        if sess is None:
            raise HTTPException(404, f"session not found: {sid}")
        d = db_mod.to_dict(sess)
        d["usage"] = db_mod.usage_totals(c, sid)
        return d


# 历史工具块折叠（2026-09-15 证书会话 16.8MB 详情响应的教训）：自动化会话单条
# assistant 消息可挂上千个工具块（browser_evaluate 连发），全量下发拖垮传输/
# 解析/渲染。读时折叠：块数超限只留尾部，超长 input/result/text 截断；DB 原样
# 保留完整流水，SSE 实时流不受影响。
_FOLD_KEEP = 50          # 每条消息最多保留的块数（留尾部=最近的过程）
_FOLD_STR = 400          # 单块内字符串截断（input 脚本/result 输出全文太长）


def _trunc_obj(v, limit: int):
    if isinstance(v, str):
        return v if len(v) <= limit else v[:limit] + "…"
    if isinstance(v, dict):
        return {k: _trunc_obj(x, limit) for k, x in v.items()}
    if isinstance(v, list):
        return [_trunc_obj(x, limit) for x in v[:20]] + (["…"] if len(v) > 20 else [])
    return v


def _fold_blocks(bj: str | None, keep: int = _FOLD_KEEP) -> str | None:
    if not bj:
        return bj
    try:
        blocks = json.loads(bj)
    except (TypeError, json.JSONDecodeError):
        return bj
    # 正文段（text）全保留——消息主体，此前也以整段 content 全量下发（同量级）；
    # thinking/tool 过程块只留尾部 keep 条（16.8MB 详情响应的教训）
    proc_idx = [i for i, b in enumerate(blocks)
                if not (isinstance(b, dict) and b.get("type") == "text")]
    if len(proc_idx) > keep:
        dropped = len(proc_idx) - keep
        keep_set = set(proc_idx[-keep:])
        out: list = []
        marked = False
        for i, b in enumerate(blocks):
            is_text = isinstance(b, dict) and b.get("type") == "text"
            if i not in keep_set and not is_text:
                if not marked:
                    out.append({"type": "tool", "name": "⋯",
                                "brief": f"（已折叠前 {dropped} 条过程细节，正文完整）"})
                    marked = True
                continue
            out.append(b)
        blocks = out
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") == "text":
            continue   # 正文段不做 400 字截断
        for k in ("input", "result", "text"):
            if k in b:
                b[k] = _trunc_obj(b[k], _FOLD_STR)
    return json.dumps(blocks, ensure_ascii=False)


@router.get("/sessions/{sid}")
def session_detail(sid: str):
    sess = _get_session_or_404(sid)
    with db_mod.conn() as c:
        sess["messages"] = [db_mod.to_dict(r) for r in db_mod.list_messages(c, sid)]
        for m in sess["messages"]:
            if m.get("role") == "assistant":          # 附件块在 user 消息里，不动
                m["blocks_json"] = _fold_blocks(m.get("blocks_json"))
        sess["turns"] = [db_mod.to_dict(r) for r in c.execute(
            "SELECT * FROM turns WHERE session_id=? ORDER BY id", (sid,)).fetchall()]
        sess["schedules"] = [db_mod.to_dict(r) for r in db_mod.list_jobs(c, sid)]
    sess["artifacts"] = art.list_artifacts(sid)
    sess["skills_available"] = [s["name"] for s in skills_mod.available()]
    from ..scheduler import next_wake
    sess["next_wake"] = next_wake(sid)
    with db_mod.conn() as c:
        sess["profile_auto"] = bool(db_mod.kv_get(c, f"profile_auto:{sid}"))
    return sess


@router.get("/sessions/{sid}/live")
def session_live(sid: str):
    """进行中 turn 的实时快照（刷新/重进会话后前端据此重建 live：停止按钮+过程流水）。"""
    _get_session_or_404(sid)
    with db_mod.conn() as c:
        act = db_mod.active_turns(c, sid)
    if not act:
        return {"live": None}
    # 优先 running：串行队列里有排队的后续消息时（act[-1] 是最新 queued），
    # live 应报告真正在跑的 turn，否则前端重进会话会把流水挂到排队 turn 上
    t = next((r for r in act if r["status"] == "running"), act[-1])
    at = ENGINE.active.get(t["id"])
    items: list[dict] = []
    if at:
        # blocks 已是穿插时间线（thinking/tool/text 按真实顺序）——直接映射；
        # at.texts 不再尾部追加（与 text 块同源，会重复）
        for b in at.blocks:
            if b.get("type") == "thinking":
                items.append({"kind": "thinking", "text": b.get("text", "")})
            elif b.get("type") == "text":
                items.append({"kind": "text", "text": b.get("text", "")})
            elif b.get("type") == "tool":
                items.append({"kind": "tool", **{k: b.get(k) for k in
                            ("id", "name", "brief", "input", "result", "is_error")}})
    # 截尾：深度研究一小时能积累数百节点（实测 627 条/860KB），全量下发会把
    # 重进会话的首帧渲染压垮；前端 live 只渲染尾部窗口，完整过程结束落库可回看
    items = items[-200:]
    return {"live": {"turnId": t["id"], "status": t["status"], "items": items,
                     "todos": list(at.todos) if at else [],
                     "startedAt": t["started_at"]}}


@router.patch("/sessions/{sid}")
async def patch_session(sid: str, body: dict):
    sess = _get_session_or_404(sid)
    if "project_id" in body:
        # 移动 = 只改 DB 不搬文件 → artifacts/shares 相对路径悬空指向不存在的
        # 目录。v1 禁止；要归组就在目标项目下新建子任务。
        raise HTTPException(400, "暂不支持移动会话到项目（工作区不迁移）；"
                                 "请在项目下新建子任务")
    in_project = bool(sess.get("project_id"))
    updates = {}
    if "title" in body:
        updates["title"] = str(body["title"])[:80]
    if "status" in body:
        if body["status"] not in ("active", "archived"):
            raise HTTPException(400, "status 只能是 active|archived")
        updates["status"] = body["status"]
    if "starred" in body:
        # 纯标记位：不扰动 updated_at（否则点星会把会话顶到列表最上）
        updates["starred"] = 1 if bool(body["starred"]) else 0
    if "pinned" in body:
        updates["pinned"] = 1 if bool(body["pinned"]) else 0
    if "category_id" in body:
        # 移动到自定义分类（null = 回「最近」）；纯分区操作同样不 touch
        cid = body.get("category_id")
        if cid is not None:
            with db_mod.conn() as c:
                if not isinstance(cid, int) or db_mod.get_category(c, cid) is None:
                    raise HTTPException(404, f"category 不存在: {cid}")
        updates["category_id"] = cid
    _apply_partition_mutex(body, updates)
    if "profile" in body:
        p = str(body["profile"])
        if p == "auto":
            # 聊天框选「✨ 自动」：保持当前角色，下一条消息重新匹配
            with db_mod.conn() as c:
                db_mod.kv_set(c, f"profile_auto:{sid}", "1")
        else:
            prof = profile_mod.get(p)
            with db_mod.conn() as c:
                db_mod.kv_del(c, f"profile_auto:{sid}")
            updates["profile"] = prof.name
            ws_mod.rerender(sid, prof_name=prof.name)
    skills = body.get("skills")
    if skills is not None:
        updates["skills_json"] = json.dumps([str(s) for s in skills], ensure_ascii=False)
        if not in_project:
            ws_mod.rerender(sid, skills=[str(s) for s in skills])
    if "mcp" in body:
        updates["mcp_json"] = json.dumps(body.get("mcp") or {}, ensure_ascii=False)
        if not in_project:   # 共享 .mcp.json 属项目——子任务改写会换掉兄弟的 MCP
            ws_mod.write_mcp_json(ws_mod.ws_of(sid), body.get("mcp") or {})
    if "engine" in body:
        # 聊天框内核切换：会话级覆盖（下一 turn 生效；id 迁移由引擎的
        # _align_engine 在 turn 启动时处理）。null/空串 = 回到跟随配置。
        from .. import engines as engines_mod
        val = body.get("engine")
        if val in (None, "", False):
            updates["engine_override"] = None
        else:
            name = str(val)
            if name not in engines_mod.ENGINES:
                raise HTTPException(400, f"未知引擎：{name}"
                                    f"（可用：{sorted(engines_mod.ENGINES)}）")
            updates["engine_override"] = name
    if updates:
        # 纯分区标记（收藏/置顶/分类）不 touch：分区操作不该把会话顶到「最近」最上
        pure_partition = {"starred", "pinned", "category_id"}
        with db_mod.conn() as c:
            db_mod.update_session(c, sid, touch=not set(updates) <= pure_partition, **updates)
            if "title" in updates:            # 手动改名优先，停用自动标题
                db_mod.kv_del(c, f"title_auto:{sid}")
    return {"ok": True, "session": _get_session_or_404(sid)}


@router.delete("/sessions/{sid}")
def delete_session(sid: str, purge: bool = False):
    _get_session_or_404(sid)
    if not purge:
        with db_mod.conn() as c:
            db_mod.update_session(c, sid, status="archived")
        return {"ok": True, "archived": sid}
    for t in ENGINE.active.values():
        if t.session_id == sid:
            raise HTTPException(409, "会话有 turn 在跑，先停止")
    import shutil
    ws = ws_mod.ws_of(sid)
    with db_mod.conn() as c:
        # 共享工作区引用计数：兄弟 session（含归档）或项目行还指着该目录时
        # 只删 DB 行——目录属主未清零前 rmtree 会连坐别人的文件
        shared = db_mod.workspace_refcount(c, str(ws), exclude_sid=sid) > 0
        db_mod.delete_session(c, sid)
    if not shared:
        shutil.rmtree(ws, ignore_errors=True)
    return {"ok": True, "deleted": sid}


# ---------------------------------------------------------------- schedules（定时调度）
# ---------------------------------------------------------------- 数据流向（W5.2）
@router.get("/admin/egress")
def egress_recent(n: int = 50):
    """最近外联（面板数据源：audit egress_request 尾窗）。管理面。"""
    from .. import audit as audit_mod
    rows = audit_mod.tail(n, "egress_request")
    out = []
    for r in rows:
        d = json.loads(r["detail_json"])
        out.append({"ts": r["ts"], **d})
    return {"events": out, "mode": CONFIG.security.egress_mode,
            "allow": CONFIG.security.egress_allow}


# ---------------------------------------------------------------- 快照回滚（W6.2）
@router.get("/sessions/{sid}/snapshots")
def get_snapshots(sid: str):
    from .. import workspace as ws_mod
    from ..policy import list_snapshots
    _get_session_or_404(sid)
    return {"snapshots": list_snapshots(ws_mod.ws_of(sid))}


@router.post("/sessions/{sid}/rollback")
def post_rollback(sid: str, body: dict):
    from .. import workspace as ws_mod
    from ..policy import rollback
    _get_session_or_404(sid)
    return rollback(ws_mod.ws_of(sid), str(body.get("point") or ""))


# ---------------------------------------------------------------- 熔断（W6.4）
@router.post("/sessions/{sid}/kill")
def kill_session(sid: str):
    """会话级 kill：停活跃 turn + 熔断（新消息拒绝）。管理面（admin 头）。"""
    _get_session_or_404(sid)
    from .. import canary as canary_mod
    from .. import db as db_mod
    from ..engine import ENGINE
    stopped = []
    with db_mod.conn() as c:
        rows = c.execute("SELECT id FROM turns WHERE session_id=? AND"
                         " status IN ('running','queued')", (sid,)).fetchall()
    for r in rows:
        if ENGINE.stop_turn(r["id"]):
            stopped.append(r["id"])
    canary_mod.lock_session(sid, "kill switch（用户/自动触发）")
    return {"ok": True, "stopped_turns": stopped, "locked": True}


@router.post("/sessions/{sid}/unlock")
def unlock_session(sid: str):
    from .. import canary as canary_mod
    return {"ok": canary_mod.unlock_session(sid)}


@router.post("/admin/kill-all")
def kill_all():
    """全局熔断：停全部活跃 turn + 调度器暂停（KILL_ALL 标记）+ 拒绝新任务。"""
    from .. import canary as canary_mod
    from .. import db as db_mod
    from ..engine import ENGINE
    from ..config import PATHS
    stopped = 0
    with db_mod.conn() as c:
        rows = c.execute("SELECT id, session_id FROM turns WHERE"
                         " status IN ('running','queued')").fetchall()
    for r in rows:
        if ENGINE.stop_turn(r["id"]):
            stopped += 1
        canary_mod.lock_session(r["session_id"], "kill-all（全局熔断）")
    (PATHS["run"] / "KILL_ALL").write_text("kill-all")
    return {"ok": True, "stopped_turns": stopped,
            "scheduler_paused": True,
            "hint": "恢复：删除 var/run/KILL_ALL 并逐会话 unlock"}


# ---------------------------------------------------------------- 审批（W1-2）
@router.get("/sessions/{sid}/approvals")
def list_approvals(sid: str):
    _get_session_or_404(sid)
    from .. import approve as approve_mod
    return {"approvals": approve_mod.list_pending(sid)}


@router.post("/sessions/{sid}/approvals")
def create_approval(sid: str, body: dict):
    """agent CLI 发起（token 面）：{action_type, params, note} → {id, summary}。"""
    _get_session_or_404(sid)
    from .. import approve as approve_mod
    from ..engine import ENGINE
    try:
        out = approve_mod.create(sid, str(body.get("action_type") or ""),
                                 dict(body.get("params") or {}),
                                 note=str(body.get("note") or ""),
                                 turn_id=body.get("turn_id"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    ENGINE.publish(sid, "approval", {"kind": "request", **out})
    return out


@router.post("/approvals/{aid}/decide")
def decide_approval(aid: int, body: dict):
    """用户裁决（token 面）：{approve: bool}。批准响应携带一次性明文码。"""
    from .. import approve as approve_mod
    from ..engine import ENGINE
    try:
        out = approve_mod.decide(aid, bool(body.get("approve")))
    except LookupError as e:
        raise HTTPException(404, str(e))
    if out.get("ok"):
        st = approve_mod.status(aid)
        ENGINE.publish(st["sid"], "approval",
                       {"kind": "decided", "id": aid, "status": out["status"]})
    return out


@router.get("/approvals/{aid}")
def approval_status(aid: int):
    from .. import approve as approve_mod
    try:
        return approve_mod.status(aid)
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.post("/approvals/consume")
def consume_approval(body: dict):
    """CLI 执行前验证（token 面）：{sid, action_type, params, confirm_code}。"""
    from .. import approve as approve_mod
    return approve_mod.consume(
        str(body.get("sid") or ""), str(body.get("action_type") or ""),
        dict(body.get("params") or {}), str(body.get("confirm_code") or ""))


@router.get("/schedules")
def list_schedules(sid: str = ""):
    """全局定时任务列表（每行带 session_title/cron_desc，前端直显）。"""
    from ..cron import describe
    with db_mod.conn() as c:
        rows = [db_mod.to_dict(r) for r in db_mod.list_jobs(c, sid or None)]
        titles = {r["id"]: r["title"] for r in c.execute("SELECT id, title FROM sessions")}
    for row in rows:
        row["session_title"] = titles.get(row.get("session_id"))
        row["cron_desc"] = describe(row["cron"]) if row.get("cron") else None
    return {"schedules": rows}


def _job_fields(body: dict, sid: str | None) -> dict:
    """创建 job 的字段规范化。触发三选一：cron > at/in；kind 两种动作；
    every/max_fires 防跑飞钳制（递归默认上限 20）。"""
    from ..cron import next_run_iso, parse_cron
    from ..scheduler import parse_when
    kind = str(body.get("kind") or "").strip()
    if sid is not None:
        kind = kind or "message"      # 会话内端点恒 message
    elif not kind:
        if body.get("session_id"):
            kind = "message"          # 全局端点带 session_id = 显式指认目标会话
        else:
            # 两者都没有：静默默认成 new_session 会让人误建错任务
            raise HTTPException(400, "需要 kind（message 投递到现有会话 / "
                                     "new_session 到点新建）或 session_id")
    if kind not in ("message", "new_session"):
        raise HTTPException(400, "kind 只能是 message|new_session")
    fields: dict = {"kind": kind, "session_id": None}
    if kind == "message":
        target = sid if sid is not None else str(body.get("session_id") or "").strip()
        if not target:
            raise HTTPException(400, "message 任务需要 session_id（或走 /sessions/{sid}/schedules）")
        _get_session_or_404(target)
        fields["session_id"] = target
    else:
        # new_session：到点新建会话；title/profile/engine 可选（profile 校验
        # 用注册表白名单，engine 校验与聊天框内核切换同源）
        fields["title"] = str(body.get("title") or "").strip()[:80] or None
        prof = str(body.get("profile") or "").strip()
        if prof and prof != "auto":
            if prof not in profile_mod.load_registry():
                raise HTTPException(400, f"未知 profile：{prof}")
            fields["profile"] = prof
        eng = str(body.get("engine") or "").strip()
        if eng:
            from .. import engines as engines_mod
            if eng not in engines_mod.ENGINES:
                raise HTTPException(400, f"未知引擎：{eng}（可用：{sorted(engines_mod.ENGINES)}）")
            fields["engine"] = eng
    if "label" in body:
        fields["label"] = str(body["label"] or "").strip()[:80] or None
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt 不能为空")
    fields["prompt"] = prompt

    # 触发三选一：cron（挂钟对齐，如每天 20:00）> at/in（一次或 every 递归）
    cron = str(body.get("cron") or "").strip()
    at, in_ = str(body.get("at") or "").strip(), str(body.get("in") or "").strip()
    if cron:
        try:
            parse_cron(cron)
        except ValueError as e:
            raise HTTPException(400, str(e))
        fields["cron"] = cron
        fields["every_s"] = None
        fields["due_at"] = next_run_iso(cron)
        max_fires = int(body.get("max_fires") or 20)
    elif at or in_:
        fields["cron"] = None
        try:
            fields["due_at"] = parse_when(at=at, in_=in_)
        except ValueError as e:
            raise HTTPException(400, str(e))
        every_s = body.get("every_s")
        if every_s is not None:
            every_s = int(every_s)
            if not 60 <= every_s <= 86400 * 30:
                raise HTTPException(400, "every_s 需在 60s-30天（递归间隔）")
            fields["every_s"] = every_s
            # 递归 job 必须有触发上限（默认 20），防无人值守跑飞
            max_fires = int(body.get("max_fires") or 20)
        else:
            fields["every_s"] = None
            max_fires = int(body.get("max_fires") or 1)
    else:
        raise HTTPException(400, "需要 cron / at / in 之一"
                            "（如 cron='0 20 * * *' / at=2026-09-15 20:30 / in=90m）")
    if not 1 <= max_fires <= 100000:
        raise HTTPException(400, "max_fires 需在 1-100000")
    fields["max_fires"] = max_fires
    return fields


@router.post("/schedules")
def create_schedule_global(body: dict):
    """全局建 job：body 带 kind（message 需 session_id；new_session 到点新建）。"""
    fields = _job_fields(body, None)
    with db_mod.conn() as c:
        jid = db_mod.create_job(c, **fields)
        job = db_mod.to_dict(db_mod.get_job(c, jid))
    return {"ok": True, "job": job}


@router.post("/sessions/{sid}/schedules")
def create_schedule(sid: str, body: dict):
    """会话内建 job（恒 message 动作——到点向该会话投递/插话）。"""
    _get_session_or_404(sid)
    body = {**body, "kind": "message"}
    fields = _job_fields(body, sid)
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
    from ..cron import next_run_iso, parse_cron
    from ..scheduler import parse_when
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
                    from .. import engines as engines_mod
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


@router.post("/sessions/{sid}/promote")
def promote_session(sid: str):
    """把现有任务升级为项目容器：原任务成为首个子任务，workspace 零迁移。

    projects.id 复用原 sid（目录名=pid=宪法 SESSION_ID 全一致）；settings env
    从 SESSION_ID 改写 PROJECT_ID（子任务的 per-session 值由 engine 恒注入）。
    之后项目行可「+ 子任务」（共享同一工作区）。
    """
    import json as _json
    with db_mod.conn() as c:
        sess = db_mod.get_session(c, sid)
        if sess is None:
            raise HTTPException(404, f"session not found: {sid}")
        if sess["project_id"]:
            raise HTTPException(400, "已是项目子任务，无需升级")
        db_mod.create_project(
            c, id=sid, title=sess["title"], workspace=sess["workspace"],
            profile=sess["profile"], skills_json=sess["skills_json"] or "[]",
            mcp_json=sess["mcp_json"] or "{}")
        db_mod.update_session(c, sid, project_id=sid)
    from .. import profile as _prof
    ws_mod.render_project_constitution(
        sid, sess["title"], _prof.get(sess["profile"]),
        _json.loads(sess["skills_json"] or "[]"))
    return {"ok": True, "project_id": sid}


# ---------------------------------------------------------------- messages / turns
@router.post("/sessions/{sid}/messages")
async def post_message(sid: str, body: dict):
    _get_session_or_404(sid)
    text = (body.get("text") or "").strip()
    atts = _validate_attachments(body.get("attachments"))
    if not text and not atts:
        raise HTTPException(400, "text 不能为空")
    mode = body.get("mode") if body.get("mode") in ("foreground", "background") else "foreground"
    # 全自动角色：首条真实消息重新匹配（零选择创建时探针只是占位标题），
    # 命中新角色则连同 skills 一起换成该角色默认（用户从未显式选过）
    with db_mod.conn() as c:
        if db_mod.kv_get(c, f"profile_auto:{sid}"):
            db_mod.kv_del(c, f"profile_auto:{sid}")
            prof = profile_mod.auto_match(text or "（见附件）")
            if prof and prof.name != db_mod.get_session(c, sid)["profile"]:
                db_mod.update_session(c, sid, profile=prof.name,
                                      skills_json=json.dumps(list(prof.skills),
                                                             ensure_ascii=False))
                ws_mod.rerender(sid, prof_name=prof.name, skills=list(prof.skills))
    try:
        tid = await ENGINE.submit(sid, text or "（见附件）", mode, attachments=atts)
    except PermissionError as e:      # W6.4 熔断（kill switch/canary 命中）
        raise HTTPException(403, str(e))
    with db_mod.conn() as c:
        turn = db_mod.to_dict(db_mod.get_turn(c, tid))
    return {"turn": turn}


def _validate_attachments(raw) -> list[dict]:
    """附件列表规范化：必须是 inputs/ 内的相对路径，≤20 条，字段白名单。"""
    if not raw:
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise HTTPException(400, "attachments 需为列表（≤20 条）")
    out = []
    for a in raw:
        if not isinstance(a, dict):
            raise HTTPException(400, "attachments 条目需为对象")
        path = str(a.get("path") or "")
        if not path.startswith("inputs/") or ".." in path or path.startswith("/"):
            raise HTTPException(400, f"附件路径非法: {path}")
        out.append({
            "type": "attachment", "path": path,
            "name": str(a.get("name") or Path(path).name)[:200],
            "kb": a.get("kb") if isinstance(a.get("kb"), (int, float)) else None,
            "is_image": bool(a.get("is_image")),
        })
    return out


@router.post("/turns/{tid}/stop")
async def stop_turn(tid: int):
    ok = await ENGINE.stop_turn(tid)
    if not ok:
        raise HTTPException(404, "turn 不存在或已结束")
    return {"ok": True}


@router.post("/sessions/{sid}/steer")
async def steer_session(sid: str, body: dict):
    """随时插话：运行中的 hahaness turn 每轮 LLM 调用前轮询 .steer.jsonl，
    插话下一轮即注入主/子代理上下文（转向/中断实时生效，不等排队）。

    仅当该 session 有 running 的 hahaness turn 时 steered=true；否则
    steered=false，前端回落正常发送（排队为新 turn）。插话落在最后一轮
    之后来不及注入的，engine._finish 按原话回队列（不丢话）。写文件/记账
    逻辑在 Engine.steer_if_running（scheduler.fire 复用同一通道）。
    """
    _get_session_or_404(sid)
    text = (body.get("text") or "").strip()[:4000]
    if not text:
        raise HTTPException(400, "text 不能为空")
    steered = ENGINE.steer_if_running(sid, text) is not None
    return {"ok": True, "steered": steered}


@router.delete("/messages/{mid}")
async def delete_message(mid: int):
    """删除单条气泡（只动展示层：messages 行 + 对应事件；transcript/turn
    记账不动——历史气泡删掉不等于 turn 没跑过）。"""
    with db_mod.conn() as c:
        row = c.execute("SELECT session_id FROM messages WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise HTTPException(404, "消息不存在")
        c.execute("DELETE FROM messages WHERE id=?", (mid,))
        sid = row["session_id"]
    ENGINE.publish(sid, "resync", {}, None)
    return {"ok": True}


@router.put("/messages/{mid}")
async def edit_message(mid: int, body: dict):
    """编辑单条气泡文本（展示层修正；不改 turn 历史——编辑后如需重跑
    请复制修改后的文本重新发送）。"""
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text 不能为空")
    with db_mod.conn() as c:
        row = c.execute("SELECT session_id FROM messages WHERE id=?", (mid,)).fetchone()
        if row is None:
            raise HTTPException(404, "消息不存在")
        c.execute("UPDATE messages SET content=? WHERE id=?", (text, mid))
        sid = row["session_id"]
    ENGINE.publish(sid, "resync", {}, None)
    return {"ok": True}


@router.delete("/turns/{tid}")
async def retract_turn(tid: int):
    """撤回排队中的消息：turn + 消息 + SSE 事件一并删除，等同没发过。

    仅限 queued；运行中的先 stop。条件删除与引擎的 queued→running 原子占位
    互斥（engine._run_turn），队列里残留的 tid 不会被误执行也不会复活。
    """
    if tid in ENGINE.active:
        raise HTTPException(409, "turn 运行中，请先停止再撤回")
    with db_mod.conn() as c:
        turn = db_mod.get_turn(c, tid)
        if turn is None:
            raise HTTPException(404, "turn 不存在")
        cur = c.execute("DELETE FROM turns WHERE id=? AND status='queued'", (tid,))
        if cur.rowcount != 1:
            raise HTTPException(400, "仅排队中的消息可撤回（已开始/已结束的只能停止）")
        c.execute("DELETE FROM messages WHERE turn_id=?", (tid,))
        c.execute("DELETE FROM session_events WHERE turn_id=?", (tid,))
    ENGINE.publish(turn["session_id"], "turn_deleted", {"turn_id": tid}, tid)
    return {"ok": True}


# ---------------------------------------------------------------- files / artifacts
@router.get("/sessions/{sid}/tree")
def get_tree(sid: str):
    _get_session_or_404(sid)
    return {"tree": art.tree(sid)}


@router.get("/sessions/{sid}/file")
def get_file(sid: str, path: str, raw: bool = False):
    _get_session_or_404(sid)
    if raw:
        # 供 iframe src 直载（html/pdf 预览）：html 的 CSP sandbox 与 srcDoc 方案同级
        # 防护（禁脚本），但 iframe 拥有自身 URL → 文档内 fragment 锚点可原生滚动
        # （srcDoc 的锚点会按父页面 base URL 解析，点击把 iframe 导航到宿主路由）。
        # pdf 走浏览器原生阅读器：正确 mime + inline；CSP sandbox 会让部分浏览器
        # 拒绝内联渲染而强制下载，故不加。
        p = art.safe_resolve(ws_mod.ws_of(sid), path)
        if p is None:
            raise HTTPException(404, "file not found")
        suffix = p.suffix.lower()
        headers = {"Cache-Control": "no-store"}
        if suffix in (".html", ".htm"):
            return Response(content=p.read_bytes(), media_type="text/html; charset=utf-8",
                            headers={**headers, "Content-Security-Policy": "sandbox"})
        if suffix == ".pdf":
            headers["Content-Disposition"] = "inline"
            return Response(content=p.read_bytes(), media_type="application/pdf", headers=headers)
        return Response(content=p.read_bytes(), media_type="text/plain; charset=utf-8",
                        headers={**headers, "Content-Security-Policy": "sandbox"})
    return art.read_file(ws_mod.ws_of(sid), path)


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
IMG_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# ---------------------------------------------------------------- 打包下载
# 容量闸：防把 chrome profile / 依赖树这类病态目录打进内存 zip
_ARCHIVE_MAX_FILES = 5000
_ARCHIVE_MAX_BYTES = 2 * 1024 * 1024 * 1024


# W0.6 预览/下载响应头：产物内容不可信（可能含注入的外链/脚本），CSP 钉死。
# default-src 'none' + img-src data:（自包含 html 的内联图）+ style-src 内联。
_DL_HEADERS = {"Content-Security-Policy": "default-src 'none'; img-src data:; "
                                          "style-src 'unsafe-inline'",
               "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer"}


def _collect_archive_files(root: Path) -> list[Path]:
    """root 下的待打包文件（宽进严出）：跳隐藏项/node_modules/chrome*
    （平台控制文件 .fake/.steer.jsonl 与依赖树/登录态不该出工作区）、
    跳符号链接（防环）。超容量闸抛 HTTPException。"""
    out: list[Path] = []
    total = 0
    walked = 0
    for p in root.rglob("*"):
        walked += 1
        if walked > 200_000:      # 病态大目录止损（chrome profile 可达数十万项）
            break
        rel = p.relative_to(root).parts
        if any(s.startswith(".") or s == "node_modules" or s.startswith("chrome")
               for s in rel):
            continue
        try:
            if p.is_symlink() or not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        out.append(p)
        total += size
        if len(out) > _ARCHIVE_MAX_FILES:
            raise HTTPException(400, f"文件数超过 {_ARCHIVE_MAX_FILES}，"
                                     "请分目录打包")
        if total > _ARCHIVE_MAX_BYTES:
            raise HTTPException(400, f"总大小超过 2GB，请分目录打包")
    return out


@router.get("/sessions/{sid}/archive")
def download_archive(sid: str, path: str = ""):
    """工作区目录打包下载（zip）：?path=reports/ 打包子目录，空 = 整个工作区。
    产物面板的「打包全部」就是 ?path=artifacts/。"""
    _get_session_or_404(sid)
    ws = ws_mod.ws_of(sid)
    p = art.safe_resolve(ws, path) if path else ws
    if p is None:
        raise HTTPException(404, "目录不存在")
    files = [p] if p.is_file() else _collect_archive_files(p)
    if not files:
        raise HTTPException(400, "目录为空（或内容都被过滤：隐藏文件/"
                                 "node_modules 不进包）")
    # zip 顶层目录名：所打目录的最后一段（根 = 会话标题），解压不散一地
    top = p.name if path else (sid or "workspace")
    import io
    import zipfile
    from urllib.parse import quote
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f"{top}/{f.relative_to(p)}")
    buf.seek(0)
    size_mb = buf.getbuffer().nbytes / 1024 / 1024
    # header 只容 latin-1：中文名进 filename*=UTF-8''（RFC 5987），
    # filename= 用 ASCII 剥离回退（老浏览器/下载器兼容位）
    ascii_top = top.encode("latin-1", "ignore").decode().strip("-_ ") or "workspace"
    fname = quote(f"{top}.zip")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_top}.zip"; '
                                   f"filename*=UTF-8''{fname}",
            "Cache-Control": "no-store",
            "X-Archive-Files": str(len(files)),
            "X-Archive-MB": f"{size_mb:.1f}",
            **_DL_HEADERS,
        })


@router.post("/sessions/{sid}/upload")
async def upload(sid: str, file: UploadFile = File(...)):
    _get_session_or_404(sid)
    dst_dir = ws_mod.ws_of(sid) / "inputs"
    dst_dir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "upload.bin").name or "upload.bin"
    stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
    suffix = ("." + suffix) if suffix else ""
    # 同名冲突自动重命名 a.png → a-1.png
    n, dst = 0, dst_dir / name
    while dst.exists():
        n += 1
        dst = dst_dir / f"{stem}-{n}{suffix}"
        name = dst.name
    total = 0
    try:
        with dst.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限")
                f.write(chunk)
    except HTTPException:
        dst.unlink(missing_ok=True)
        raise
    kb = round(total / 1024, 1)
    is_image = dst.suffix.lower() in IMG_UPLOAD_EXTS
    ENGINE.publish(sid, "files", {"turn_id": None,
                                  "files": [{"path": f"inputs/{name}", "kb": kb}]})
    return {"ok": True, "path": f"inputs/{name}", "name": name, "kb": kb, "is_image": is_image}


MAX_INGEST_BYTES = 200 * 1024 * 1024
# 直通道目标子目录白名单（page JS / 容器 curl 直传宿主，替代 tmpfiles 中转）
_INGEST_DIRS = ("artifacts/", "notes/", "work/", "inputs/")
# 页内 JS 跨源直传（file:// 或平台页 → 宿主 8792）没有 CORS 头时请求虽能到达
# 但 JS 读不到响应、无法确认成败——FormData POST 是 simple request 不需要预检，
# 响应带 ACAO:* 即闭环。W0 决策（v1.1 方案修订）：ACOA:* 保留（沙箱容器直传是
# 产品链路），跨域攻击面由 token enforce 关闭——token 生效后无凭证跨源 POST
# 在 auth 中间件即 401，ACAO 回显与否无关紧要；目录白名单兜底。
_INGEST_CORS = {"Access-Control-Allow-Origin": "*"}


@router.options("/sessions/{sid}/ingest")
def ingest_preflight(sid: str):
    """CORS 预检兜底（FormData POST 是 simple request 通常不触发，防御性保留）。"""
    _get_session_or_404(sid)
    return Response(status_code=204, headers={
        **_INGEST_CORS, "Access-Control-Allow-Methods": "POST",
        "Access-Control-Allow-Headers": "Content-Type"})


@router.post("/sessions/{sid}/ingest")
async def ingest_file(sid: str, file: UploadFile = File(...), to: str = "artifacts/"):
    """文件直通道（P1-3）：沙箱/浏览器容器/页内 JS 直传文件进 workspace。

    场景：证书 PDF 落在 headless 容器里——页内 fetch 直接 POST 到
    /api/sessions/<sid>/ingest?to=artifacts/（token 走 ?token=），彻底替代
    tmpfiles.org 中转与 base64 分块。目标限白名单目录，同名自动重命名。
    成败响应都带 CORS 头：跨源 JS 能读到结果（HTTPException 默认错误响应
    无 CORS 头会被浏览器吞掉，JS 只见 Failed to fetch 分不清被拒还是断网）。
    """
    _get_session_or_404(sid)
    to = to or "artifacts/"

    def _err(code: int, msg: str) -> JSONResponse:
        return JSONResponse({"error": msg}, status_code=code, headers=_INGEST_CORS)

    if to not in _INGEST_DIRS:
        return _err(400, f"to 只能是 {'/'.join(_INGEST_DIRS)}")
    dst_dir = ws_mod.ws_of(sid) / to.rstrip("/")
    dst_dir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "upload.bin").name or "upload.bin"
    stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
    suffix = ("." + suffix) if suffix else ""
    n, dst = 0, dst_dir / name
    while dst.exists():
        n += 1
        dst = dst_dir / f"{stem}-{n}{suffix}"
        name = dst.name
    total = 0
    with dst.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_INGEST_BYTES:
                f.close()
                dst.unlink(missing_ok=True)
                return _err(413, f"文件超过 {MAX_INGEST_BYTES // (1024 * 1024)}MB 上限")
            f.write(chunk)
    ENGINE.publish(sid, "files", {"turn_id": None,
                                  "files": [{"path": f"{to}{name}", "kb": round(total / 1024, 1)}]})
    return JSONResponse({"ok": True, "path": f"{to}{name}", "name": name,
                         "kb": round(total / 1024, 1)}, headers=_INGEST_CORS)


@router.get("/sessions/{sid}/artifacts")
def get_artifacts(sid: str):
    _get_session_or_404(sid)
    art.scan_session(sid)
    return {"artifacts": art.list_artifacts(sid)}


@router.post("/sessions/{sid}/share")
def post_share(sid: str, body: dict):
    """铸造产物分享链接（幂等）：{path: artifacts/xxx} → {url, token}。"""
    _get_session_or_404(sid)
    from .. import share as share_mod
    try:
        return share_mod.mint(sid, str(body.get("path") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/sessions/{sid}/export")
def post_export(sid: str, body: dict):
    _get_session_or_404(sid)
    try:
        out = art.export(sid, body.get("source_path", ""), body.get("format", "html"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return out


@router.get("/artifacts/{aid}/download")
def download_artifact(aid: int):
    a = art.get_artifact(aid)
    if a is None:
        raise HTTPException(404, "artifact not found")
    p = art.safe_resolve(ws_mod.ws_of(a["session_id"]), a["path"])
    if p is None:
        raise HTTPException(404, "file missing")
    return FileResponse(p, filename=p.name,
                        headers={"Cache-Control": "no-store", **_DL_HEADERS})


@router.get("/artifacts/{aid}/preview")
def preview_artifact(aid: int):
    a = art.get_artifact(aid)
    if a is None:
        raise HTTPException(404, "artifact not found")
    p = art.safe_resolve(ws_mod.ws_of(a["session_id"]), a["path"])
    if p is None:
        raise HTTPException(404, "file missing")
    if a["kind"] == "md":
        from ..exporter import md_to_html
        html = md_to_html.convert(p.read_text(errors="replace"), title=a["title"])
        return Response(content=html, media_type="text/html", headers=_DL_HEADERS)
    data = art.read_file(ws_mod.ws_of(a["session_id"]), a["path"])
    if data.get("cat") == "img":
        import base64
        return Response(content=base64.b64decode(data["b64"]), media_type=data["mime"],
                        headers=_DL_HEADERS)
    return Response(content=p.read_text(errors="replace"), media_type="text/plain",
                    headers=_DL_HEADERS)


# ---------------------------------------------------------------- 统计 / 健康
@router.get("/stats/usage")
def stats_usage(days: int = 7):
    with db_mod.conn() as c:
        daily = db_mod.daily_usage(c, days)
        total = db_mod.usage_totals(c)
        by_profile = {}
        for r in c.execute(
                """SELECT s.profile p, COUNT(*) n, SUM(COALESCE(t.cost_usd,0)) cost
                   FROM turns t JOIN sessions s ON s.id=t.session_id
                   GROUP BY s.profile"""):
            by_profile[r["p"]] = {"turns": r["n"], "cost_usd": round(r["cost"] or 0, 4)}
    return {"daily": daily, "total": total, "by_profile": by_profile}


@router.get("/stats/cost")
def stats_cost(days: int = 14):
    """成本分析页数据：三口径成本（CLI 假价 / z.ai API 真价 / Coding Plan 积分）
    + 按模型 / 每日 / 角色 / Top 会话 / 工具接口调用 聚合。Python 全表聚合
    （千行级 <10ms），不建物化层。"""
    from .. import pricing as pricing_mod

    def _load(text: str | None) -> dict:
        try:
            return json.loads(text) if text else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    tok = dict.fromkeys(("input", "output", "cache_read", "cache_write"), 0)
    cost_cli = 0.0
    turns_n = turns_no_model = 0
    sids: set[str] = set()
    models: dict[str, dict] = {}          # 展示名（原始模型名 or "(未记录)"）→ 聚合
    daily: dict[str, dict] = {}
    profiles: dict[str, dict] = {}
    sess_agg: dict[str, dict] = {}

    def _m_row(name: str, assumed: bool) -> dict:
        return models.setdefault(name, {
            "model": name, "norm": "" if assumed else pricing_mod.normalize_model(name),
            "assumed": assumed, "turns": 0, "input": 0, "output": 0,
            "cache_read": 0, "cache_write": 0, "web_search_requests": 0,
            "cost_api_usd": 0.0, "plan_credits": 0.0})

    with db_mod.conn() as c:
        rows = c.execute(
            """SELECT t.id, t.session_id, t.cost_usd, t.usage_json, t.models_json,
                      t.started_at, s.profile, s.title
               FROM turns t LEFT JOIN sessions s ON s.id=t.session_id""").fetchall()
        for r in rows:
            turns_n += 1
            sids.add(r["session_id"])
            u = _load(r["usage_json"])
            mu = _load(r["models_json"]) if r["models_json"] else None
            turn_cli = r["cost_usd"] or 0
            cost_cli += turn_cli
            turn_api = 0.0
            tu = sum(u.get(k) or 0 for k in db_mod.NUMERIC_USAGE_KEYS)

            # ---- 模型维度（有 models_json 按模型拆；没有则整 turn 归"(未记录)"，
            #      按默认模型价计、assumed 标记）
            if mu:
                for name, m in mu.items():
                    if not isinstance(m, dict):
                        continue
                    it, ot = m.get("inputTokens") or 0, m.get("outputTokens") or 0
                    cr, cw = (m.get("cacheReadInputTokens") or 0,
                              m.get("cacheCreationInputTokens") or 0)
                    row = _m_row(name, assumed=False)
                    row["turns"] += 1
                    row["input"] += it; row["output"] += ot
                    row["cache_read"] += cr; row["cache_write"] += cw
                    row["web_search_requests"] += m.get("webSearchRequests") or 0
                    m_api = pricing_mod.cost_api_usd(
                        name, input_t=it, cache_read_t=cr, cache_write_t=cw, output_t=ot)
                    row["cost_api_usd"] += m_api
                    row["plan_credits"] += pricing_mod.plan_credits(
                        name, input_t=it, cache_t=cr + cw, output_t=ot)
                    tok["input"] += it; tok["output"] += ot
                    tok["cache_read"] += cr; tok["cache_write"] += cw
                    turn_api += m_api
            else:
                turns_no_model += 1
                it, ot = u.get("input_tokens") or 0, u.get("output_tokens") or 0
                cr, cw = (u.get("cache_read_input_tokens") or 0,
                          u.get("cache_creation_input_tokens") or 0)
                row = _m_row("(未记录)", assumed=True)
                row["turns"] += 1
                row["input"] += it; row["output"] += ot
                row["cache_read"] += cr; row["cache_write"] += cw
                m_api = pricing_mod.cost_api_usd(
                    pricing_mod.DEFAULT_MODEL, input_t=it, cache_read_t=cr,
                    cache_write_t=cw, output_t=ot)
                row["cost_api_usd"] += m_api
                row["plan_credits"] += pricing_mod.plan_credits(
                    pricing_mod.DEFAULT_MODEL, input_t=it, cache_t=cr + cw, output_t=ot)
                turn_api += m_api

            # ---- 每日 / 角色 / 会话桶（CLI 假价 + 真实价 + 四类 token 合计）
            day = (r["started_at"] or "")[:10]
            d = daily.setdefault(day, {"turns": 0, "cost_cli_usd": 0.0,
                                       "cost_api_usd": 0.0, "tokens": 0})
            d["turns"] += 1; d["cost_cli_usd"] += turn_cli
            d["cost_api_usd"] += turn_api; d["tokens"] += tu
            p = profiles.setdefault(r["profile"] or "?", {"turns": 0, "cost_cli_usd": 0.0,
                                                          "cost_api_usd": 0.0, "tokens": 0})
            p["turns"] += 1; p["cost_cli_usd"] += turn_cli
            p["cost_api_usd"] += turn_api; p["tokens"] += tu
            sa = sess_agg.setdefault(r["session_id"], {
                "sid": r["session_id"], "title": r["title"] or r["session_id"],
                "profile": r["profile"] or "?", "turns": 0,
                "cost_cli_usd": 0.0, "cost_api_usd": 0.0, "tokens": 0})
            sa["turns"] += 1; sa["cost_cli_usd"] += turn_cli
            sa["cost_api_usd"] += turn_api; sa["tokens"] += tu

        # ---- 工具/接口调用统计（messages.blocks_json 的 [{name,brief,is_error}]）
        tools = [{"name": r["name"], "calls": r["calls"], "errors": r["errors"] or 0}
                 for r in c.execute(
                     """SELECT json_extract(b.value,'$.name') name, COUNT(*) calls,
                               SUM(json_extract(b.value,'$.is_error')) errors
                        FROM messages m, json_each(m.blocks_json) b
                        WHERE m.blocks_json IS NOT NULL
                          AND json_extract(b.value,'$.name') IS NOT NULL
                        GROUP BY name ORDER BY calls DESC LIMIT 30""")]

    def _r2(x: float) -> float:
        return round(x, 4)

    return {
        "totals": {
            "tokens": {**tok, "total_all": sum(tok.values())},
            "cost_cli_usd": round(cost_cli, 2),
            "cost_api_usd": _r2(sum(m["cost_api_usd"] for m in models.values())),
            "plan_credits": round(sum(m["plan_credits"] for m in models.values()), 4),
            "turns": turns_n, "sessions": len(sids),
            "turns_without_model": turns_no_model,
        },
        "by_model": sorted(models.values(), key=lambda m: -m["cost_api_usd"]),
        "daily": [{"day": k, "turns": v["turns"], "tokens": v["tokens"],
                   "cost_cli_usd": _r2(v["cost_cli_usd"]),
                   "cost_api_usd": _r2(v["cost_api_usd"])}
                  for k, v in sorted(daily.items())][-days:],
        "by_profile": [{"profile": k, "turns": v["turns"], "tokens": v["tokens"],
                        "cost_cli_usd": _r2(v["cost_cli_usd"]),
                        "cost_api_usd": _r2(v["cost_api_usd"])}
                       for k, v in sorted(profiles.items(),
                                          key=lambda kv: -kv[1]["cost_api_usd"])],
        "top_sessions": sorted(sess_agg.values(), key=lambda s: -s["cost_api_usd"])[:10],
        "tools": tools,
        "pricing": pricing_mod.pricing_overview(),
    }


@router.get("/health")
def health():
    from .. import engines as engines_mod

    engines = {name: spec.health() for name, spec in engines_mod.ENGINES.items()}
    disk_free_gb = -1.0
    try:
        import shutil as _sh
        _, _, free = _sh.disk_usage(str(PATHS["root"]))
        disk_free_gb = round(free / 1024 ** 3, 1)
    except OSError:
        pass
    # claude_bin/claude_version 顶层字段保留（前端兼容）；engines 为全引擎状态
    return {"claude_bin": engines.get("claude", {}).get("bin"),
            "claude_version": engines.get("claude", {}).get("version"),
            "engines": engines, "default_engine": engines_mod.default_engine(),
            "disk_free_gb": disk_free_gb, "time": iso()}
