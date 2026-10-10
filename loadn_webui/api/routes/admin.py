"""管理面：资源中心/凭证库/安全中心/审计/egress 策略/熔断全停。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import settings_admin
from ...config import CODE_ROOT, CONFIG, PATHS
from ...engine import ENGINE
from ...integrations import mcp_admin
from ...util import iso

router = APIRouter(prefix="/api")

from ._common import _RES_SERVICE_FIELDS


@router.get("/admin/resources")
def resources_overview():
    """资源中心总览：服务端点+密钥状态（布尔）/ MCP servers / 凭证库。

    密钥值永不出现在响应——只有「已加密保存」与否。
    """
    from ...security import vault as vault_mod
    r = CONFIG.resources
    states = vault_mod.res_secret_states()
    services = [
        {"key": k, "label": lb, "note": nt, "value": str(getattr(r, k) or ""),
         "kind": "endpoint" if k.endswith(("_url", "_base")) or k in
         ("proxy", "sms_phone", "mail_imap", "mail_smtp", "mail_user",
          "vlm_model", "zhipu_engine", "adb_addr", "textr_email") else "secret"}
        for k, lb, nt in _RES_SERVICE_FIELDS]
    secrets = [{"key": k, "set": states.get(k, False)}
               for k in vault_mod.RES_SECRET_FIELDS]
    custom = [{"name": s.get("name"), "url": str(s.get("url") or ""),
               "note": str(s.get("note") or ""),
               "key_set": states.get(f"svc:{s.get('name')}", False)}
              for s in (CONFIG.resources.custom_services or [])
              if isinstance(s, dict) and s.get("name")]
    return {"services": services, "secrets": secrets, "custom": custom,
            "mcp": mcp_admin.list_servers(),
            "vault": {"platforms": vault_mod.list_platforms(),
                      "verify": vault_mod.verify()}}
@router.post("/admin/resources/custom")
def resources_upsert_custom(body: dict):
    """新增/更新自定义服务（name/url/note；upsert by name）。"""
    from . import settings_admin
    try:
        return settings_admin.put_custom_service(body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
@router.delete("/admin/resources/custom")
def resources_delete_custom(name: str):
    """删除自定义服务（yaml+CONFIG+vault svc:<name> 密钥一并清）。"""
    from . import settings_admin
    try:
        return settings_admin.delete_custom_service(name)
    except ValueError as e:
        raise HTTPException(404 if "不存在" in str(e) else 400, str(e)) from e
@router.post("/admin/resources/service")
def resources_set_service(body: dict):
    """改服务端点/参数（明文字段——非密钥）。走 settings 网关校验。"""
    from . import settings_admin
    key = str(body.get("key") or "")
    if key not in {k for k, _, _ in _RES_SERVICE_FIELDS}:
        raise HTTPException(400, f"未知资源字段: {key}")
    try:
        return settings_admin.put_resources({key: str(body.get("value") or "")})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
@router.post("/admin/resources/secret")
def resources_set_secret(body: dict):
    """写资源密钥 → vault（AES-GCM）。值不回显、不落 yaml。"""
    from ...security import vault as vault_mod
    key = str(body.get("key") or "")
    val = str(body.get("value") or "").strip()
    if not val:
        raise HTTPException(400, "密钥值不能为空")
    try:
        vault_mod.set_res_secret(key, val)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "key": key, "set": True}
@router.post("/admin/resources/test")
async def resources_test(body: dict):
    """探测服务连通（ping 子集）。AC-5.10f（P2-6）：结果落 ping_history——
    「昨晚短信服务通不通」可回查，故障排查有据（此前仅内存态，刷新即失）。"""
    from ...integrations import resources
    from ...util import iso
    only = body.get("only") or None
    if isinstance(only, list):
        only = [t for t in only if isinstance(t, str)][:20]
    results = await resources.ping_all(only)
    try:   # 历史落库失败不阻断探测响应（历史是增强，不是主路径）
        with db_mod.conn() as c:
            for target, r in results.items():
                c.execute(
                    "INSERT INTO ping_history(target, ok, ms, msg, ts) "
                    "VALUES (?,?,?,?,?)",
                    (target, 1 if r.get("ok") else 0,
                     r.get("ms"), (str(r.get("msg") or ""))[:200], iso()))
    except Exception:   # noqa: BLE001 —— 探测为主，历史尽力而为
        pass
    return {"results": results}

@router.get("/admin/resources/history")
def resources_history():
    """探测历史聚合（每目标最近一次 + 24h 失败次数）——卡上常驻最近结果。

    在 _ADMIN_READ_PREFIXES /api/admin/resources 覆盖内：普通 cookie 用户 403。
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with db_mod.conn() as c:
        last = {r["target"]: r for r in c.execute(
            "SELECT h.* FROM ping_history h JOIN ("
            "  SELECT target, MAX(id) mid FROM ping_history GROUP BY target"
            ") m ON h.id = m.mid")}
        fails = {r["target"]: r["n"] for r in c.execute(
            "SELECT target, COUNT(*) AS n FROM ping_history "
            "WHERE ts >= ? AND ok = 0 GROUP BY target", (cutoff,))}
    return {"items": [
        {"target": t,
         "last": {"ok": bool(r["ok"]), "ms": r["ms"],
                  "msg": r["msg"], "ts": r["ts"]},
         "fails_24h": fails.get(t, 0)}
        for t, r in sorted(last.items())]}

