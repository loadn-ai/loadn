"""P12 经验→技能固化闭环（纠错检测 / 建议卡 / 压缩后反思）。

顶层模块（memorystore 先例：引擎 loop 与 webui 决策面共用，进程边界禁
webui 入 loadn.core）。

- **纠错信号检测（确定性优先，禁模型猜常开）**：两类词表命中才触发——
  显式教学（以后都/记住要/always/never…）与否定纠错（不对/错了/重做…）。
  防自激：每会话 ≤2 次（skill_suggest transcript 事件计数）。
- **建议卡**：命中 → 写会话工作区 .loadn/skill-suggest.json（pending，含
  预填技能名/描述/正文=用户原话+上轮 assistant 尾部摘要，不改写语义）；
  webui 卡片可编辑，确认 → 写项目 .agents/skills/（过 skill_scan 八类扫描）
  ；拒绝 → 负样本（同类指纹 7 天抑制，存 $LOADN_HOME/memory/
  skill-suggest.json）。
- **压缩后反思（默认 off，env LOADN_REFLECT_AFTER_COMPACT=on）**：cheap
  provider 读压缩摘要 → ≤3 条教训候选 → 写项目记忆域 draft 条目（待确认
  ——经 P6 记忆页人工转正，**绝不自动落盘为正式条目**）。反思不触发纠错
  检测（防自激）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)

MAX_PER_SESSION = 2
REJECT_COOLDOWN_S = 7 * 86400        # 负样本同类抑制 7 天

# 显式教学（用户在「教」agent 一个持续规则）
TEACH_RE = re.compile(
    r"以后都|以后请|以后别|以后要|记住要|记住[:：]|今后一律|从今往后|不要再"
    r"|always\s|never\s|from now on", re.I)
# 否定纠错（对上轮行为的明确否定/要求重做——须是明确否定词，拿不准不触发）
CORRECT_RE = re.compile(
    r"不对|错了|搞错|弄错|不是这样|不是我要的|重来|重做|撤销.{0,6}重|别这样")


def detect_correction(text: str) -> str | None:
    """"teach"（显式教学）/"correct"（否定纠错）/None（普通对话不触发）。"""
    t = (text or "").strip()
    if not t or len(t) > 2000:
        return None
    if TEACH_RE.search(t):
        return "teach"
    if CORRECT_RE.search(t):
        return "correct"
    return None


def fingerprint(text: str) -> str:
    """同类指纹（归一化：去空白/标点后 sha1 前 12 位——拒绝抑制的匹配键）。"""
    norm = re.sub(r"[\s，。,\.!！?？;；:：'\"]+", "", (text or "").lower())
    import hashlib
    return hashlib.sha1(norm.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- 负样本抑制
def _store_path() -> Path:
    return loadn_home() / "memory" / "skill-suggest.json"


def _load_store() -> dict:
    try:
        d = json.loads(_store_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def is_rejected(fp: str) -> bool:
    until = (_load_store().get("rejected") or {}).get(fp)
    return bool(until and time.time() < float(until))


def reject(fp: str) -> None:
    """负样本：同类指纹 7 天内不再触发。"""
    st = _load_store()
    rej = st.setdefault("rejected", {})
    rej[fp] = time.time() + REJECT_COOLDOWN_S
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 建议生成
def suggest_count(session) -> int:
    """本会话已触发次数（skill_suggest transcript 事件计数，≤2 防刷）。"""
    n = 0
    try:
        for ev in session.transcript.read_events():
            if ev.get("type") == "skill_suggest":
                n += 1
    except Exception:                                  # noqa: BLE001
        pass
    return n


def pending_path(cwd: Path) -> Path:
    return Path(cwd) / ".loadn" / "skill-suggest.json"


def clear_pending(cwd: Path) -> None:
    try:
        pending_path(cwd).unlink(missing_ok=True)
    except OSError:
        pass


def _slug(kind: str, text: str) -> str:
    """预填技能名：教学类取关键词，纠错类取「avoid-…」；合法化到 skill 名。"""
    words = re.findall(r"[\w一-鿿]{2,8}", text)[:3]
    base = ("-".join(w.lower() for w in words) if words else "lesson")[:40]
    name = re.sub(r"[^a-z0-9._-]", "", base) or "lesson"
    return f"{name}" if kind == "teach" else f"avoid-{name}"


def maybe_suggest(cwd: Path, session) -> dict | None:
    """turn 成功后调用：命中才产出建议卡（写 pending 文件 + transcript 事件）。

    正文=用户原话（不改写语义）+ 上轮 assistant 尾部 200 字摘要（上下文）。
    返回建议 dict 或 None。同步函数（transcript 读写均为本地 IO）。
    """
    try:
        if pending_path(cwd).exists():
            return None                     # 已有未决策卡：不覆盖（单槽保护）
        users, assistants = [], []
        for ev in session.transcript.read_events():
            if ev.get("type") == "user":
                c = (ev.get("payload") or {}).get("content")
                if isinstance(c, list):
                    # 复查修#2：tool_result 块不是人话——只取真人 text 块；
                    # 整条是 tool_result（无 text）的 user 事件跳过，否则
                    # 带工具的 turn 里 users[-1] 恒为 tool_result → 检测失明
                    texts = [str(b.get("text") or "") for b in c
                             if isinstance(b, dict) and b.get("type") == "text"]
                    if not texts:
                        continue
                    c = " ".join(t for t in texts if t.strip())
                users.append(str(c or ""))
            elif ev.get("type") == "assistant":
                c = (ev.get("payload") or {}).get("content")
                if isinstance(c, list):
                    c = " ".join(str(b.get("text", "")) for b in c
                                 if isinstance(b, dict))
                assistants.append(str(c or ""))
        if not users:
            return None
        last_user = users[-1]
        kind = detect_correction(last_user)
        if kind is None:
            return None
        fp = fingerprint(last_user)
        if is_rejected(fp):
            return None
        if suggest_count(session) >= MAX_PER_SESSION:
            return None
        prev_tail = (assistants[-1] or "")[-200:] if assistants else ""
        body = (f"# 用户教导（{kind}）\n\n> {last_user}\n\n"
                f"## 上一轮行为（被纠正的上下文）\n\n{prev_tail}\n\n"
                f"## 应遵循的规则\n\n（按用户原话执行）{last_user}")
        card = {"kind": kind, "fingerprint": fp,
                "name": _slug(kind, last_user),
                "description": f"用户{ '教导' if kind == 'teach' else '纠正'}的规则",
                "body": body,
                "origin_session": getattr(session, "session_id", ""),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        pp = pending_path(cwd)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text(json.dumps(card, ensure_ascii=False, indent=1),
                      encoding="utf-8")
        session.append_event("skill_suggest",
                             {"name": card["name"], "kind": kind,
                              "fingerprint": fp})
        return card
    except Exception as e:                              # noqa: BLE001
        log.warning("纠错检测失败（不影响主循环）：%s", e)
        return None


# ---------------------------------------------------------------- 压缩后反思
REFLECT_PROMPT = """读下面的会话压缩摘要，抽取**操作教训**（下次同类任务应怎么做，
不是事实记忆）。最多 3 条，只输出 JSON 数组（无则 []）：
[{{"summary": "触发场景 ≤20字", "content": "应做行为 ≤120字"}}]

