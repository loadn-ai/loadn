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


def memory_dir(cwd: Path) -> Path:
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


def load_entries(cwd: Path) -> list[dict]:
    return _manifest(memory_dir(cwd)).get("entries") or []


def forget(cwd: Path, keyword: str, *, session=None) -> int:
    """忘掉：summary/content 命中 keyword 的条目删除（返回删除数）。"""
    dir_ = memory_dir(cwd)
    m = _manifest(dir_)
    keep, removed = [], []
    for e in m.get("entries") or []:
        hit = keyword in (e.get("summary") or "") or keyword in e.get(
            "content", "")
        (removed if hit else keep).append(e)
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
             session=None) -> dict | None:
    """写入一条记忆（直写通道与抽取通道同库）。敏感护栏前置。"""
    if canary_hit(summary + "\n" + content):
        _trace(session, "memory_blocked", {"summary": summary[:80]})
        return None
    dir_ = memory_dir(cwd)
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


def render_block(cwd: Path, limit: int = 12) -> str:
    """注入块（zcode recall 同构）：[memory] 标注 + 溯源会话。"""
    entries = load_entries(cwd)[-limit:]
    if not entries:
        return ""
    lines = []
    for e in entries:
        src = (e.get("origin_session") or "?")[:16]
        lines.append(f"- [memory|{src}] {e.get('summary', '')}"
                     f"{'：' + e['content'][:120] if e.get('content') else ''}")
    return "### 项目长期记忆\n" + "\n".join(lines) + "\n" + MEMORY_NOTE


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
