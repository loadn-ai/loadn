"""管理面：资源中心/凭证库/安全中心/审计/egress 策略/熔断全停。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ... import db as db_mod
from ... import settings_admin
from ...config import CONFIG, PATHS
from ...engine import ENGINE
from ...integrations import mcp_admin

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
    """探测服务连通（ping 子集）。"""
    from ...integrations import resources
    only = body.get("only") or None
    if isinstance(only, list):
        only = [t for t in only if isinstance(t, str)][:20]
    return {"results": await resources.ping_all(only)}
@router.post("/admin/vault/entry")
def vault_put_entry(body: dict):
    """凭证库写条目（新增/更新）。密码/恢复码只写不回。"""
    from ...security import vault as vault_mod
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
    return {"ok": True, "platform": platform}
@router.delete("/admin/vault/entry/{platform}")
def vault_delete_entry(platform: str):
    from ...security import vault as vault_mod
    if platform == vault_mod.RES_ENTRY:
        raise HTTPException(400, "保留条目不可删")
    return {"ok": vault_mod.delete(platform)}
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