压缩摘要：
{summary}"""


def reflection_enabled() -> bool:
    import os
    return os.environ.get("LOADN_REFLECT_AFTER_COMPACT", "off").strip().lower() \
        in ("on", "1", "true", "yes")


async def reflect_after_compact(provider, cwd: Path, session,
                                summary_text: str, *,
                                small_model: str | None = None) -> int:
    """压缩完成后：cheap provider 读摘要 → ≤3 条教训 → 项目记忆域 draft 条目。

    draft 条目带 draft=true 标记（P6 记忆页角标+人工转正）；**绝不自动转正**。
    反思产物不触发纠错检测（防自激——本函数不调用 maybe_suggest）。"""
    try:
        from loadn.types import Message as _Msg
        from loadn.types import TextBlock as _TB
        text = ""
        prompt = REFLECT_PROMPT.format(summary=(summary_text or "")[-8000:])
        async for c in provider.chat(
                [_Msg(role="user", content=[_TB(text=prompt)])], [],
                "你是操作教训抽取器，只输出 JSON 数组本身。",
                model=small_model, use_cache=False):
            if c.kind == "text_delta":
                text += c.text
        import json as _json
        try:
            items = _json.loads(text.strip().removeprefix("```json")
                                .removeprefix("```").removesuffix("```").strip())
        except ValueError:
            items = 0
        written = 0
        sid = getattr(session, "session_id", "?")
        from loadn import memorystore as ms
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or written >= 3:
                continue
            summary = str(it.get("summary") or "")[:40].strip()
            content = str(it.get("content") or "")[:2000].strip()
            if not summary or not content:
                continue
            e = ms.remember(cwd, summary, content, origin_session=sid,
                            reason="inferred")
            if e is not None:
                # draft 标记：manifest 原位补 draft=true（P6 角标；编辑转正即除）
                with ms._domain_lock(ms.memory_dir(cwd)) as ok:
                    if ok:
                        m = ms._manifest(ms.memory_dir(cwd))
                        for row in m.get("entries") or []:
                            if row.get("id") == e["id"]:
                                row["draft"] = True
                        ms._save_manifest(ms.memory_dir(cwd), m)
                session.append_event("lesson_draft",
                                     {"id": e["id"], "summary": summary})
                written += 1
        return written
    except Exception as e:                              # noqa: BLE001
        log.warning("压缩后反思失败（不影响主循环）：%s", e)
        return 0
