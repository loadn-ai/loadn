"""长期记忆引擎层（P1-4a，Zcode Z3 × codex C7 同构）：边界驱动后台抽取 +
跨会话注入。

存储/双域/git 版本化/管理面操作在顶层 loadn/memorystore.py（进程边界：
webui 禁 import loadn.core，锁与 manifest 协议必须单实现）——本模块是引擎
侧消费者：资格判定、直写/忘掉指令识别、边界锚点推进、small_model 抽取、
注入块渲染；变更留痕走 transcript 事件（_trace）。
"""
from __future__ import annotations

import re
from pathlib import Path

from loadn.memorystore import (  # noqa: F401  再导出：存储真源在顶层，消费方导入路径不变
    MAX_ENTRIES,
    PROJECT_DOMAIN,
    USER_DOMAIN,
    _git,
    advance_boundary,
    boundary_of,
    canary_hit,
    classify_domain,
    commit_domain,
    domain_dir_by_key,
    entries_of,
    forget,
    forget_all,
    list_domain_keys,
    load_entries,
    memory_dir,
    memory_root,
    project_key,
    promote,
    remember,
    user_enabled,
)
from loadn.util import get_logger

log = get_logger(__name__)

MINIMUM_USER_WORDS = 3          # zcode 同构
MEMORY_NOTE = "（以上为平台自动抽取的项目记忆，可能过时；以最近指令为准）"
MEMORY_NOTE_USER = "（以上为平台自动抽取的跨项目用户记忆，可能过时；以最近指令为准）"


def _trace(session, type_: str, payload: dict) -> None:
    """记忆变更留痕（transcript；引擎侧等价审计）。"""
    try:
        if session is not None:
            session.append_event(type_, payload)
    except Exception:                                      # noqa: BLE001
        pass


def _sink(session):
    """memorystore 的 event_sink 适配器（None 安全）。"""
    if session is None:
        return None
    return lambda t, p: _trace(session, t, p)


# ---------------------------------------------------------------- 注入
def _render_domain(cwd: Path, limit: int, domain: str, title: str,
                  tag: str, note: str) -> str:
    entries = load_entries(cwd, domain)[-limit:]
    if not entries:
        return ""
    lines = []
    for e in entries:
        src = (e.get("origin_session") or "?")[:16]
        lines.append(f"- [{tag}|{src}] {e.get('summary', '')}"
                     f"{'：' + e['content'][:120] if e.get('content') else ''}")
    return f"{title}\n" + "\n".join(lines) + "\n" + note


def render_block(cwd: Path, limit: int = 12) -> str:
    """注入块（zcode recall 同构）：user 段 [user-memory] 置于 project 段
    [memory] 之上（身份先于项目）；off 或 user 域空 → 与单域现状一致。"""
    parts = []
    if user_enabled():
        parts.append(_render_domain(cwd, limit, USER_DOMAIN,
                                    "### 用户长期记忆", "user-memory",
                                    MEMORY_NOTE_USER))
    parts.append(_render_domain(cwd, limit, PROJECT_DOMAIN,
                                "### 项目长期记忆", "memory", MEMORY_NOTE))
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------- 抽取钩子（P1-4b）
EXTRACT_PROMPT = """从下面这轮对话新增段里抽取**项目级长期记忆**（用户偏好/
约定/环境事实），供未来会话使用。只抽稳定、可复用的信息，忽略一次性细节。

新增段：
{segment}

只输出 JSON 数组（无则 []）：[{{"summary": "≤20字概括", "content": "具体内容≤200字"}}]"""

# 直写/删除指令形态（当轮跳过自动抽取——用户已显式表达，zcode
# direct-memory-write 同构）
_DIRECT_WRITE_RE = re.compile(r"记住[:：]|请记住|忘掉|忘记[:：]?|以后都", re.I)


