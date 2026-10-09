"""会话内子任务自动分类打标（rev10 概念模型：项目 ⊃ 会话=任务 ⊃ 子任务）。

每轮对话（turn）在 submit 时后台分类：
- 应答形态（qa）：追问/确认/闲聊——不打标，留在主控合集；
- 任务形态（task）：有交付物的请求——能并入已有子任务则打其标签，
  否则建新子任务（≤12 字标题）。

复用 titlegen 全套（_chat/_sanitize/CONFIG 开关）——零新配置面；失败静默
不打标，绝不阻塞 turn。产物/agent 归子任务经 turn join 派生（无新关联列）。
"""
from __future__ import annotations

import asyncio
import json
import re

from ..config import CONFIG
from ..util import get_logger

log = get_logger(__name__)

_STEER_PREFIX = "（运行中插话，转发处理）"      # _finish/_salvage re-submit 前缀
_MAX_TITLE = 12

_SYSTEM = (
    "你是任务流分类器。判断这条用户消息的形态并给出决策 JSON：\n"
    "- 应答形态（qa）：追问/确认/闲聊/改写要求——只期待返回内容，没有独立交付物；\n"
    "- 任务形态（task）：有交付物的请求（调研报告/写代码/建项目/数据处理…）。\n"
    "任务形态时判断能否并入已有子任务（同一件事的延续、追问式补充→给其 id；"
    "新的独立交付物→给 new_title，不超过 12 个字，不要书名号/引号）。\n"
    '只输出 JSON：{"type":"qa"} 或 {"type":"task","subtask_id":<int>} '
    '或 {"type":"task","new_title":"..."}'
)

# 会话 turn 串行 ≠ 分类串行（submit 时点火，运行中再发消息会有两个分类 task
# 并存）——读列表→LLM→写库窗口必须整体持锁，否则同会话双建子任务。
# 锁按 sid 常驻（量=会话数，进程生命周期内可接受）
_LOCKS: dict[str, asyncio.Lock] = {}


def _lock(sid: str) -> asyncio.Lock:
    lk = _LOCKS.get(sid)
    if lk is None:
        lk = asyncio.Lock()
        _LOCKS[sid] = lk
    return lk


def _clean_text(user_text: str) -> str:
    text = (user_text or "").strip()
    if text.startswith(_STEER_PREFIX):          # 插话 re-submit：剥前缀再分类
        text = text[len(_STEER_PREFIX):].strip()
    return text


def _parse_decision(raw: str, valid_ids: set[int]) -> dict | None:
    """LLM 回复 → {type, subtask_id?, new_title?}；任何不合法都返回 None（不打标）。

    防御：``` 围栏 / 前后噪声（取首 { 到末 }）/ type 非法 / subtask_id 幻觉
    （不在现有列表）/ new_title 空。
    """
    t = (raw or "").strip()
    if not t:
        return None
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or d.get("type") not in ("qa", "task"):
        return None
    if d["type"] == "qa":
        return {"type": "qa"}
    sid = d.get("subtask_id")
    title = d.get("new_title")
    if isinstance(sid, int) and sid in valid_ids:
        return {"type": "task", "subtask_id": sid}
    if isinstance(title, str):
        from .titlegen import _sanitize
        cleaned = _sanitize(title)[:_MAX_TITLE]
        if cleaned:
            return {"type": "task", "new_title": cleaned}
    return None


def _user_prompt(text: str, subtasks: list) -> str:
    lines = [f"用户消息：\n{text[:1500]}", ""]
    if subtasks:
        lines.append("已有子任务：")
        lines.extend(f"- id={r['id']}：{r['title']}" for r in subtasks)
    else:
        lines.append("（尚无子任务）")
    lines.append("")
    lines.append("给出决策 JSON。")
    return "\n".join(lines)


async def _decide(sid: str, user_text: str) -> tuple[dict | None, list]:
    """守卫 + 读现有子任务 + LLM 决策（不写库）。返回 (decision, subtasks)。"""
    cfg = CONFIG.titlegen
    text = _clean_text(user_text)
    if not (cfg.enabled and cfg.api_key and text):
        return None, []
    from .. import db as db_mod
    from . import titlegen

    with db_mod.conn() as c:
        cur = db_mod.list_subtasks(c, sid)
        valid_ids = {r["id"] for r in cur}
    try:
        raw = await titlegen._chat(_SYSTEM, _user_prompt(text, cur),
                                   max_tokens=200)
    except Exception:                            # noqa: BLE001 —— 分类失败不影响任务
        log.info("子任务分类调用失败 sid=%s（静默不打标）", sid)
        return None, cur
    return _parse_decision(raw, valid_ids), cur


