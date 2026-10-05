"""长期记忆（P1-4a，ZCode Z3 × codex C7 同构）：边界驱动后台抽取 +
跨会话注入。

- 存储：$LOADN_HOME/memory/<project>/<hash>.md + manifest.json
  {entries:[{id,file,summary,origin_session,created_at}], boundary:{message_id}}。
  project=git 根路径的 sha1 短哈希（无 git 根=cwd 绝对路径哈希——记忆仍按
  工作区隔离，不跨项目泄漏）。
- 边界驱动（zcode MemoryExtractionSnapshot 同构）：manifest.boundary 记
  上次处理到的消息 uuid，只抽新增段；跳过理由记录
  （direct-memory-write / no-user-prose）。
- 资格规则（codex 同构）：用户正文 <MINIMUM_USER_WORDS 词 → skip；子代理/
  辅助请求（use_cache=False 侧道）不触发；直写通道命中当轮 → 该轮 skip。
- **敏感护栏（先于一切）**：抽取输入先过 canary 蜜罐检测（诱饵 token/
  假凭证出现即熔断不存）+ 凭证形态过滤（api key 长串/邮箱密码对等）；
  记忆变更 transcript 留痕（引擎侧等价审计——进程边界无 webui audit）。
- 上限：MAX_ENTRIES 条 LRU（最旧淘汰）；"忘掉 X" 显式删除。
- 注入（P1-4a 接线位）：context.py 宪法块之后、既有 MEMORY.md 段之前，
  带 [memory] 来源标注 + 溯源会话 id。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)

MINIMUM_USER_WORDS = 3          # zcode 同构
MAX_ENTRIES = 200
MEMORY_NOTE = "（以上为平台自动抽取的项目记忆，可能过时；以最近指令为准）"
MEMORY_NOTE_USER = "（以上为平台自动抽取的跨项目用户记忆，可能过时；以最近指令为准）"

# ---------------------------------------------------------------- P4 双域
PROJECT_DOMAIN = "project"
USER_DOMAIN = "user"
USER_DIR_NAME = "_user"          # $LOADN_HOME/memory/_user/（下划线前缀避让 hex 域键）


def user_enabled() -> bool:
    """LOADN_USER_MEMORY=off → 用户域整体关闭（默认 on；off=单域现状逐字节一致）。"""
    return (os.environ.get("LOADN_USER_MEMORY", "on").strip().lower()
            not in ("off", "0", "false", "no"))


# 归属判定词表（确定性启发式，禁模型猜）：
#   user 侧=第一人称偏好指称；project 侧=路径/文件名/import·包管理形态指称。
#   user-domain-words.txt（$LOADN_HOME/memory/，一行一正则，# 注释）可扩 user 侧。
_USER_MARKS_DEFAULT = (
    "我喜欢", "我讨厌", "我偏好", "我习惯", "我总是", "我爱用", "我的",
    "I prefer", "I like", "I hate", "I always", "I use", "my favorite",
)
_PROJECT_HINT_RE = re.compile(
    r"[\w.-]+/[\w.-]+"                       # 路径形态：src/x.py、~/.zshrc、a/b
    r"|\.(?:py|ts|tsx|js|jsx|go|rs|java|c|cpp|h|md|json|ya?ml|toml|sql|sh|cfg|ini)\b"
    r"|(?:import\s+\w+|from\s+\w+\s+import"
    r"|pip\s+install|npm\s+(?:i|install)|cargo\s+(?:add|install))", re.I)


def _user_mark_re() -> re.Pattern:
    """默认词表 + 配置文件扩充（读失败回落默认；抽取频度低，直接读不缓存）。"""
    words = list(_USER_MARKS_DEFAULT)
    try:
        for ln in (loadn_home() / "memory" / "user-domain-words.txt").read_text(
                encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                words.append(ln)
    except OSError:
        pass
    return re.compile("|".join(re.escape(w) for w in words), re.I)


def classify_domain(text: str) -> str:
    """归属判定（确定性）：第一人称偏好指称 且 无项目指称 → user；
    其余（含拿不准）→ project（宁保守）。开关 off → 恒 project。"""
    if not user_enabled():
        return PROJECT_DOMAIN
    if _PROJECT_HINT_RE.search(text or ""):
        return PROJECT_DOMAIN
    return USER_DOMAIN if _user_mark_re().search(text or "") else PROJECT_DOMAIN

# 蜜罐诱饵（与 webui canary 同族——引擎侧守抽取面）
_CANARY_RE = re.compile(r"canary|诱饵|honeypot|sk-[a-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}"
                        r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                        re.I)
# 凭证/密钥形态（命中即拒存该候选）
_SECRET_RE = re.compile(r"password\s*[:=]\s*\S|api[_-]?key\s*[:=]\s*\S{8,}"
                        r"|bearer\s+[a-z0-9._-]{20,}", re.I)


def project_key(cwd: Path) -> str:
    """记忆域键：git 根优先（git rev-parse --show-toplevel），否则 cwd。"""
    import subprocess
    root = str(cwd)
    try:
        out = subprocess.run(["git", "-C", str(cwd), "rev-parse",
                              "--show-toplevel"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            root = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return hashlib.sha1(root.encode()).hexdigest()[:12]


def memory_dir(cwd: Path, domain: str = PROJECT_DOMAIN) -> Path:
    """域目录：user 域忽略 cwd（全局唯一 _user/）；project 域按 git 根隔离。"""
    if domain == USER_DOMAIN:
        return loadn_home() / "memory" / USER_DIR_NAME
    return loadn_home() / "memory" / project_key(cwd)


def _manifest(dir_: Path) -> dict:
    try:
        d = json.loads((dir_ / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"entries": [], "boundary": {}}


def _save_manifest(dir_: Path, m: dict) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    tmp = dir_ / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, dir_ / "manifest.json")


def canary_hit(text: str) -> bool:
    """蜜罐/凭证形态命中（护栏第 1 道：熔断不存）。"""
    return bool(_CANARY_RE.search(text or "") or _SECRET_RE.search(text or ""))


def eligible(new_messages: list[dict]) -> tuple[bool, str]:
    """资格判定（zcode 决策 run/skip 同构）。new_messages=[
    {role, content}]。"""
    def _words(t: str) -> int:
        # 中文无空格分词：词数≈max(空格分词, CJK 字符数/2)（zcode 语义的
        # CJK 适配——用户实际表达量，而非字节噪声）
        cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
        return max(len(t.split()), cjk // 2)
    user_words = sum(_words(str(m.get("content") or ""))
                     for m in new_messages if m.get("role") == "user")
    if user_words < MINIMUM_USER_WORDS:
        return False, "no-user-prose"
    return True, ""


def load_entries(cwd: Path, domain: str = PROJECT_DOMAIN) -> list[dict]:
    return _manifest(memory_dir(cwd, domain)).get("entries") or []


def forget(cwd: Path, keyword: str, *, session=None,
          domain: str = PROJECT_DOMAIN) -> int:
    """忘掉：summary/content 命中 keyword 的条目删除（返回删除数；单域）。"""
    if domain == USER_DOMAIN and not user_enabled():
        return 0                         # off：用户域零读写
    dir_ = memory_dir(cwd, domain)
    m = _manifest(dir_)
    keep, removed = [], []
    # 词级匹配：指令剥词后语序常与库内相反（「忘掉 pytest 偏好」vs 摘要
    # 「偏好 pytest」）——按 token 全命中判定，比整串子串稳
    tokens = [t for t in re.split(r"[\s，。,]+", keyword) if t]

    def _hit(e: dict) -> bool:
        hay = (e.get("summary") or "") + " " + (e.get("content") or "")
        if not tokens:
            return False
        return all(t in hay for t in tokens)
    for e in m.get("entries") or []:
        (removed if _hit(e) else keep).append(e)
    if removed:
        for e in removed:
            try:
                (dir_ / e["file"]).unlink(missing_ok=True)
            except OSError:
                pass
        m["entries"] = keep
        _save_manifest(dir_, m)
        _trace(session, "memory_forgotten",
               {"keyword": keyword, "removed": [e["id"] for e in removed]})
    return len(removed)


def remember(cwd: Path, summary: str, content: str, *, origin_session: str,
             session=None, domain: str = PROJECT_DOMAIN) -> dict | None:
    """写入一条记忆（直写通道与抽取通道同库；两域同守护栏）。敏感护栏前置。"""
    if domain == USER_DOMAIN and not user_enabled():
        return None                      # off：用户域零读写（显式面也拒）
    if canary_hit(summary + "\n" + content):
        _trace(session, "memory_blocked", {"summary": summary[:80]})
        return None
    dir_ = memory_dir(cwd, domain)
    m = _manifest(dir_)
    eid = uuid.uuid4().hex[:12]
    fname = f"{eid}.md"
    (dir_).mkdir(parents=True, exist_ok=True)
    (dir_ / fname).write_text(
        f"---\nid: {eid}\nsummary: {summary}\norigin_session: {origin_session}\n"
        f"created_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n---\n\n{content}\n",
        encoding="utf-8")
    m.setdefault("entries", []).append(
        {"id": eid, "file": fname, "summary": summary,
         "content": content[:2000], "origin_session": origin_session,
         "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    # LRU 上限：最旧淘汰
    if len(m["entries"]) > MAX_ENTRIES:
        for old in m["entries"][:len(m["entries"]) - MAX_ENTRIES]:
            try:
                (dir_ / old["file"]).unlink(missing_ok=True)
            except OSError:
                pass
        m["entries"] = m["entries"][-MAX_ENTRIES:]
    _save_manifest(dir_, m)
    _trace(session, "memory_written",
           {"id": eid, "summary": summary[:80], "origin": origin_session})
    return m["entries"][-1]


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


def forget_all(cwd: Path, keyword: str, *, session=None) -> int:
    """"忘掉 X"两域生效：project 全查 + user（开关开时）。返回总删除数。"""
    n = forget(cwd, keyword, session=session)
    if user_enabled():
        n += forget(cwd, keyword, session=session, domain=USER_DOMAIN)
    return n


def _remove_entry(dir_: Path, m: dict, eid: str) -> dict | None:
    """按 id 从 manifest 摘除并删文件（promote 用）。"""
    for i, e in enumerate(m.get("entries") or []):
        if e.get("id") == eid:
            m["entries"] = (m.get("entries") or [])[:i] + \
                (m.get("entries") or [])[i + 1:]
            try:
                (dir_ / e["file"]).unlink(missing_ok=True)
            except OSError:
                pass
            return e
    return None


def promote(cwd: Path, eid: str, *, session=None) -> dict | None:
    """手动提升：项目域条目 → 用户域（原域删除，溯源保留）。CLI 面。"""
    if not user_enabled():
        log.warning("promote 需要 LOADN_USER_MEMORY=on")
        return None
    dir_ = memory_dir(cwd, PROJECT_DOMAIN)
    m = _manifest(dir_)
    e = _remove_entry(dir_, m, eid)
    if e is None:
        return None
    _save_manifest(dir_, m)
    out = remember(cwd, e.get("summary") or eid, e.get("content") or "",
                   origin_session=e.get("origin_session") or "?",
                   session=session, domain=USER_DOMAIN)
    _trace(session, "memory_promoted", {"id": eid})
    return out


def boundary_of(cwd: Path) -> str:
    return str(_manifest(memory_dir(cwd)).get("boundary", {}).get(
        "message_id") or "")


def advance_boundary(cwd: Path, message_id: str) -> None:
    dir_ = memory_dir(cwd)
    m = _manifest(dir_)
    m["boundary"] = {"message_id": message_id}
    _save_manifest(dir_, m)


def _trace(session, type_: str, payload: dict) -> None:
    """记忆变更留痕（transcript；引擎侧等价审计）。"""
    try:
        if session is not None:
            session.append_event(type_, payload)
    except Exception:                                      # noqa: BLE001
        pass


# ---------------------------------------------------------------- 抽取钩子（P1-4b）
EXTRACT_PROMPT = """从下面这轮对话新增段里抽取**项目级长期记忆**（用户偏好/
约定/环境事实），供未来会话使用。只抽稳定、可复用的信息，忽略一次性细节。

新增段：
{segment}

只输出 JSON 数组（无则 []）：[{{"summary": "≤20字概括", "content": "具体内容≤200字"}}]"""

# 直写/删除指令形态（当轮跳过自动抽取——用户已显式表达，zcode
# direct-memory-write 同构）
_DIRECT_WRITE_RE = re.compile(r"记住[:：]|请记住|忘掉|忘记[:：]?|以后都", re.I)


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
                         session=session,
                         domain=classify_domain(content))
            _advance(session, cwd, last_id)
            return 1
        if intent == "forget":
            kw = re.sub(r"忘掉|忘记[:：]?", "", user_tail).strip()
            if kw:
                forget_all(cwd, kw, session=session)
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
                        session=session,
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