@router.post("/admin/vault/entry")
def vault_put_entry(body: dict):
    """凭证库写条目（新增/更新）。密码/恢复码只写不回。

    deprecated（AC-1.3）：凭证库唯一编辑面收敛到 PUT /admin/vault/{platform}
    （安全 tab 八字段 merge 编辑器）；本端保留兼容旧客户端，行为不变，
    但补审计（此前写库无留痕）。
    """
    from ...security import vault as vault_mod
    from ...security.audit import audit
    platform = str(body.get("platform") or "").strip()
    if not platform or platform == vault_mod.RES_ENTRY:
        raise HTTPException(400, "platform 非法")
    fields = {}
    for f in ("username", "email", "phone", "twofa", "status", "notes",
              "password", "recovery"):
        v = body.get(f)
        if v is not None and str(v).strip():
            fields[f] = str(v).strip()
    if not fields:
        raise HTTPException(400, "没有可写字段")
    vault_mod.put(platform, **fields)
    audit("vault", {"action": "entry_write", "platform": platform,
                    "fields": sorted(fields)})
    return {"ok": True, "platform": platform}
@router.delete("/admin/vault/entry/{platform}")
def vault_delete_entry(platform: str):
    """删除条目（deprecated 同上；对齐 vault_delete 补 404 与审计）。"""
    from ...security import vault as vault_mod
    from ...security.audit import audit
    if platform == vault_mod.RES_ENTRY:
        raise HTTPException(400, "保留条目不可删")
    if not vault_mod.delete(platform):
        raise HTTPException(404, f"无 {platform} 条目")
    audit("vault", {"action": "admin_delete", "platform": platform})
    return {"ok": True}
