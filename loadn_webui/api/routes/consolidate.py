"""P12 建议卡决策面：会话工作区 .loadn/skill-suggest.json 的读 + 确认/拒绝。

确认 → 写会话工作区 .agents/skills/<name>/SKILL.md（过 skill_scan 八类扫描，
红线拒写）；拒绝 → 负样本（同类指纹 7 天抑制，引擎侧同文件）。审计
type=consolidate（accepted/rejected）。draft 教训的转正走既有记忆编辑 API
（edit_entry 保存即除 draft 标记）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from loadn import consolidate

from ...security.audit import audit

router = APIRouter(prefix="/api")


def _ws_of(sid: str):
    from ...config import PATHS
    ws = PATHS["workspace"] / sid
    if not ws.is_dir():
        raise HTTPException(404, f"会话不存在: {sid}")
    return ws


def _pending(ws) -> dict | None:
    try:
        d = json.loads((ws / ".loadn" / "skill-suggest.json").read_text(
            encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


@router.get("/sessions/{sid}/skill-suggest")
def get_suggest(sid: str):
    """当前待决策建议卡（无 pending → {pending: null}）。"""
    return {"pending": _pending(_ws_of(sid))}


@router.post("/sessions/{sid}/skill-suggest/decide")
def decide_suggest(sid: str, body: dict):
    """确认（写 .agents/skills/ 过扫描）/ 拒绝（负样本 7 天抑制）。"""
    ws = _ws_of(sid)
    card = _pending(ws)
    if card is None:
        raise HTTPException(404, "没有待决策的技能建议")
    accept = bool(body.get("accept"))
    if accept:
        name = str(body.get("name") or card.get("name") or "").strip()
        desc = str(body.get("description") or card.get("description") or "").strip()
        body_text = str(body.get("body") or card.get("body") or "").strip()
        import re as _re
        if not _re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", name or ""):
            raise HTTPException(400, "技能名需匹配 ^[a-z0-9][a-z0-9._-]{0,63}$")
        if not body_text:
            raise HTTPException(400, "正文不能为空")
        skill_md = (f"---\nname: {name}\ndescription: {desc or name}\n"
                     f"origin: user-taught:{card.get('fingerprint')}\n"
                     f"---\n\n{body_text}\n")
        # 复查修#5 双写：①会话工作区 .agents/skills/（本会话立即生效——
        # cwd 即工作区，下轮 discover 直接可见）
        skill_dir = ws / ".agents" / "skills" / name
        if skill_dir.exists():
            raise HTTPException(409, f"已存在同名 skill: {name}")
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        # ②平台技能库（数据根 skills/——跨会话持久：工作区是每会话独立
        # 的，只写①则「下次同类请求自动启用」跨会话不成立；②进管理面
        # 可见可挂载，同名冲突时带 -taught 后缀不覆盖既有技能）
        from ... import skills as platform_skills
        lib_dir = platform_skills._writable_root() / name
        if lib_dir.exists():
            lib_dir = platform_skills._writable_root() / f"{name}-taught"
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
        import json as _json
        (lib_dir / ".loadn-source.json").write_text(_json.dumps({
            "via": "user-taught", "sid": sid,
            "fingerprint": card.get("fingerprint")}, ensure_ascii=False),
            encoding="utf-8")
        # 供应链扫描（W4 八类——用户教学也不豁免；红线拒写即清目录）
        from ...security import skill_scan
        report = skill_scan.scan_skill(skill_dir)
        if report["level"] == "red":
            import shutil
            shutil.rmtree(skill_dir, ignore_errors=True)
            audit("consolidate", {"action": "scan_rejected", "sid": sid,
                                  "name": name})
            raise HTTPException(400, f"扫描红线拒写："
                                     f"{[x['rule'] for x in report['findings'] if x['level'] == 'red'][:3]}")
        consolidate.clear_pending(ws)
        audit("consolidate", {"action": "accepted", "sid": sid, "name": name,
                              "kind": card.get("kind"),
                              "fingerprint": card.get("fingerprint")})
        return {"ok": True, "skill": name, "scanned": report["level"],
                "library": lib_dir.name}
    # 拒绝 → 负样本
    consolidate.reject(str(card.get("fingerprint") or ""))
    consolidate.clear_pending(ws)
    audit("consolidate", {"action": "rejected", "sid": sid,
                          "kind": card.get("kind"),
                          "fingerprint": card.get("fingerprint")})
    return {"ok": True, "rejected": True}


# ---------------------------------------------------------------- draft 转正
@router.post("/memory/entry/{domain}/{eid}/confirm")
def confirm_draft(domain: str, eid: str, body: dict = None):
    """P12 draft 教训转正（去 draft 标记；正文可同请求更新）。"""
    from loadn import memorystore as mstore

    from ...config import PATHS  # noqa: F401
    d = mstore.domain_dir_by_key(domain)
    if d is None:
        raise HTTPException(404, f"记忆域不存在: {domain}")
    entries = mstore.entries_of(d)
    e = next((x for x in entries if x.get("id") == eid), None)
    if e is None:
        raise HTTPException(404, f"条目不存在: {eid}")
    if not e.get("draft"):
        return {"ok": True, "already": True}
    body = body or {}
    content = str(body.get("content") or e.get("content") or "").strip()
    try:
        mstore.edit_entry(d, eid, content=content + "\n" if content else None,
                          event_sink=lambda t, p: audit(
                              "consolidate", {"action": "lesson_confirmed",
                                              "domain": domain, **p}))
    except mstore.MemoryOpError as ex:
        raise HTTPException(400, str(ex)) from None
    # edit 保存即转正：manifest 里 draft 标记随保存移除
    return {"ok": True, "id": eid}
