"""不可逆动作审批中心（W1-2 MVP）——确认码门，summary 平台渲染防伪造。

核心反注入设计（v1.1 §14-2）：
- summary **由平台从结构化参数渲染**（收件人=to 字段值、金额、目标域），
  agent 只能附 agent_note（UI 弱化展示「来自 agent 的说明」）——注入者
  控制不了用户在卡片上看到的目标地址。
- 执行需 confirm_code（用户批准时一次性生成的 6 位码，明文只在批准响应
  里出现一次）+ params_hash 比对（批准 A 不能执行 B）。
- fail-closed：TTL 600s 过期 → expired；码错/参数不符/重复消费 → 拒。
- 单次有效：consume 即 executed。

首批覆盖：mail_send（r mail --to）/ account_write（r account --set）。
sms/notify 的不可逆面（网关发信）随 W1-b 评估接入（r sms 为收码读操作）。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from .audit import audit
from .config import PATHS
from .util import get_logger

log = get_logger(__name__)

DEFAULT_TTL_S = 600

ACTION_TYPES = {
    "mail_send": "发邮件",
    "account_write": "写凭证",
    "sms_send": "发短信",
    "wechat_send": "发微信",
    "pay": "支付",
    "browser_export": "导出登录态",
    # 网络侧不可逆面：任务级临时放行外联域（限时+审计+到期收回）
    "egress": "临时放行外联",
    # 执行侧规则化（P0-4b）：批准 = 把 token 前缀规则写进会话工作区
    # .loadn/policy.json（下轮生效，同类命令不再 ask）
    "bash_allow": "放行 Bash 命令规则",
    # 浏览器侧（P2-5）：公网域首次 browser.open 的确认门
    "browser_open": "浏览器打开外部域",
}


def _render_summary(action_type: str, params: dict) -> str:
    """平台渲染（agent 不可控）。各动作类型的「用户必须看到什么」。"""
    if action_type == "mail_send":
        return (f"发邮件 → {params.get('to', '?')}"
                f"：{(params.get('subject') or '')[:60]}")
    if action_type == "account_write":
        fields = ",".join(sorted(k for k in params if k != "platform")) or "?"
        return f"写凭证 {params.get('platform', '?')}（字段：{fields}）"
    if action_type == "sms_send":
        return f"发短信 → {params.get('to', '?')}：{(params.get('text') or '')[:60]}"
    if action_type == "pay":
        return f"支付 {params.get('amount', '?')} → {params.get('to', '?')}"
    if action_type == "wechat_export":
        return f"导出登录态：{params.get('target', '?')}"
    if action_type == "bash_allow":
        prefix = params.get("prefix") or []
        note = (params.get("justification") or "")[:60]
        return (f"放行命令规则：{' '.join(str(x) for x in prefix) or '?'}"
                + (f"（{note}）" if note else ""))
    if action_type == "browser_open":
        return f"浏览器打开外部域：{params.get('host', '?')}"
    if action_type == "egress":
        ttl = int(params.get("ttl_s") or 7200)
        h, m = ttl // 3600, (ttl % 3600) // 60
        dur = f"{h}h{m:02d}m" if h else f"{m}m"
        return f"临时放行外联域 {params.get('host', '?')}（{dur}，仅本任务沙箱）"
    return f"{ACTION_TYPES.get(action_type, action_type)} {json.dumps(params, ensure_ascii=False)[:80]}"


def _params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _conn() -> sqlite3.Connection:
    PATHS["db"].parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(PATHS["db"], timeout=10)
    c.row_factory = sqlite3.Row
    return c


_DDL = """
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sid TEXT NOT NULL,
  turn_id INTEGER,
  action_type TEXT NOT NULL,
  summary TEXT NOT NULL,
  agent_note TEXT,
  params_json TEXT NOT NULL,
  params_hash TEXT NOT NULL,
  confirm_code_hash TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  decided_at TEXT, decided_by TEXT, executed_at TEXT,
  ttl_s INTEGER NOT NULL DEFAULT 600
);
CREATE INDEX IF NOT EXISTS idx_approvals_sid ON approvals(sid, status);
"""

_ensured = False


def _ensure(c: sqlite3.Connection) -> None:
    global _ensured
    if not _ensured:
        c.executescript(_DDL)
        _ensured = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(sid: str, action_type: str, params: dict, note: str = "",
           turn_id: int | None = None, ttl_s: int = DEFAULT_TTL_S) -> dict:
    """创建审批请求（pending）+ 审计 + 返回（码不在此步出现）。"""
    if action_type not in ACTION_TYPES:
        raise ValueError(f"未知动作类型 {action_type!r}")
    if action_type == "egress":
        # host 提前验：非法请求在创建即拒，不留到裁决时才炸
        from .egress_grants import valid_host
        if not valid_host(str(params.get("host") or "")):
            raise ValueError("egress 审批需要合法 host（params.host，如 api.example.com）")
    if action_type == "bash_allow":
        # prefix 提前验（P0-4b）：token 数组形态，坏前缀创建即拒
        from loadn.bash_policy import parse_rules
        try:
            parse_rules([{"prefix": params.get("prefix") or [],
                          "decision": "allow"}])
        except ValueError as e:
            raise ValueError(f"bash_allow 审批需要合法 params.prefix：{e}") from None
    with _conn() as c:
        _ensure(c)
        cur = c.execute(
            "INSERT INTO approvals (sid, turn_id, action_type, summary, agent_note,"
            " params_json, params_hash, status, created_at, ttl_s)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, turn_id, action_type, _render_summary(action_type, params),
             (note or "")[:500], json.dumps(params, ensure_ascii=False),
             _params_hash(params), "pending", _now(), ttl_s))
        aid = cur.lastrowid
    audit("approval_request",
          {"id": aid, "sid": sid, "action_type": action_type,
           "summary": _render_summary(action_type, params),
           "agent_note": (note or "")[:200]}, sid=sid, turn_id=turn_id)
    return {"id": aid, "summary": _render_summary(action_type, params),
            "ttl_s": ttl_s}


def decide(aid: int, approve: bool, by: str = "user") -> dict:
    """用户裁决。批准 → 生成一次性 6 位码（明文只在本响应出现一次）。"""
    with _conn() as c:
        _ensure(c)
        row = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise LookupError(f"approval {aid} 不存在")
        if row["status"] != "pending":
            return {"ok": False, "status": row["status"],
                    "error": "非 pending 状态（不可重复裁决）"}
        if _expired(row):
            c.execute("UPDATE approvals SET status='expired', decided_at=? "
                      "WHERE id=?", (_now(), aid))
            audit("approval_decision", {"id": aid, "decision": "expired"},
                  sid=row["sid"])
            return {"ok": False, "status": "expired", "error": "已超时"}
        if approve:
            if row["action_type"] == "egress":
                # 效果在裁决即生效（执行方是平台=授权落表，无 agent 侧确认码/
                # 消费步）；审批行直接终态 executed，授权到期自动失效
                from . import egress_grants
                params = json.loads(row["params_json"])
                host = egress_grants.valid_host(str(params.get("host") or ""))
                if not host:
                    return {"ok": False, "status": "pending",
                            "error": "params.host 非法，未生效"}
                ttl = int(params.get("ttl_s") or egress_grants.DEFAULT_TTL_S)
                g = egress_grants.grant(row["sid"], host, ttl, approval_id=aid)
                c.execute("UPDATE approvals SET status='executed', decided_at=?,"
                          " decided_by=?, executed_at=? WHERE id=?",
                          (_now(), by, _now(), aid))
                audit("approval_decision",
                      {"id": aid, "decision": "approved-executed", "host": host},
                      sid=row["sid"])
                return {"ok": True, "status": "executed", "granted": g}
            if row["action_type"] == "bash_allow":
                # P0-4b：裁决即规则化——token 前缀规则写进会话工作区
                # .loadn/policy.json（flock 原子），引擎下轮加载生效；审批行
                # 直接终态 executed（与 egress 同款「执行方=平台」语义）
                from loadn.bash_policy import amend_policy, parse_rules

                from .workspace import ws_of
                params = json.loads(row["params_json"])
                prefix = params.get("prefix")
                try:
                    parse_rules([{"prefix": prefix or [],
                                  "decision": "allow"}])   # 前置校验，坏前缀不落盘
                    rule = amend_policy(
                        ws_of(row["sid"]),
                        prefix=prefix or [],
                        decision="allow",
                        justification=str(params.get("justification") or "")[:200],
                        source=f"approval:{aid}")
                except ValueError as e:
                    return {"ok": False, "status": "pending",
                            "error": f"prefix 非法，未生效：{e}"}
                # 回写改变信任摘要 → 平台（合法写方）re-admit 刷新之
                #（write_settings 同款）；agent 私改 policy.json 不经此路，
                # 摘要不匹配 → 规则不加载（A6 攻击面）
                try:
                    from loadn.truststore import admit as _admit
                    _admit(ws_of(row["sid"]))
                except Exception:                            # noqa: BLE001
                    pass
                c.execute("UPDATE approvals SET status='executed', decided_at=?,"
                          " decided_by=?, executed_at=? WHERE id=?",
                          (_now(), by, _now(), aid))
                audit("approval_decision",
                      {"id": aid, "decision": "approved-executed",
                       "bash_prefix": prefix},
                      sid=row["sid"])
                return {"ok": True, "status": "executed", "rule": rule}
            code = f"{secrets.randbelow(1000000):06d}"
            code_hash = hashlib.sha256(code.encode()).hexdigest()
            c.execute("UPDATE approvals SET status='approved', decided_at=?,"
                      " decided_by=?, confirm_code_hash=? WHERE id=?",
                      (_now(), by, code_hash, aid))
            audit("approval_decision", {"id": aid, "decision": "approved"},
                  sid=row["sid"])
            return {"ok": True, "status": "approved", "code": code}
        c.execute("UPDATE approvals SET status='denied', decided_at=?, decided_by=?"
                  " WHERE id=?", (_now(), by, aid))
        audit("approval_decision", {"id": aid, "decision": "denied"},
              sid=row["sid"])
        return {"ok": True, "status": "denied"}


def _expired(row) -> bool:
    try:
        created = datetime.fromisoformat(row["created_at"]).timestamp()
    except ValueError:
        return True
    return time.time() - created > (row["ttl_s"] or DEFAULT_TTL_S)


def status(aid: int) -> dict:
    with _conn() as c:
        _ensure(c)
        row = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        if row is None:
            raise LookupError(f"approval {aid} 不存在")
        return {"id": row["id"], "sid": row["sid"], "action_type": row["action_type"],
                "summary": row["summary"], "status": row["status"]}


def consume(sid: str, action_type: str, params: dict,
            confirm_code: str) -> dict:
    """执行前验证：按 (sid, action_type, params_hash) 找最新 approved 未消费，
    码 hash 比对 + single-use。全过 → executed 并放行执行。"""
    ph = _params_hash(params)
    with _conn() as c:
        _ensure(c)
        row = c.execute(
            "SELECT * FROM approvals WHERE sid=? AND action_type=? AND params_hash=?"
            " AND status='approved' AND executed_at IS NULL"
            " ORDER BY id DESC LIMIT 1", (sid, action_type, ph)).fetchone()
        if row is None:
            return {"ok": False, "error": "无匹配的已批准请求（未批准/参数不符/"
                                          "已消费/已过期）"}
        if _expired(row):
            c.execute("UPDATE approvals SET status='expired' WHERE id=?",
                      (row["id"],))
            return {"ok": False, "error": "批准已超时（fail-closed）"}
        if not confirm_code or not row["confirm_code_hash"] or \
                hashlib.sha256(confirm_code.encode()).hexdigest() != row["confirm_code_hash"]:
            audit("approval_decision",
                  {"id": row["id"], "decision": "confirm_code_mismatch"}, sid=sid)
            return {"ok": False, "error": "确认码不符"}
        c.execute("UPDATE approvals SET status='executed', executed_at=? WHERE id=?",
                  (_now(), row["id"]))
    audit("approval_decision",
          {"id": row["id"], "decision": "executed", "action_type": action_type},
          sid=sid)
    return {"ok": True, "id": row["id"]}


def pending_egress_id(sid: str, host: str) -> int | None:
    """该会话对某域是否有 pending 的 egress 审批（弹卡去重——同一域
    连续被拦只挂一张卡，不刷屏）。host 级匹配（ttl 等参数差异不算新卡）。

    顺手惰性过期：超 TTL 的卡翻 expired 再继续匹配（否则挂起轮询会
    对一张永远不再有人裁决的卡空等满额）。"""
    with _conn() as c:
        _ensure(c)
        rows = c.execute(
            "SELECT id, params_json, created_at, ttl_s FROM approvals WHERE"
            " sid=? AND action_type='egress' AND status='pending'",
            (sid,)).fetchall()
        out: int | None = None
        for r in rows:
            if _expired(r):
                c.execute("UPDATE approvals SET status='expired' WHERE id=?",
                          (r["id"],))
                continue
            if out is not None:
                continue
            try:
                if json.loads(r["params_json"]).get("host") == host:
                    out = r["id"]
            except (TypeError, ValueError):
                continue
    return out


def list_pending(sid: str | None = None) -> list[dict]:
    with _conn() as c:
        _ensure(c)
        q = ("SELECT id, sid, action_type, summary, agent_note, status,"
             " created_at, ttl_s FROM approvals")
        args: list = []
        if sid:
            q += " WHERE sid=? AND status IN ('pending','approved')"
            args.append(sid)
        q += " ORDER BY id DESC LIMIT 50"
        return [dict(r) for r in c.execute(q, args)]