@router.get("/admin/security")
def security_posture():
    """六机制姿态+沙箱覆盖率+熔断状态+vault 计数+账本尾（管理面读）。

    只回计数/模式/键名——vault 值、canary token 一律不出现在响应里。
    """
    from ...security import audit as audit_mod
    from ...security import canary as canary_mod
    from ...security import vault as vault_mod
    snaps = audit_mod.tail(100, "snapshot")
    bwrap_n = sum(1 for r in snaps
                  if json.loads(r["detail_json"]).get("mode") == "bwrap")
    locked = []
    with db_mod.conn() as c:
        for r in c.execute(
                "SELECT id FROM sessions WHERE status='active'").fetchall():
            reason = canary_mod.is_locked(r["id"])
            if reason:
                locked.append({"sid": r["id"], "reason": reason})
    rows = audit_mod.tail(1)
    from ...security import sandbox as sandbox_mod
    tier = sandbox_mod.tier_status()
    return {
        "sandbox": {"mode": CONFIG.security.sandbox,
                    "requested": tier["requested"],
                    "effective": tier["effective"],
                    "reason": tier["reason"],
                    "bwrap": bwrap_n, "direct": len(snaps) - bwrap_n,
                    "window": len(snaps)},
        "policy": {"approval_enforce": CONFIG.security.approval_enforce},
        "ops": {"codemode_enabled": CONFIG.security.codemode_enabled,
                "lsp_enabled": CONFIG.security.lsp_enabled,
                "shared_readonly": list(CONFIG.security.shared_readonly),
                "resource_bridges": list(CONFIG.security.resource_bridges),
                "approval_ttl_s": CONFIG.security.approval_ttl_s,
                "egress_proxy_port": CONFIG.security.egress_proxy_port,
                "egress_grant_ttl_s": CONFIG.security.egress_grant_ttl_s},
        "egress": {"mode": CONFIG.security.egress_mode,
                   "on_deny": CONFIG.security.egress_on_deny,
                   "ask_wait_s": CONFIG.security.egress_ask_wait_s,
                   "allow_count": len(CONFIG.security.egress_allow)},
        "canary": {"locked_sessions": locked,
                   "kill_all": (PATHS["run"] / "KILL_ALL").exists()},
        "vault": {"platforms": len(vault_mod.list_platforms())},
        "audit": {"last_id": rows[0]["id"] if rows else 0,
                  "last_ts": rows[0]["ts"] if rows else "",
                  "anchors": len(audit_mod.anchors())},
    }
@router.get("/admin/approvals")
def approvals_overview():
    """全平台待审清单（安全中心「审批」卡明细）。管理面读。"""
    from ...security import approve as approve_mod
    return {"pending": approve_mod.list_pending()}
@router.get("/admin/vault")
def vault_overview():
    """凭证库概览（安全中心卡明细）：条目名/安全字段+完整性校验。

    list_platforms 本就不含 password/recovery 明文——明文永不走 HTTP。
    """
    from ...security import vault as vault_mod
    return {"platforms": vault_mod.list_platforms(),
            "verify": vault_mod.verify()}
@router.get("/admin/vault/{platform}")
def vault_entry(platform: str):
    """单条目掩码视图（编辑器回填用）：密钥字段只回「已设置/位数」。"""
    from ...security import vault as vault_mod
    d = vault_mod.view(platform)
    if d is None:
        raise HTTPException(404, f"无 {platform} 条目")
    return d
@router.put("/admin/vault/{platform}")
def vault_put(platform: str, body: dict):
    """条目编辑（管理面=用户本人操作，不走 agent 审批门；写审计）。

    语义：merge——只改给出的键；密码类字段留空=不改、空串=清除。
    明文只在本次请求体里出现，不回传、不落日志（审计只记字段名）。
    """
    from ...security import vault as vault_mod
    from ...security.audit import audit
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise HTTPException(400, "body 需为 {fields: {...}}")
    clean = {}
    for k, v in fields.items():
        if k not in vault_mod.FIELDS:
            raise HTTPException(400, f"未知字段 {k}（可用：{'/'.join(vault_mod.FIELDS)}）")
        if v is None:
            continue
        if not isinstance(v, str) or len(v) > 500:
            raise HTTPException(400, f"{k} 需为字符串（≤500 字符）")
        clean[k] = v
    if not clean:
        raise HTTPException(400, "无可更新字段")
    try:
        entry = vault_mod.put(platform, **clean)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit("vault", {"action": "admin_edit", "platform": platform,
                    "fields": sorted(clean)})
    return {"ok": True, "platform": platform,
            "set": sorted(clean), "updated_at": entry.get("updated_at")}
