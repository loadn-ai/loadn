"""审计账本（W6.1 完整版）：append-only 哈希链 + 链头每日外置锚点。

- 每行 hash = SHA-256(prev_hash || canonical_json(ts,sid,turn_id,type,detail))，
  prev_hash 取上一行——单行篡改/删除即断链（verify 逐行重算定位）。
- **链头每日外置**（var/audit_heads/YYYYMMDD.txt，0600）：对抗整库重算重写的
  攻击者——库内哈希可以全部重算得天衣无缝，但外置锚点文件对不上。
  诚实口径：单机提供「事后可检测」，不宣称「不可篡改」。
- 写入失败策略：log.error 且不阻塞调用方（哈希链下重写为 fail-closed 属
  W6 完整化决策——当前审计写失败仅丢一行留痕，不拦截业务）。
- 月分表 audit_events_YYYYMM：TODO（量级到百万行前单表即可，分表意义在
  轮转归档，随 W6.5 红队全量时落地）。

无 UPDATE/DELETE API；UI 只读。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import PATHS
from .util import get_logger

log = get_logger(__name__)

# 事件类型枚举（v1.1 §6.7）
TYPES = (
    "permission_decision",   # policy.py 判定（allow/block/warn）
    "approval_request", "approval_decision",
    "tool_call_blocked",
    "egress_request", "credential_use", "policy_change",
    "skill_install", "skill_scan",
    "canary_hit", "sandbox_violation",
    "rollback", "kill_switch", "snapshot",
    "anomaly",
)

GENESIS = "0" * 64

_DDL = """
CREATE TABLE IF NOT EXISTS {t} (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  sid TEXT,
  turn_id INTEGER,
  type TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
)
"""

_last_anchor_day: str = ""        # 进程内去重（跨进程由锚点文件自身幂等兜底）


def _table_for(ts: str) -> str:
    """月分表路由（v1.1 §6.7）：audit_events_YYYYMM；主表留作历史。"""
    return "audit_events_" + (ts[:7].replace("-", "") if ts else "197001")


def _conn() -> sqlite3.Connection:
    PATHS["var"].mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(PATHS["var"] / "audit.db", timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _all_tables(c: sqlite3.Connection) -> list[str]:
    """全部审计表（主表+各月表，升序=链序——分表按创建顺序接力）。"""
    rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND"
                     " (name='audit_events' OR name LIKE 'audit_events_______')"
                     " ORDER BY name").fetchall()
    return [r["name"] for r in rows]


_initialized = False


def _ensure_table(c: sqlite3.Connection, table: str = "audit_events") -> None:
    c.execute(_DDL.format(t=table))
    c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_type_ts"
              f" ON {table}(type, ts)")
    global _initialized
    if not _initialized:
        _migrate_placeholder_chain(c)
        _initialized = True


def repair_monthly_chain() -> int:
    """一次性修复：月表链重算——每行 prev 应接上一行（首行接主表尾）。

    背景：分表初版取链尾恒指历史主表尾，月表内 prev 全错（verify 报断链）。
    幂等：重算结果与正确链一致时无变化。
    """
    with _conn() as c:
        tables = _all_tables(c)
        if len(tables) < 2:
            return 0
        main, months = tables[0], tables[1:]
        # repair 语义=按 id 序接力：主表尾取 id 最大（同秒 ts DESC 不稳——
        # 首版 repair 拿到倒数第二行，实抓）
        row = c.execute(f"SELECT hash FROM {main} ORDER BY id DESC"
                        " LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        n = 0
        for t in months:
            for r in c.execute(f"SELECT id, ts, sid, turn_id, type,"
                               f" detail_json FROM {t} ORDER BY id"):
                h = _row_hash(prev, _canonical(r["ts"], r["sid"],
                                               r["turn_id"], r["type"],
                                               r["detail_json"]))
                c.execute(f"UPDATE {t} SET prev_hash=?, hash=? WHERE id=?",
                          (prev, h, r["id"]))
                prev = h
                n += 1
    # 重算改变历史行 hash——旧日锚点已指向失效值，重锚当前链头
    global _last_anchor_day
    _last_anchor_day = ""
    _maybe_anchor(prev)
    log.info("审计月表链重算修复：%d 行（已重锚链头）", n)
    return n


def _migrate_placeholder_chain(c: sqlite3.Connection) -> None:
    """一次性迁移：W1-a 最小版占位行（prev/hash 恒创世值）→ 重算为真链。

    占位行本身没有可校验性，重算=建立链基线（发生在 W6.1 上线时，属合法
    一次性动作；此后任何重算都是篡改信号，由 verify+锚点抓）。
    """
    try:
        rows = list(c.execute("SELECT * FROM audit_events ORDER BY id"))
    except sqlite3.OperationalError:
        return                      # 主表不存在（分表新库）——无占位链可迁
    prev = GENESIS
    for r in rows:
        h = _row_hash(prev, _canonical(r["ts"], r["sid"], r["turn_id"],
                                       r["type"], r["detail_json"]))
        c.execute("UPDATE audit_events SET prev_hash=?, hash=? WHERE id=?",
                  (prev, h, r["id"]))
        prev = h
    log.info("审计账本迁移：%d 行占位哈希重算为链基线", len(rows))


def _canonical(ts: str, sid: str | None, turn_id: int | None,
               type_: str, detail_json: str) -> str:
    """规范序 JSON（键排序+紧凑分隔——重算侧必须逐字节同构）。"""
    return json.dumps(
        {"ts": ts, "sid": sid, "turn_id": turn_id, "type": type_,
         "detail": json.loads(detail_json)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_hash(prev: str, canonical: str) -> str:
    return hashlib.sha256((prev + "|" + canonical).encode("utf-8")).hexdigest()


def audit(type_: str, detail: dict, *, sid: str | None = None,
          turn_id: int | None = None) -> None:
    """追加一条审计事件（链式哈希 + 惰性日锚点；永不抛）。"""
    if type_ not in TYPES:
        raise ValueError(f"未知审计事件类型 {type_!r}（TYPES 枚举外）")
    ts = datetime.now(timezone.utc).isoformat(timespec="microseconds")  # 秒级在同秒多写下链尾判定不稳（实测教训）
    detail_json = json.dumps(detail, ensure_ascii=False, default=str)
    try:
        with _conn() as c:
            # 链尾=全部表中 ts 最新的一行（分表各表 id 独立——旧法逐表
            # 循环在存在历史主表时永远取旧尾，月表 prev 全错——生产实测教训）
            tables = _all_tables(c)
            if tables:
                union = " UNION ALL ".join(
                    f"SELECT hash, ts, rowid FROM {t}" for t in tables)
                row = c.execute(f"SELECT hash FROM ({union})"
                                " ORDER BY ts DESC, rowid DESC"
                                " LIMIT 1").fetchone()
                prev = row["hash"] if row else GENESIS
            else:
                prev = GENESIS
            h = _row_hash(prev, _canonical(ts, sid, turn_id, type_, detail_json))
            table = _table_for(ts)
            _ensure_table(c, table)
            c.execute(
                f"INSERT INTO {table} (ts, sid, turn_id, type, detail_json,"
                " prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (ts, sid, turn_id, type_, detail_json, prev, h))
        _maybe_anchor(h)
    except sqlite3.Error:
        log.exception("审计写入失败（不阻塞调用方）type=%s", type_)


# ---------------------------------------------------------------- 链头外置锚点

def _anchor_dir() -> Path:
    return PATHS["var"] / "audit_heads"


def _maybe_anchor(head_hash: str) -> None:
    """每日锚点文件：记录当日**最新**链头（覆盖写，0600）。

    锚点行形如 `2026-09-22T12:34:56+00:00 <hash>`。选「最新链头」而非
    「首写链头」：首写锚定后追加的行无外置锚——攻击者重算整库即可绕过
    （E2 教训：首写锚在被篡改行之前，比对恒过）。锚最新链头 = 攻击前
    最后一行 hash 必须仍在账本中——整库重算必暴露。覆盖写一天一次足够
    （进程内去重），跨进程由文件时间戳幂等；日终链头由次日首写补锚。
    """
    global _last_anchor_day
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    _last_anchor_day = today       # 仅作展示/调试态；锚点每次写入都覆盖更新
    try:
        d = _anchor_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{today}.txt"
        f.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds")
                     + " " + head_hash + "\n")
        f.chmod(0o600)
    except OSError:
        log.exception("审计锚点写入失败（下次写入重试）")


def anchors() -> list[dict]:
    """全部外置锚点（verify/展示用）。"""
    out = []
    d = _anchor_dir()
    if not d.exists():
        return out
    for f in sorted(d.glob("*.txt")):
        for line in f.read_text().splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2:
                out.append({"day": f.stem, "ts": parts[0], "hash": parts[1]})
    return out


# ---------------------------------------------------------------- 读 / 校验

def tail(n: int = 20, type_: str | None = None) -> list[dict]:
    """最近事件（loadn-web audit tail / 调试用）。"""
    with _conn() as c:
        out: list[dict] = []
        for t in reversed(_all_tables(c)):            # 新表在前
            q = f"SELECT * FROM {t}"
            args: list = []
            if type_:
                q += " WHERE type=?"
                args.append(type_)
            q += " ORDER BY id DESC LIMIT ?"
            args.append(n)
            out.extend(dict(r) for r in c.execute(q, args))
            if len(out) >= n:
                break
        return out[:n]


def verify() -> list[str]:
    """逐行重算哈希链 + 锚点比对。返回问题清单（空=账本健康）。

    E2 语义：单行篡改 → 断链定位行号；整库重算重写 → 锚点比对暴露。
    """
    problems: list[str] = []
    prev = GENESIS
    rows_by_hash: dict[str, int] = {}
    with _conn() as c:
        rows = []
        for t in _all_tables(c):                      # 链序=表序+表内 id 序
            rows.extend(c.execute(f"SELECT * FROM {t} ORDER BY id"))
    for r in rows:
        if r["prev_hash"] != prev:
            problems.append(f"行 {r['id']}: prev_hash 断链"
                            f"（期望 {prev[:12]}…，实际 {r['prev_hash'][:12]}…）")
        h = _row_hash(prev, _canonical(r["ts"], r["sid"], r["turn_id"],
                                       r["type"], r["detail_json"]))
        if h != r["hash"]:
            problems.append(f"行 {r['id']}: hash 不符（内容被篡改或链错）")
        prev = r["hash"]
        rows_by_hash.setdefault(r["hash"], r["id"])
    for a in anchors():
        if a["hash"] not in rows_by_hash:
            problems.append(
                f"锚点 {a['day']} {a['ts']}: 链头 {a['hash'][:12]}… 不在账本中"
                "（整库重算/截断的强信号）")
    return problems


def export_jsonl() -> str:
    """全量导出（SIEM 对接 / 离线回放）。"""
    with _conn() as c:
        rows = []
        for t in _all_tables(c):
            rows.extend(c.execute(f"SELECT * FROM {t} ORDER BY id"))
    return "\n".join(
        json.dumps({k: r[k] for k in
                    ("id", "ts", "sid", "turn_id", "type", "detail_json",
                     "prev_hash", "hash")},
                   ensure_ascii=False) for r in rows)