def eligible(new_messages: list[dict]) -> tuple[bool, str]:
    """资格判定（zcode 决策 run/skip 同构）。new_messages=[
    {role, content}]。"""
    def _words(t: str) -> int:
        # 中文无空格分词：词数≈max(空格分词, CJK 字符数/2)（zcode 语义的
        # CJK 适配——用户实际表达量，而非字节噪声）
        cjk = sum(1 for ch in t if "一" <= ch <= "鿿")
        return max(len(t.split()), cjk // 2)
    user_words = sum(_words(str(m.get("content") or ""))
                     for m in new_messages if m.get("role") == "user")
    if user_words < MINIMUM_USER_WORDS:
        return False, "no-user-prose"
    return True, ""


def direct_write_intent(user_text: str) -> str | None:
    """识别直写/忘掉指令：返回 'write'/'forget'/None。"""
    t = user_text or ""
    if re.search(r"忘掉|忘记[:：]?", t):
        return "forget"
    if _DIRECT_WRITE_RE.search(t):
        return "write"
    return None


def new_segment_since_boundary(cwd: Path, session) -> tuple[list[dict], str]:
    """boundary 之后的新增消息段（role/content 扁平），末条消息 id 做新锚。"""
    msgs: list[dict] = []
    last_id = ""
    for ev in session.transcript.read_events():
        if ev.get("type") != "user":
            continue
        mid = str(ev.get("uuid") or "")     # 事件 uuid=锚（transcript.append）
        if mid:
            last_id = mid
        payload = ev.get("payload") or {}
        content = payload.get("content") or ""
        if isinstance(content, list):
            content = " ".join(str(c.get("text", "")) for c in content
                               if isinstance(c, dict))
        msgs.append({"role": "user", "content": str(content),
                     "_id": mid})
    b = boundary_of(cwd)
    out: list[dict] = []
    seen_boundary = not b            # 无锚=全部（首轮）
    for m in msgs:
        if not seen_boundary:
            if m["_id"] == b:
                seen_boundary = True
            continue
        out.append({"role": m["role"], "content": m["content"]})
        if m["_id"]:
            last_id = m["_id"]
    return out, last_id


async def extract_and_store(provider, cwd: Path, session, *,
                            small_model: str | None = None) -> int:
    """turn 成功后的后台抽取（P1-4b 主入口）。返回写入条数（0=skip/无收获）。

    流程：新增段 → 资格（no-user-prose skip）→ 直写指令轮 skip（显式表达
    优先）→ small_model 抽取（JSON 数组）→ 蜜罐护栏逐条 → 入库 → 边界
    推进。任何失败静默（后台任务不炸主循环）。
    """
    try:
        sink = _sink(session)
        segment, last_id = new_segment_since_boundary(cwd, session)
        if not segment or not last_id:
            return 0
        ok, why = eligible(segment)
        if not ok:
            _trace(session, "memory_extract_skipped", {"reason": why})
            return 0
        user_tail = segment[-1].get("content") or ""
        intent = direct_write_intent(user_tail)
        if intent == "write":
            # 直写：把指令语句本身入库（去指令词），本轮不再自动抽取
            content = re.sub(r"^(请)?记住[:：]?", "", user_tail).strip()
            if content and not canary_hit(content):
                remember(cwd, content[:20], content,
                         origin_session=getattr(session, "session_id", "?"),
                         event_sink=sink,
                         domain=classify_domain(content))
            _advance(session, cwd, last_id)
            return 1
        if intent == "forget":
            kw = re.sub(r"忘掉|忘记[:：]?", "", user_tail).strip()
            if kw:
                forget_all(cwd, kw, event_sink=sink)
            _advance(session, cwd, last_id)
            return 0
        # 自动抽取：small_model JSON 数组
        from loadn.types import Message as _Msg
        from loadn.types import TextBlock as _TB
        prompt = EXTRACT_PROMPT.format(
            segment="\n".join(m["content"] for m in segment)[-8000:])
        text = ""
        async for c in provider.chat(
                [_Msg(role="user", content=[_TB(text=prompt)])], [],
                "你是记忆抽取器，只输出 JSON 数组本身。",
                model=small_model, use_cache=False):
            if c.kind == "text_delta":
                text += c.text
        import json as _json
        try:
            items = _json.loads(text.strip().removeprefix("```json")
                                .removeprefix("```").removesuffix("```").strip())
        except ValueError:
            items = []
        written = 0
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            summary = str(it.get("summary") or "")[:40].strip()
            content = str(it.get("content") or "")[:2000].strip()
            if not summary or not content:
                continue
            if remember(cwd, summary, content,
                        origin_session=getattr(session, "session_id", "?"),
                        event_sink=sink,
                        domain=classify_domain(summary + " " + content)):
                written += 1
        _advance(session, cwd, last_id)
        return written
    except Exception as e:                                  # noqa: BLE001
        log.warning("记忆抽取失败（不影响主循环）：%s", e)
        return 0


def _advance(session, cwd: Path, last_id: str) -> None:
    advance_boundary(cwd, last_id)
    _trace(session, "memory_boundary", {"message_id": last_id})