async def maybe_tag(sid: str, tid: int, user_text: str, *,
                    publish: bool = True) -> int | None:
    """给 turn 打子任务标签（fire-and-forget 入口，engine.submit 点火）。

    返回 subtask_id（qa/失败/竞态 → None）。守卫与 titlegen 同构：未配置
    直接返回，零 LLM 调用。
    """
    if not _clean_text(user_text):
        return None
    async with _lock(sid):
        decision, _ = await _decide(sid, user_text)
        if decision is None or decision["type"] == "qa":
            return None
        from .. import db as db_mod

        # 写库一个事务；publish 必须在事务外（它另开连接写 session_events，
        # 嵌在本事务内=database locked）。竞态防御：新建行前先验 turn 存在，
        # UPDATE rowcount==0 且本事务新建过行 → 事务内回滚删除（不留悬挂）
        snapshot: list | None = None
        with db_mod.conn() as c:
            if db_mod.get_turn(c, tid) is None:
                return None                       # turn 已被撤回（先验）
            created_here = False
            if decision.get("subtask_id") is not None:
                stid = decision["subtask_id"]
            else:
                # 同名复用（LLM 复用决策之外的第二道闸；backfill 重跑幂等）
                hit = db_mod.find_subtask_by_title(c, sid, decision["new_title"])
                if hit:
                    stid = hit["id"]
                else:
                    stid = db_mod.create_subtask(c, sid, decision["new_title"])
                    created_here = True
            if db_mod.update_turn(c, tid, subtask_id=stid) == 0:
                if created_here:                  # 撤回窗口命中：回滚本事务新建行
                    c.execute("DELETE FROM subtasks WHERE id=?", (stid,))
                return None
            if publish:
                snapshot = [db_mod.to_dict(r) for r in
                            db_mod.list_subtasks(c, sid)]
        if publish and snapshot is not None:
            from ..engine import ENGINE
            ENGINE.publish(sid, "subtask", {
                "turn_id": tid, "subtask_id": stid,
                "subtasks": snapshot}, tid)
        log.info("子任务打标 sid=%s tid=%s → %s", sid, tid, stid)
        return stid


async def backfill(sid: str, *, dry_run: bool = False,
                   limit: int = 0) -> dict:
    """存量 turn 补打标（CLI：按时间序，subtasks 增量演进，不 publish）。

    dry_run 只打印决策不写库；limit 0=全部。返回统计
    {scanned, tagged, created, merged, qa, failed}。
    """
    from .. import db as db_mod

    stats = {"scanned": 0, "tagged": 0, "created": 0, "merged": 0,
             "qa": 0, "failed": 0}
    with db_mod.conn() as c:
        if db_mod.get_session(c, sid) is None:
            raise KeyError(f"session not found: {sid}")
        tids = [r["id"] for r in c.execute(
            "SELECT id FROM turns WHERE session_id=? AND subtask_id IS NULL"
            " ORDER BY id", (sid,))]
        texts: dict[int, str] = {}
        for r in c.execute(
                "SELECT turn_id, content FROM messages WHERE session_id=?"
                " AND role='user' ORDER BY id", (sid,)):
            texts.setdefault(r["turn_id"], r["content"])
    if limit > 0:
        tids = tids[:limit]

    def _n_subtasks() -> int:
        with db_mod.conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM subtasks"
                             " WHERE session_id=?", (sid,)).fetchone()["n"]

    for tid in tids:
        stats["scanned"] += 1
        text = (texts.get(tid) or "").strip()
        if not text:
            continue
        if dry_run:
            decision, cur = await _decide(sid, text)
            if decision is None:
                stats["qa"] += 1
                print(f"  turn {tid} → 未打标（qa/失败）")
            elif decision["type"] == "qa":
                stats["qa"] += 1
                print(f"  turn {tid} → qa（留在主控）")
            elif decision.get("subtask_id") is not None:
                stats["merged"] += 1
                title = next((r["title"] for r in cur
                              if r["id"] == decision["subtask_id"]), "?")
                print(f"  turn {tid} → 并入 #{decision['subtask_id']} {title}")
            else:
                stats["created"] += 1
                print(f"  turn {tid} → 新建子任务「{decision['new_title']}」")
            continue
        before = _n_subtasks()
        stid = await maybe_tag(sid, tid, text, publish=False)
        if stid is None:
            # qa / 调用失败 / 竞态——maybe_tag 静默聚合为「未打标」，
            # 重跑 backfill 会再试（幂等）
            stats["qa"] += 1
            continue
        stats["tagged"] += 1
        if _n_subtasks() > before:
            stats["created"] += 1
        else:
            stats["merged"] += 1
    stats["failed"] = 0                          # 失败并入 qa 计数（见上）
    return stats
