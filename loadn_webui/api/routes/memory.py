"""P6 记忆管理面：域/条目浏览、新建/编辑（护栏重跑+commit）、删除、
历史/版本/恢复。

读写经顶层 loadn/memorystore（进程边界：锁与 manifest 协议单实现）。
GET 只读不记审计；新建/编辑/删除/恢复入审计账本（type=memory）。
管理面写操作吃 admin 双头（_ADMIN_PREFIXES 含 /api/memory，非 GET）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from loadn import memorystore as mstore

from ...security.audit import audit
from ._common import _http_err

router = APIRouter(prefix="/api")


def _dir_of(domain: str, *, create: bool = False):
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
    return {"domains": mstore.list_domain_keys()}


@router.get("/memory/entries")
def list_entries(domain: str):
    dir_ = _dir_of(domain)
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
