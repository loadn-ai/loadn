"""模型库（T10）：多模型 CRUD + 默认标记 + apply（写引擎 provider 配置）。

设计要点：
- api_key 只写不读——列表/详情一律脱敏（sk-***尾4），明文仅引擎侧消费；
- is_default 全库至多一个（设默认互斥清其他）；
- apply = 把该模型的 provider/base_url/api_key/model 写进 $LOADN_HOME/
  config.json（provider_config() 解析序最高位）——引擎默认模型即刻生效，
  新会话（engine=loadn）即用；新会话联动见 sessions.create。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ...security import userauth as _ua
from ...util import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api")

_FIELDS = ("name", "engine", "provider", "base_url", "api_key",
           "model", "is_default", "enabled", "notes")


def _require_admin():
    u = _ua.current_user()
    if u is not None and u["role"] != "admin":
        raise HTTPException(403, "模型库仅管理员可管理")
    return u


def _mask(key: str) -> str:
    k = (key or "").strip()
    if not k:
        return ""
    return f"***{k[-4:]}" if len(k) > 4 else "***"


def _to_api(row: sqlite3.Row) -> dict:
    d = db_mod.to_dict(row)
    d["api_key_masked"] = _mask(d.get("api_key") or "")
    d.pop("api_key", None)          # 明文永不出服务端
    return d


def _get_or_404(c, mid: int) -> sqlite3.Row:
    row = c.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
    if row is None:
        raise HTTPException(404, f"模型不存在: {mid}")
    return row


def _write(c, mid: int, fields: dict) -> sqlite3.Row:
    sets, vals = ["updated_at=datetime('now')"], []
    for k in _FIELDS:
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    vals.append(mid)
    c.execute(f"UPDATE models SET {', '.join(sets)} WHERE id=?", tuple(vals))
    return _get_or_404(c, mid)


def _clear_other_defaults(c, keep_id: int) -> None:
    c.execute("UPDATE models SET is_default=0 WHERE is_default=1 AND id!=?",
              (keep_id,))


@router.get("/models")
def list_models():
    _require_admin()
    with db_mod.conn() as c:
        rows = c.execute(
            "SELECT * FROM models ORDER BY is_default DESC, id").fetchall()
    return {"models": [_to_api(r) for r in rows]}


@router.post("/models")
def create_model(body: dict):
    _require_admin()
    name = (body.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "name 不能为空")
    model = (body.get("model") or "").strip()[:120]
    provider = (body.get("provider") or "openai").strip().lower()
    if provider not in ("openai", "anthropic", "fake"):
        raise HTTPException(400, "provider 只能是 openai|anthropic|fake")
    if provider in ("openai", "anthropic") and not (body.get("base_url") or "").strip():
        raise HTTPException(400, "base_url 不能为空（openai/anthropic 均需端点）")
    if not model:
        raise HTTPException(400, "model 不能为空")
    is_default = 1 if body.get("is_default") else 0
    with db_mod.conn() as c:
        if c.execute("SELECT 1 FROM models WHERE name=?", (name,)).fetchone():
            raise HTTPException(409, f"模型名已存在：{name}")
        cur = c.execute(
            "INSERT INTO models(name, engine, provider, base_url, api_key, model,"
            " is_default, enabled, notes, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
            (name, (body.get("engine") or "loadn").strip()[:40], provider,
             (body.get("base_url") or "").strip()[:500],
             (body.get("api_key") or "").strip()[:500], model,
             is_default, 1 if body.get("enabled", True) else 0,
             (body.get("notes") or "").strip()[:500]))
        mid = cur.lastrowid
        if is_default:
            _clear_other_defaults(c, mid)
        row = _get_or_404(c, mid)
    return {"model": _to_api(row)}


@router.patch("/models/{mid}")
def patch_model(mid: int, body: dict):
    _require_admin()
    updatable = {k: body[k] for k in
                 ("name", "engine", "provider", "base_url", "model",
                  "enabled", "notes") if k in body}
    if "name" in updatable:
        updatable["name"] = (updatable["name"] or "").strip()[:60]
        if not updatable["name"]:
            raise HTTPException(400, "name 不能为空")
    if "api_key" in body:
        # 空串=保持不变（前端不回显明文，全量表单提交时避免误清）
        k = (body.get("api_key") or "").strip()
        if k:
            updatable["api_key"] = k[:500]
    if "is_default" in body:
        updatable["is_default"] = 1 if body["is_default"] else 0
    if not updatable:
        raise HTTPException(400, "无可更新字段")
    with db_mod.conn() as c:
        if "name" in updatable:
            dup = c.execute("SELECT id FROM models WHERE name=? AND id!=?",
                            (updatable["name"], mid)).fetchone()
            if dup:
                raise HTTPException(409, f"模型名已存在：{updatable['name']}")
        row = _write(c, mid, updatable)
        if updatable.get("is_default"):
            _clear_other_defaults(c, mid)
    return {"model": _to_api(row)}


@router.delete("/models/{mid}")
def delete_model(mid: int):
    _require_admin()
    with db_mod.conn() as c:
        _get_or_404(c, mid)
        c.execute("DELETE FROM models WHERE id=?", (mid,))
    return {"ok": True}


@router.post("/models/{mid}/apply")
def apply_model(mid: int):
    """把该模型写进 $LOADN_HOME/config.json（provider_config 最高优先级）——
    引擎默认端点/密钥/模型即刻切换。config.json 里已有的其他键保留。"""
    _require_admin()
    with db_mod.conn() as c:
        row = _get_or_404(c, mid)
    if not row["enabled"]:
        raise HTTPException(400, "模型已停用，先启用再应用")
    from loadn import loadn_home
    cfg_path = Path(loadn_home()) / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) \
            if cfg_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        cfg = {}
    cfg.update({"provider": row["provider"], "base_url": row["base_url"],
                "api_key": row["api_key"], "model": row["model"]})
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    log.info("模型库 apply：config.json ← %s (provider=%s model=%s)",
             row["name"], row["provider"], row["model"])
    return {"ok": True, "applied": row["name"], "model": row["model"]}