@router.delete("/admin/vault/{platform}")
def vault_delete(platform: str):
    """删除条目（确认在 UI 侧；审计留痕）。"""
    from ...security import vault as vault_mod
    from ...security.audit import audit
    if not vault_mod.delete(platform):
        raise HTTPException(404, f"无 {platform} 条目")
    audit("vault", {"action": "admin_delete", "platform": platform})
    return {"ok": True}
@router.get("/admin/audit")
def audit_feed(n: int = 50, type: str | None = None):
    """审计事件流（管理面读；type 过滤同 audit tail）。"""
    from ...security import audit as audit_mod
    rows = audit_mod.tail(max(1, min(n, 200)), type)
    return {"events": [dict(r) for r in rows]}
@router.post("/admin/audit/verify")
def audit_verify():
    """账本哈希链全量校验（管理面写语义——昂贵操作走双头防滥用）。"""
    from ...security import audit as audit_mod
    return {"problems": audit_mod.verify()}
@router.get("/admin/egress")
def egress_recent(n: int = 50):
    """最近外联（面板数据源：audit egress_request 尾窗）+ 活跃临时授权。管理面。"""
    from ...security import audit as audit_mod
    from ...security import egress_grants
    rows = audit_mod.tail(n, "egress_request")
    out = []
    for r in rows:
        d = json.loads(r["detail_json"])
        out.append({"ts": r["ts"], **d})
    return {"events": out, "mode": CONFIG.security.egress_mode,
            "on_deny": CONFIG.security.egress_on_deny,
            "ask_wait_s": CONFIG.security.egress_ask_wait_s,
            "allow": CONFIG.security.egress_allow,
            "grants": egress_grants.list_active()}
