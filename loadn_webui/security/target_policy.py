"""P10 按目标系统的权限三档（allow/ask/never）——外部副作用动作的目标级
持久策略层。

决策序（在审批门**之前**）：never→拒绝+审计；always→放行+审计；
ask/无记录/表损坏→现有审批门（fail-closed）。本地动作（bash 写文件等）
不叠加此表（卡边界）。

- 表 target_policies（主库，SCHEMA_REV 7）：{match, kind, mode, scope_note,
  created_from}。match=域名模式（精确或 *.suffix 通配）或动作类名
  （mail_send/pay/wechat_send/sms_send/browser_export 等外部副作用）。
- 来源：设置页手配；审批卡「Always allow」三档窄化生成（created_from=
  审批 id）；SKILL.md frontmatter `targets:` 声明仅作默认建议展示，
  绝不自动生效（fail-closed：装技能≠授权）。
- Always allow 直接放行 = 用户对该目标的常设决定——create_approval 返回
  一次性码（agent 即用即 consume，与逐次审批同一条 consume 链，审计同留）。
"""
from __future__ import annotations

from .. import db as db_mod
from ..util import iso
from .audit import audit

MODES = ("always", "ask", "never")
KINDS = ("host", "action")            # host=出网域名；action=动作类名


def _valid_mode(m: str) -> bool:
    return m in MODES


def _norm(match: str) -> str:
    return (match or "").strip().lower().rstrip(".")


def match_hit(rule_match: str, key: str) -> bool:
    """匹配：精确，或 *.suffix 通配（含多级）。"""
    rm, k = _norm(rule_match), _norm(key)
    if not rm or not k:
        return False
    if rm == k:
        return True
    if rm.startswith("*."):
        suffix = rm[1:]               # ".example.com"
        return k.endswith(suffix)
    return False


def decide(kind: str, key: str) -> str:
    """决策（只读）。无记录/表损坏/坏行 → ask（fail-closed 走审批门）。"""
    if kind not in KINDS or not (key or "").strip():
        return "ask"
    try:
        with db_mod.conn() as c:
            rows = c.execute(
                "SELECT match, mode FROM target_policies WHERE kind=?",
                (kind,)).fetchall()
    except Exception:                                  # noqa: BLE001 — 表损坏/缺表
        return "ask"
    hits = [r for r in rows
            if isinstance(r["mode"], str) and _valid_mode(r["mode"])
            and match_hit(str(r["match"]), key)]
    if not hits:
        return "ask"
    # 最严优先：never > always > ask（多规则命中时宁严勿松）
    modes = {r["mode"] for r in hits}
    if "never" in modes:
        return "never"
    if "always" in modes:
        return "always"
    return "ask"


def add(match: str, kind: str, mode: str, *, scope_note: str = "",
        created_from: str = "manual") -> int:
    m = _norm(match)
    if not m:
        raise ValueError("match 不能为空")
    if kind not in KINDS:
        raise ValueError(f"kind 只能是 {'|'.join(KINDS)}")
    if not _valid_mode(mode):
        raise ValueError(f"mode 只能是 {'|'.join(MODES)}")
    with db_mod.conn() as c:
        cur = c.execute(
            "INSERT INTO target_policies(match, kind, mode, scope_note,"
            " created_from, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (m, kind, mode, scope_note[:200], created_from, iso(), iso()))
        pid = cur.lastrowid
    audit("policy_change", {"action": "target_add", "match": m, "kind": kind,
                            "mode": mode, "created_from": created_from})
    return pid


def list_all() -> list[dict]:
    try:
        with db_mod.conn() as c:
            return [db_mod.to_dict(r) for r in c.execute(
                "SELECT * FROM target_policies ORDER BY kind, match")]
    except Exception:                                  # noqa: BLE001
        return []


def set_mode(pid: int, mode: str) -> None:
    if not _valid_mode(mode):
        raise ValueError(f"mode 只能是 {'|'.join(MODES)}")
    with db_mod.conn() as c:
        row = c.execute("SELECT match FROM target_policies WHERE id=?",
                        (pid,)).fetchone()
        if row is None:
            raise LookupError(f"策略不存在: {pid}")
        c.execute("UPDATE target_policies SET mode=?, updated_at=? WHERE id=?",
                  (mode, iso(), pid))
    audit("policy_change", {"action": "target_mode", "id": pid, "mode": mode})


def delete(pid: int) -> None:
    with db_mod.conn() as c:
        row = c.execute("SELECT match FROM target_policies WHERE id=?",
                        (pid,)).fetchone()
        if row is None:
            raise LookupError(f"策略不存在: {pid}")
        c.execute("DELETE FROM target_policies WHERE id=?", (pid,))
    audit("policy_change", {"action": "target_delete", "id": pid})


def skill_suggestions() -> list[dict]:
    """webui 侧实现：扫平台 skills 目录 frontmatter 的 targets 字段。"""
    import re

    from ..config import skills_dirs
    out = []
    fm_re = re.compile(r"^targets:\s*\[(.*?)\]\s*$", re.M)
    for d in skills_dirs():
        try:
            dirs = sorted(d.iterdir())
        except OSError:
            continue
        for sub in dirs:
            md = sub / "SKILL.md"
            if not md.is_file():
                continue
            try:
                head = md.read_text(encoding="utf-8", errors="replace")[:2000]
            except OSError:
                continue
            m = fm_re.search(head)
            if not m:
                continue
            for item in m.group(1).split(","):
                item = item.strip().strip("'\"")
                if item:
                    out.append({"skill": sub.name, "target": item})
    return out
