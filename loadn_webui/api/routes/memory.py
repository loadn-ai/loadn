"""P6 记忆管理面：域/条目浏览、新建/编辑（护栏重跑+commit）、删除、
历史/版本/恢复。

读写经顶层 loadn/memorystore（进程边界：锁与 manifest 协议单实现）。
GET 只读不记审计；新建/编辑/删除/恢复入审计账本（type=memory）。
管理面写操作吃 admin 双头（_ADMIN_PREFIXES 含 /api/memory，非 GET）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from loadn import memorystore as mstore
from loadn.util import parse_frontmatter as _parse_fm

from ...security.audit import audit
from ._common import _get_session_or_404, _http_err

router = APIRouter(prefix="/api")


def ws_of(sid: str):
    from ... import workspace as ws_mod
    return ws_mod.ws_of(sid)        # 二轮修#5：DB 感知（项目子任务≠默认路径）


def _guard_domain(domain: str) -> None:
    """多用户批3：记忆域属主收口（token/宽限通道 user=None 放行）。

    - user 域=全局用户记忆 → admin 维护（普通用户 403）
    - p: 域=项目记忆 → 反查 projects ∪ sessions（hex 缓存映射）→ 属主；
      无映射域 → admin（不暴露存在性以外信息）。工作区记忆批：普通（非
      项目）会话的工作区不在 projects 表，补 sessions 反查——会话属主
      在任务右侧边栏·工作区·记忆里可管自己工作区的项目记忆域。
    """
    from ...security import userauth as _ua
    u = _ua.current_user()
    if u is None or u["role"] == "admin":
        return
    if domain == "user":
        raise HTTPException(403, "用户记忆域是全局知识库——由管理员维护")
    if domain.startswith("p:") and len(domain) == 14:
        from loadn import memorystore as _ms

        from ... import db as _db
        hex_ = domain[2:]
        with _db.conn() as _c:
            rows = _c.execute(
                "SELECT owner_id, workspace FROM projects "
                "WHERE workspace IS NOT NULL").fetchall() + _c.execute(
                "SELECT owner_id, workspace FROM sessions "
                "WHERE workspace IS NOT NULL").fetchall()
        for pr in rows:
            ws = pr["workspace"]
            if ws and _ms.cached_project_key(ws) == hex_ \
                    and _ua.owner_ok(pr, u):
                return
    raise HTTPException(404, f"记忆域不存在: {domain}（先有写入才有域）")


def _dir_of(domain: str, *, create: bool = False):
    _guard_domain(domain)          # 多用户批3：属主收口（读写同门）
    d = mstore.domain_dir_by_key(domain or "", create=create)
    if d is None:
        raise HTTPException(404, f"记忆域不存在: {domain}（先有写入才有域）")
    return d


def _audit_sink(domain: str):
    """storage 事件 → 审计账本（type=memory，action 去前缀）。"""
    def sink(t: str, p: dict) -> None:
        audit("memory", {"action": t.removeprefix("memory_"),
                         "domain": domain, **p})
    return sink


def _entry_of(dir_, eid: str) -> dict:
    e = next((x for x in mstore.entries_of(dir_) if x.get("id") == eid), None)
    if e is None:
        raise HTTPException(404, f"条目不存在: {eid}")
    return e


@router.get("/memory/domains")
def list_domains():
    """域列表 + 可读信息：p:hex 对用户不可读（记忆页可读性批）——
    sessions/projects 的 workspace 反查 hex 标注 label，条目 manifest
    出 count/last_at。sessions.workspace 取最新（DESC 覆盖式），归档
    会话回落全条目 origin_session 反查；仍无 → label 空（前端显 hex）。"""
    keys = mstore.list_domain_keys()
    from ... import db as _db
    ws_map: dict[str, str] = {}
    claude2s: dict[str, str] = {}
    sid2s: dict[str, str] = {}
    with _db.conn() as c:
        for r in c.execute(
                "SELECT title, workspace FROM sessions "
                "WHERE workspace IS NOT NULL ORDER BY created_at DESC"):
            ws_map[mstore.cached_project_key(r["workspace"])] = r["title"]
        for r in c.execute(
                "SELECT title, workspace FROM projects "
                "WHERE workspace IS NOT NULL"):
            ws_map.setdefault(mstore.cached_project_key(r["workspace"]),
                              r["title"])
        claude2s = {r["claude_session_id"]: r["title"] for r in c.execute(
            "SELECT claude_session_id, title FROM sessions")
            if r["claude_session_id"]}
        sid2s = {r["id"]: r["title"] for r in c.execute(
            "SELECT id, title FROM sessions")}
    info: dict[str, dict] = {}
    for k in keys:
        d = mstore.domain_dir_by_key(k)
        es = mstore.entries_of(d) if d else []
        if k == "user":
            label = "用户级（跨项目）"
        else:
            label = ws_map.get(k[2:], "")
            if not label:
                for e in sorted(es, key=lambda x: x.get("created_at") or "",
                                reverse=True):
                    sid = e.get("origin_session") or ""
                    if sid in claude2s:
                        label = claude2s[sid]
                        break
                    if sid in sid2s:
                        label = sid2s[sid]
                        break
        last = max(es, key=lambda x: x.get("created_at") or "") if es else None
        info[k] = {"label": label, "count": len(es),
                   "last_at": (last or {}).get("created_at"),
                   "last_summary": (last or {}).get("summary")}
    return {"domains": keys, "info": info}


@router.get("/memory/entries")
def list_entries(domain: str):
    try:
        dir_ = _dir_of(domain)
    except HTTPException as e:
        # 空域不是错误：user 域恒在列表但「先有写入才有域」——目录缺失
        # 即空态（200 空列表）；p:hex 域无目录/属主拒绝仍走原 404
        if e.status_code == 404 and domain == "user":
            return {"entries": []}
        raise
    return {"entries": mstore.entries_of(dir_)}


@router.get("/memory/file")
def get_file(domain: str, id: str):
    dir_ = _dir_of(domain)
    e = _entry_of(dir_, id)
    return {"entry": e, "text": mstore.entry_file_text(dir_, e)}


@router.post("/memory/file")
def create_file(body: dict):
    """新建条目（护栏同守；保存即 commit `memory: manual:webui 新增`）。"""
    dir_ = _dir_of(str(body.get("domain") or ""), create=True)
    try:
        e = mstore.create_entry(dir_, str(body.get("summary") or ""),
                                str(body.get("content") or ""),
                                event_sink=_audit_sink(str(body.get("domain"))))
    except mstore.MemoryOpError as e:
        raise HTTPException(400, str(e)) from None
    if e is None:
        raise HTTPException(400, "写入被拒（护栏或用户域关闭）")
    return {"ok": True, "entry": e}


@router.put("/memory/file")
def put_file(body: dict):
    """编辑条目：护栏重跑（命中 400 返回原因），保存即 commit
    `memory: manual:webui 编辑 <id>`。"""
    domain = str(body.get("domain") or "")
    dir_ = _dir_of(domain)
    try:
        e = mstore.edit_entry(
            dir_, str(body.get("id") or ""),
            content=(body["content"] if "content" in body else None),
            summary=(str(body["summary"]) if body.get("summary") else None),
            event_sink=_audit_sink(domain))
    except mstore.MemoryOpError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001
        raise _http_err(e)
    return {"ok": True, "entry": e}


@router.delete("/memory/entry")
def delete_entry(domain: str, id: str):
    """删除条目（git 史保留可恢复）；删除即 commit。"""
    dir_ = _dir_of(domain)
    try:
        e = mstore.delete_entry(dir_, id, event_sink=_audit_sink(domain))
    except mstore.MemoryOpError as e:
        raise HTTPException(404, str(e)) from None
    return {"ok": True, "deleted": e["id"]}


@router.get("/memory/history")
def get_history(domain: str, id: str):
    dir_ = _dir_of(domain)
    # 已删条目同样可查史（删→历史在→可恢复）：按文件名查 git 史，
    # 不要求条目仍在 manifest
    return {"history": mstore.file_history(dir_, f"{id}.md")}


@router.get("/memory/version")
def get_version(domain: str, id: str, ref: str):
    """某版本全文（编辑器侧栏 diff/查看用）。"""
    dir_ = _dir_of(domain)
    try:
        return {"text": mstore.version_text(dir_, f"{id}.md", ref)}
    except mstore.MemoryOpError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/memory/restore")
def restore_version(body: dict):
    """恢复条目到某版本（走 edit 护栏+锁；禁整仓 reset）。"""
    domain = str(body.get("domain") or "")
    dir_ = _dir_of(domain)
    try:
        e = mstore.restore_version(dir_, str(body.get("id") or ""),
                                    str(body.get("ref") or ""),
                                    event_sink=_audit_sink(domain))
    except mstore.MemoryOpError as e:
        raise HTTPException(400, str(e)) from None
    return {"ok": True, "entry": e}


# ------------------------------------------------ 工作区记忆批：任务右侧边栏
@router.get("/sessions/{sid}/memory")
def session_memory(sid: str):
    """会话工作区的项目记忆域（任务右侧边栏·工作区·记忆子 tab 数据源）。

    返回域键 p:<hex>（前端后续编辑/删除/历史/恢复走 /api/memory/* 同域，
    写操作经 _guard_domain 会话属主收口）+ 条目清单。空域（先有写入才有
    域）→ 空列表；会话属主检查复用 _get_session_or_404（项目子任务
    workspace 同表，hex 按 ws_of 的 DB 感知路径算——与引擎注入同域）。
    """
    _get_session_or_404(sid)
    domain = f"p:{mstore.cached_project_key(str(ws_of(sid)))}"
    d = mstore.domain_dir_by_key(domain)
    return {"domain": domain, "entries": mstore.entries_of(d) if d else []}


# ---------------------------------------------------------------- P7 sources
@router.get("/sessions/{sid}/messages/{mid}/sources")
def message_sources(sid: str, mid: int):
    """P7 来源标注：assistant 消息的 memory_hits → 逐条解析命中记忆的
    全文 + 来源会话 id + 当前状态（present/modified/deleted——已删除时
    内容经 git 史回溯）。旧消息无 memory_hits → {"hits": []}（向后兼容）。"""
    from ... import db as db_mod2
    with db_mod2.conn() as c:
        row = c.execute(
            "SELECT memory_hits_json, role FROM messages WHERE id=? AND session_id=?",
            (mid, sid)).fetchone()
    if row is None:
        raise HTTPException(404, f"消息不存在: {mid}")
    try:
        hits = json.loads(row["memory_hits_json"] or "[]")
    except ValueError:
        hits = []
    if not isinstance(hits, list):
        hits = []
    out = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        eid = str(h.get("id") or "")
        dkey = str(h.get("domain") or "")
        # 复查修#3：project 域按「该会话的项目域目录」解析（域枚举名不是
        # 目录键——此前 project 命中恒误报已删除）
        pdir = mstore.memory_dir(ws_of(sid)) if dkey == "project" else None
        d, e = mstore.find_entry(dkey, eid, project_dir=pdir)
        item = {"id": eid, "domain": dkey,
                "reason": h.get("reason"),
                "origin_session": (e or {}).get("origin_session"),
                "status": "deleted", "content": None}
        if e is not None:
            cur_hash = mstore.content_hash8(e.get("content") or "")
            if h.get("hash") and cur_hash != h.get("hash"):
                item["status"] = "modified"
            else:
                item["status"] = "present"
            item["content"] = e.get("content") or ""
        elif d is not None and (d / ".git").exists():
            # 已删除：git 史最新版本回溯（删→史在→可回溯）
            hist = mstore.file_history(d, f"{eid}.md", limit=5)
            for v in hist:
                try:
                    text = mstore.version_text(d, f"{eid}.md", v["hash"])
                    _, body = _parse_fm(text)
                    item["content"] = (body or "").strip()
                    break
                except mstore.MemoryOpError:
                    continue
        out.append(item)
    return {"message_id": mid, "session_id": sid, "hits": out}