@router.post("/admin/egress/allow")
def egress_allow_host(body: dict):
    """放行一个域进出口白名单（持久化+热生效，审计留痕）。管理面写。

    被拒记录一键放行的后端——摩擦从「改 yaml+重启（顺手杀在跑 turn）」降为一点。
    """
    from . import settings_admin
    try:
        return settings_admin.put_egress_allow("add", str(body.get("host") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
@router.put("/admin/egress/policy")
def egress_put_policy(body: dict):
    """出口管控策略写：{mode: off|warn|enforce, on_deny: deny|ask,
    ask_wait_s: 15-600}——全可省略（只改给的键）。持久化+热生效+审计。

    任务级放开不在这里：那是会话 params.egress（PATCH /sessions/{sid}）。
    """
    try:
        return settings_admin.put_security_egress(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
@router.put("/admin/security/ops")
async def put_security_ops(body: dict):
    """安全运维面写（v0.6.5 显式配置化七键）：sandbox 档位/功能开关/
    授权面/审批 TTL——yaml round-trip + CONFIG 原地更新。"""
    try:
        return settings_admin.put_security_ops(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
@router.delete("/admin/egress/allow")
def egress_remove_host(host: str = ""):
    """从白名单移除一个域（持久化+热生效；正在用它的 turn 会开始被拒）。"""
    from . import settings_admin
    try:
        return settings_admin.put_egress_allow("remove", host)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
@router.post("/admin/egress/grant/revoke")
def egress_revoke_grant(body: dict):
    """手动收回一条临时授权（会话级，提前于到期）。管理面写。"""
    from ...security import egress_grants
    host = egress_grants.valid_host(str(body.get("host") or "")) or ""
    sid = str(body.get("sid") or "")
    if not host or not sid or not egress_grants.revoke(sid, host):
        raise HTTPException(404, "授权不存在")
    return {"ok": True, "grants": egress_grants.list_active()}
@router.post("/admin/kill-all")
async def kill_all():
    """全局熔断：停全部活跃 turn + 调度器暂停（KILL_ALL 标记）+ 拒绝新任务。"""
    from ... import db as db_mod
    from ...config import PATHS
    from ...security import canary as canary_mod
    stopped = 0
    with db_mod.conn() as c:
        rows = c.execute("SELECT id, session_id FROM turns WHERE"
                         " status IN ('running','queued')").fetchall()
    for r in rows:
        if await ENGINE.stop_turn(r["id"]):
            stopped += 1
        canary_mod.lock_session(r["session_id"], "kill-all（全局熔断）")
    (PATHS["run"] / "KILL_ALL").write_text("kill-all")
    return {"ok": True, "stopped_turns": stopped,
            "scheduler_paused": True,
            "hint": "恢复：管理中心「安全」tab 解除熔断，或 loadn-web kill-all-clear"}
@router.post("/admin/kill-all/clear")
def kill_all_clear():
    """解除全局熔断：删 KILL_ALL 标记 + 解锁**因熔断锁定**的会话。

    金丝雀命中的锁（reason 不含 kill）不解——那是真实警报，须人工核。
    """
    from ...config import PATHS
    from ...security import canary as canary_mod
    marker = PATHS["run"] / "KILL_ALL"
    existed = marker.exists()
    marker.unlink(missing_ok=True)
    unlocked, kept = [], []
    with db_mod.conn() as c:
        for r in c.execute(
                "SELECT id FROM sessions WHERE status='active'").fetchall():
            reason = canary_mod.is_locked(r["id"])
            if not reason:
                continue
            if "kill" in reason:
                canary_mod.unlock_session(r["id"])
                unlocked.append(r["id"])
            else:
                kept.append({"sid": r["id"], "reason": reason})
    from ...security import audit as audit_mod
    audit_mod.audit("kill_switch", {"action": "clear", "cleared": existed,
                                    "unlocked": unlocked})
    return {"ok": True, "cleared": existed, "unlocked": unlocked,
            "kept_locked": kept,
            "hint": "调度已恢复" if existed else "本就未熔断"}


# ---------------------------------------------------------------- 任务管理页
_SIZE_CACHE: dict[str, tuple[float, int]] = {}      # sid → (monotonic, bytes)
_SIZE_TTL_S = 600


def _workspace_size(sid: str) -> int:
    """workspace 磁盘占用（字节）。缓存 TTL 10 分钟；跳过重目录
    （node_modules/chrome*——tree() 同款规则）+ 条目数止损。"""
    import time as _t
    now = _t.monotonic()
    hit = _SIZE_CACHE.get(sid)
    if hit and now - hit[0] < _SIZE_TTL_S:
        return hit[1]
    from ... import workspace as ws_mod
    root = ws_mod.ws_of(sid)
    total = 0
    if root.exists():
        walked = 0
        for p in root.rglob("*"):
            walked += 1
            if walked > 50_000:                   # 病态工作区止损
                break
            if any(s == "node_modules" or s.startswith("chrome")
                   for s in p.relative_to(root).parts):
                continue
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    _SIZE_CACHE[sid] = (now, total)
    return total


@router.get("/admin/tasks")
def tasks_overview(q: str = "", status: str = "all", project_id: str = "",
                   category_id: int = 0, engine: str = "", pinned: str = "all",
                   starred: str = "all", sort: str = "updated", dir: str = "desc",
                   limit: int = 0, offset: int = 0):
    """任务管理页数据面：全量任务（会话）+ 聚合指标 + 筛选排序。

    行：基础列 + n_turns/n_messages/n_artifacts/tokens/cost/size_bytes/
    project_title/running。属主过滤同 categories（非 admin 只看自己的+
    legacy 无主行）。批量操作不走本面——前端复用既有单任务端点逐个执行。

    AC-4.3b 性能改造：过滤/属主下推 SQL WHERE，created/updated/title/cost
    排序下推 ORDER BY；limit>0 时服务端分页（offset 配套，total 恒为过滤
    后全量计数），size_bytes 目录统计只算返回页（原实现全量行×目录遍历，
    千会话级即风暴）。limit=0（默认）行为不变（全量，兼容旧前端）。
    """
    import json as _json

    from ... import db as db_mod
    from ...security import userauth as _ua

    where, params = [], []
    if status != "all":
        where.append("s.status = ?"); params.append(status)
    if project_id:
        where.append("s.project_id = ?"); params.append(project_id)
    if category_id:
        where.append("s.category_id = ?"); params.append(category_id)
    if engine:
        where.append("s.engine = ?"); params.append(engine)
    if pinned != "all":
        where.append("s.pinned = ?"); params.append(1 if pinned == "yes" else 0)
    if starred != "all":
        where.append("s.starred = ?"); params.append(1 if starred == "yes" else 0)
    q_ = (q or "").strip().lower()
    if q_:
        where.append("(LOWER(s.title) LIKE ? OR LOWER(p.title) LIKE ?)")
        params += [f"%{q_}%", f"%{q_}%"]
    u = _ua.current_user()
    if u is not None and u["role"] != "admin":
        where.append("(s.owner_id IS NULL OR s.owner_id = ?)")
        params.append(u["id"])
    wsql = (" WHERE " + " AND ".join(where)) if where else ""

    # SQL 可下推的排序列（turns/tokens/size 无列，Python 兜底）
    order_col = {"created": "s.created_at", "updated": "s.updated_at",
                 "title": "s.title", "cost": "s.cost_usd"}.get(sort)
    order_sql = (f" ORDER BY {order_col} {'ASC' if dir == 'asc' else 'DESC'}"
                 if order_col else "")

    with db_mod.conn() as c:
        total = c.execute(
            "SELECT COUNT(*) FROM sessions s LEFT JOIN projects p"
            f" ON p.id=s.project_id{wsql}", params).fetchone()[0]
        page_sql = ("SELECT s.*, p.title AS project_title FROM sessions s"
                    f" LEFT JOIN projects p ON p.id=s.project_id{wsql}{order_sql}")
        page_params = list(params)
        if limit > 0:
            page_sql += " LIMIT ? OFFSET ?"
            page_params += [max(1, min(limit, 500)), max(0, offset)]
        rows = [db_mod.to_dict(r) for r in c.execute(page_sql, page_params)]
        n_turns = {r["session_id"]: r["n"] for r in c.execute(
            "SELECT session_id, COUNT(*) AS n FROM turns GROUP BY session_id")}
        n_msgs = {r["session_id"]: r["n"] for r in c.execute(
            "SELECT session_id, COUNT(*) AS n FROM messages GROUP BY session_id")}
        n_arts = {r["session_id"]: r["n"] for r in c.execute(
            "SELECT session_id, COUNT(*) AS n FROM artifacts GROUP BY session_id")}
        running = {r["session_id"] for r in c.execute(
            "SELECT DISTINCT session_id FROM turns"
            " WHERE status IN ('running','queued')")}

    out = []
    for r in rows:
        try:
            usage = _json.loads(r.get("usage_json") or "{}")
        except (ValueError, TypeError):
            usage = {}
        out.append({
            "id": r["id"], "title": r.get("title") or "（未命名）",
            "status": r["status"], "engine": r.get("engine"),
            "profile": r.get("profile"),
            "project_id": r.get("project_id"),
            "project_title": r.get("project_title"),
            "category_id": r.get("category_id"),
            "pinned": bool(r.get("pinned")), "starred": bool(r.get("starred")),
            "n_turns": n_turns.get(r["id"], 0),
            "n_messages": n_msgs.get(r["id"], 0),
            "n_artifacts": n_arts.get(r["id"], 0),
            "tokens": usage.get("total_all") or usage.get("total") or 0,
            "cost_usd": r.get("cost_usd") or 0,
            "size_bytes": _workspace_size(r["id"]),   # 只算返回页（AC-4.3b）
            "running": r["id"] in running,
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        })

    # 无 SQL 列的排序键（turns/tokens/size）在页内/全量结果上 Python 兜底
    key = {"turns": lambda x: x["n_turns"],
           "tokens": lambda x: x["tokens"],
           "size": lambda x: x["size_bytes"]}.get(sort)
    if key:
        out.sort(key=key, reverse=(dir != "asc"))
        if limit > 0:
            out = out[offset:offset + limit] if offset else out[:limit]
    return {"tasks": out, "total": total}


# ---------------------------------------------------------------- 系统页（AC-4.1）

_BACKUP_STATE: dict = {"running": False, "kind": "", "started_at": "",
                       "finished_at": "", "rc": None, "detail": ""}
_BACKUP_LOCK = threading.Lock()


def _proc_started_epoch() -> float | None:
    """本服务进程启动 epoch（/proc/self/stat 第 22 字段 + btime）——uptime 源。

    uvicorn 单进程模型下 self 即服务进程；非 Linux/读失败返回 None（前端
    显示「未知」而非报错）。
    """
    try:
        stat = Path("/proc/self/stat").read_text()
        ticks = int(stat.rsplit(")", 1)[1].split()[19])   # comm 可能含空格——括号后切
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat") as f:
            btime = next(float(ln.split()[1]) for ln in f if ln.startswith("btime"))
        return btime + ticks / hz
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def _vkey(v: str) -> tuple:
    """vX.Y.Z → 可比较元组（解析失败回落 (0,) 排最前）。"""
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return (0,)


def _start_backup_job(kind: str, fn) -> bool:
    """备份/验证后台线程状态机：cmd_backup_run 是分钟级（DB backup API +
    workspace rsync），同步路由会挂死请求——起 daemon 线程，前端轮询
    GET /admin/system 的 backup.running 收敛。

    输出（print）捕获进 detail 尾 200 字符（rc!=0 时前端可见失败原因）。
    running 中重复触发返回 False（路由转 409）。
    """
    with _BACKUP_LOCK:
        if _BACKUP_STATE["running"]:
            return False
        _BACKUP_STATE.update(running=True, kind=kind, rc=None, detail="",
                             started_at=iso(), finished_at="")

    def _worker() -> None:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = fn()
            _BACKUP_STATE.update(running=False, finished_at=iso(), rc=rc,
                                 detail="" if rc == 0
                                 else buf.getvalue().strip()[-200:])
        except Exception as e:  # noqa: BLE001 — 线程兜底：状态必须复位
            _BACKUP_STATE.update(running=False, finished_at=iso(), rc=-1,
                                 detail=str(e)[:200])

    threading.Thread(target=_worker, daemon=True,
                     name=f"admin-{kind}").start()
    return True


@router.get("/admin/system")
def system_overview():
    """系统页数据面：运行版本/部署 releases/uptime/磁盘/DB/调度器/备份。

    管理面读（部署路径与备份清单属平台级信息——挂 _ADMIN_READ_PREFIXES）。
    版本面复用 ops（R7 发布系统）的目录/符号链接探测：开发仓（无
    /opt/loadn 部署结构）时 releases 为空、current 为 None，前端只显示
    运行版本（RELEASE.json 或包版本）。engines 探测不在此聚合——
    前端复用公开 /api/health（各 spec subprocess 探测已有现成面）。
    """
    from datetime import datetime, timezone

    from ... import ops as ops_mod  # 同包复用（R7 常量与读法单一真源）
    from ... import scheduler as sched_mod

    # ---- 版本：运行版本（RELEASE.json → 包版本兜底）+ 部署面
    running: dict = {}
    try:
        running = json.loads((CODE_ROOT / "RELEASE.json").read_text())
    except (OSError, ValueError):
        pass
    if not running.get("version"):
        try:
            from loadn import __version__
            running = {"version": __version__, "source": "repo"}
        except ImportError:
            running = {"source": "unknown"}
    cur = ops_mod._current_version()
    prev = ops_mod._read_deploy_json().get("previous")
    releases = []
    for v in ops_mod._list_releases():
        info = ops_mod._read_release_json(v)
        releases.append({"version": v,
                         "git_sha": (info.get("git_sha") or "")[:10],
                         "built_at": (info.get("built_at") or "")[:19],
                         "current": v == cur, "previous": v == prev,
                         "venv_ok": (ops_mod.RELEASES_DIR / v / ".venv" / "bin"
                                     / "python").exists()})
    newer = [r["version"] for r in releases
             if cur and _vkey(r["version"]) > _vkey(cur)]

    # ---- 运行时：uptime / 磁盘 / DB
    started = _proc_started_epoch()
    disk: dict = {}
    try:
        t_, used, free = shutil.disk_usage(str(PATHS["root"]))
        disk = {"total_gb": round(t_ / (1 << 30), 1),
                "free_gb": round(free / (1 << 30), 1),
                "used_pct": round(used / t_ * 100, 1) if t_ else 0.0}
    except OSError:
        pass
    try:
        db_mb = round(PATHS["db"].stat().st_size / (1 << 20), 1)
    except OSError:
        db_mb = 0.0

    # ---- 调度器：进程内单例存活 + 熔断标记 + job 统计
    alive = bool(sched_mod.SCHEDULER and sched_mod.SCHEDULER._task
                 and not sched_mod.SCHEDULER._task.done())
    with db_mod.conn() as c:
        jobs = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM scheduled_jobs GROUP BY status")}
        next_due = c.execute("SELECT MIN(due_at) m FROM scheduled_jobs"
                             " WHERE status='active'").fetchone()["m"]

    # ---- 备份：清单（manifest 摘要——大小统计 rglob 太贵不做）
    from ... import backup as backup_mod
    backups: list[dict] = []
    root = backup_mod.BACKUP_ROOT
    if root.exists():
        for d in sorted(root.iterdir(), reverse=True)[:20]:
            if not d.is_dir():
                continue
            m: dict = {}
            mf = d / "manifest.json"
            if mf.exists():
                try:
                    m = json.loads(mf.read_text())
                except (OSError, ValueError):
                    m = {"errors": ["manifest 损坏"]}
            backups.append({"name": d.name,
                            "timestamp": m.get("timestamp") or "",
                            "full_workspace": bool(m.get("full_workspace")),
                            "errors": m.get("errors") or [],
                            "complete": mf.exists()})

    return {
        "version": {"running": running, "current": cur,
                    "upgrade_available": newer, "releases": releases},
        "runtime": {
            "pid": os.getpid(),
            "started_at": (datetime.fromtimestamp(started, timezone.utc).isoformat()
                           if started else None),
            "uptime_s": round(time.time() - started, 1) if started else None,
            "disk": disk, "db_size_mb": db_mb},
        "scheduler": {"alive": alive,
                      "check_interval_s": sched_mod.CHECK_INTERVAL,
                      "kill_all": (PATHS["run"] / "KILL_ALL").exists(),
                      "jobs": jobs, "next_due_at": next_due},
        "backup": {"root": str(root), **_BACKUP_STATE, "recent": backups},
        "time": iso(),
    }


@router.post("/admin/system/backup")
def system_backup_run(body: dict):
    """立即备份（后台线程，不阻塞请求）。full_workspace=true 含全量 workspace。

    管理面写——审计留痕；进度/结果由 GET /admin/system 的 backup 状态轮询。
    """
    from ... import backup as backup_mod
    from ...security.audit import audit
    fw = bool(body.get("full_workspace"))
    if not _start_backup_job("backup", lambda: backup_mod.cmd_backup_run(fw)):
        raise HTTPException(409, "已有备份/验证任务在跑")
    audit("system", {"action": "backup_run", "full_workspace": fw})
    return {"ok": True, "started": True, "full_workspace": fw}


@router.post("/admin/system/backup/verify")
def system_backup_verify():
    """验证最近一次备份完整性（关键文件+DB 可开+manifest；后台线程）。"""
    from ... import backup as backup_mod
    from ...security.audit import audit
    if not _start_backup_job("verify", backup_mod.cmd_backup_verify):
        raise HTTPException(409, "已有备份/验证任务在跑")
    audit("system", {"action": "backup_verify"})
    return {"ok": True, "started": True}
