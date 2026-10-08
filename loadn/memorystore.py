"""记忆存储单一真相（P1-4a 存储 / P4 双域 / P5 git 版本化 / P6 管理面）。

顶层模块：引擎 core（抽取/注入）、CLI（memory promote/log/restore）、平台
webui（记忆管理页）三方共用——进程边界禁 webui import loadn.core，而
manifest 读改写与域锁协议必须单实现（两套实现=并发写必坏，P5 的 20 并发
实测教训）。抽取管线/注入渲染在 loadn/core/memory.py（引擎侧消费者）。

- 存储与域：$LOADN_HOME/memory/<domain>/<hash>.md + manifest.json
  {entries, boundary}；project 域键=git 根 sha1[:12]，user 域=_user/。
- 敏感护栏（先于一切写入）：canary 蜜罐 + 凭证形态（两域、所有通道同守）。
- git 版本化（MemFS 语义）：一次 commit=一次「记住」；本地仓永不触网络；
  跨进程 flock 域锁包住 manifest 读改写+LRU+提交；锁超时=文件照写、commit
  下趟 add -A 补。
- 变更留痕：event_sink 回调（引擎传 transcript 事件，webui 传审计账本，
  CLI 不传）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from loadn import loadn_home
from loadn.util import get_logger

log = get_logger(__name__)

MAX_ENTRIES = 200

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
#   user-domain-words.txt（$LOADN_HOME/memory/，一行一词，# 注释）可扩 user 侧。
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


# 蜜罐诱饵（与 webui canary 同族——两域所有写入通道同守）
_CANARY_RE = re.compile(r"canary|诱饵|honeypot|sk-[a-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}"
                        r"|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----",
                        re.I)
# 凭证/密钥形态（命中即拒存该候选）
_SECRET_RE = re.compile(r"password\s*[:=]\s*\S|api[_-]?key\s*[:=]\s*\S{8,}"
                        r"|bearer\s+[a-z0-9._-]{20,}", re.I)


def canary_hit(text: str) -> bool:
    """蜜罐/凭证形态命中（护栏第 1 道：熔断不存）。"""
    return bool(_CANARY_RE.search(text or "") or _SECRET_RE.search(text or ""))


# ---------------------------------------------------------------- 域目录
def project_key(cwd: Path) -> str:
    """记忆域键：git 根优先（git rev-parse --show-toplevel），否则 cwd。"""
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


_PROJECT_KEY_CACHE: dict = {}       # workspace 路径 → hex（git 根探测贵）


def cached_project_key(workspace: str) -> str:
    k = _PROJECT_KEY_CACHE.get(workspace)
    if k is None:
        from pathlib import Path as _P
        k = project_key(_P(workspace))
        _PROJECT_KEY_CACHE[workspace] = k
    return k


def memory_root() -> Path:
    return loadn_home() / "memory"


def memory_dir(cwd: Path, domain: str = PROJECT_DOMAIN) -> Path:
    """域目录：user 域忽略 cwd（全局唯一 _user/）；project 域按 git 根隔离。"""
    if domain == USER_DOMAIN:
        return memory_root() / USER_DIR_NAME
    return memory_root() / project_key(cwd)


def domain_dir_by_key(key: str, *, create: bool = False) -> Path | None:
    """管理面按键寻域目录：'user' 或 'p:<hex键>'（p- 前缀与目录名隔离）。
    create=True：user 域目录不存在也返回（新建面——首写自动建目录）。"""
    if key == USER_DOMAIN:
        d = memory_root() / USER_DIR_NAME
        return d if (create or d.is_dir()) else None
    if key.startswith("p:") and re.fullmatch(r"p:[0-9a-f]{12}", key):
        d = memory_root() / key[2:]
        return d if d.is_dir() else None
    return None


def list_domain_keys() -> list[str]:
    """现存域键（user 恒在；project 域=有 manifest 的 hex 目录）。"""
    out = [USER_DOMAIN]
    root = memory_root()
    try:
        dirs = sorted(p for p in root.iterdir()
                      if p.is_dir() and p.name != USER_DIR_NAME)
    except OSError:
        return out
    out += [f"p:{d.name}" for d in dirs
            if re.fullmatch(r"[0-9a-f]{12}", d.name)
            and (d / "manifest.json").exists()]
    return out


def _manifest(dir_: Path) -> dict:
    try:
        d = json.loads((dir_ / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"entries": [], "boundary": {}}


def _atomic_write(path: Path, text: str) -> None:
    """三轮修：tmp+rename 原子写——进程写一半被杀不留截断文件（skilllock
    / manifest 同族防线）。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _save_manifest(dir_: Path, m: dict) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    tmp = dir_ / "manifest.json.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, dir_ / "manifest.json")


# ---------------------------------------------------------------- P5 git 版本化
# MemFS 语义：一次 commit = 「记住」的边界——历史可回溯、LRU/忘掉可恢复。
# 本地仓（init 后永不触网络：只用 add/commit/show/log）；git 缺席/失败静默
# 降级为无版本记忆（写入不受影响）；跨进程锁 flock（用户域是全局目录，
# 多会话进程并发写是常态）。
_GIT_DISABLED = False               # git 二进制缺席时置位（进程内短路）
_LOCK_TIMEOUT_S = 5.0               # 锁超时：放弃本次 commit，文件照写下趟补


def _git(dir_: Path, *args: str, timeout: float = 15.0):
    """本地 git 调用（永不触网络）。失败返回 None（调用方自决降级）。"""
    global _GIT_DISABLED
    if _GIT_DISABLED:
        return None
    try:
        return subprocess.run(["git", "-C", str(dir_), *args],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        _GIT_DISABLED = True
        log.warning("git 不可用——记忆 git 版本化降级关闭（读写不受影响）")
        return None
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("git %s 失败（跳过版本化）：%s", args[0], e)
        return None


def _ensure_repo(dir_: Path) -> bool:
    """域目录首次使用时 git init（本地身份注入，禁 GPG；锁文件进 exclude）。"""
    if (dir_ / ".git").exists():
        return True
    r = _git(dir_, "init", "-q", "--initial-branch=main")
    if r is None or r.returncode != 0:
        return False
    _git(dir_, "config", "user.name", "loadn-memory")
    _git(dir_, "config", "user.email", "memory@loadn.invalid")
    _git(dir_, "config", "commit.gpgsign", "false")
    info = dir_ / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    try:
        (info / "exclude").write_text(".loadn-memory.lock\n", encoding="utf-8")
    except OSError:
        pass
    return True


@contextmanager
def _domain_lock(dir_: Path):
    """跨进程域锁（flock 重试至超时；超时 yield False=放弃本次 commit）。"""
    import fcntl
    dir_.mkdir(parents=True, exist_ok=True)   # reject 路径先于写入建目录进锁
    lock = dir_ / ".loadn-memory.lock"
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    fd = None
    try:
        lock.touch(exist_ok=True)
        fd = open(lock)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False            # 超时：文件已写，commit 让下趟补
                    return
                time.sleep(0.05)
        yield True
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            fd.close()


def _commit_locked(dir_: Path, message: str, *, allow_empty: bool = False) -> bool:
    """add -A + commit（**调用方须已持域锁**；含上趟锁超时漏提的补提交）。
    消息单行化（换行不进 message）。init 也入锁——防并发双 init。"""
    if not _ensure_repo(dir_):
        return False
    message = " ".join((message or "").split())[:200]
    r = _git(dir_, "add", "-A")
    if r is None or r.returncode != 0:
        return False
    args = ["commit", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    r = _git(dir_, *args)
    if r is not None and r.returncode != 0 \
            and "nothing to commit" not in (r.stderr or ""):
        return False
    return True


def commit_domain(dir_: Path, message: str, *, allow_empty: bool = False) -> bool:
    """对域提交一次变更（取锁→ensure→add -A→commit）。锁超时放弃本次
    commit（调用方文件已写，下趟 add -A 补提交）。"""
    with _domain_lock(dir_) as ok:
        if not ok:
            log.warning("记忆域锁超时——本次 commit 放弃（文件已写，下趟补）：%s",
                        " ".join(message.split())[:60])
            return False
        return _commit_locked(dir_, message, allow_empty=allow_empty)


# ---------------------------------------------------------------- 条目操作
def load_entries(cwd: Path, domain: str = PROJECT_DOMAIN) -> list[dict]:
    return _manifest(memory_dir(cwd, domain)).get("entries") or []


def entries_of(dir_: Path) -> list[dict]:
    """按域目录取条目（管理面用——不经 cwd 解析）。"""
    return _manifest(dir_).get("entries") or []


def entry_file_text(dir_: Path, e: dict) -> str:
    """条目 .md 全文（含 frontmatter；读失败回落 manifest 摘要）。"""
    try:
        return (dir_ / e["file"]).read_text(encoding="utf-8")
    except (OSError, KeyError):
        return (e.get("content") or "")


def forget(cwd: Path, keyword: str, *, event_sink=None,
           domain: str = PROJECT_DOMAIN) -> int:
    """忘掉：summary/content 命中 keyword 的条目删除（返回删除数；单域）。"""
    return _forget_dir(memory_dir(cwd, domain), keyword, event_sink=event_sink,
                       user_domain=(domain == USER_DOMAIN))


def _forget_dir(dir_: Path, keyword: str, *, event_sink=None,
                user_domain: bool = False) -> int:
    if user_domain and not user_enabled():
        return 0                         # off：用户域零读写
    with _domain_lock(dir_) as locked:
        if not locked:
            # 三轮修：fail-closed——锁忙时放弃删除（原"体照跑"：超时后
            # 无锁整文件 RMW 会覆盖并发 remember 的新条目=已删条目复活/
            # 新条目变孤儿）
            return 0
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
        if not removed:
            return 0
        for e in removed:
            try:
                (dir_ / e["file"]).unlink(missing_ok=True)
            except OSError:
                pass
        m["entries"] = keep
        _save_manifest(dir_, m)
        if locked:      # 锁超时：删除已落盘，commit 让下趟补
            _commit_locked(dir_, f"memory: 忘掉 {keyword[:30]}"
                                 f"（{len(removed)} 条）")
        if event_sink:
            event_sink("memory_forgotten",
                       {"keyword": keyword, "removed": [e["id"] for e in removed]})
        return len(removed)


def remember(cwd: Path, summary: str, content: str, *, origin_session: str,
             event_sink=None, domain: str = PROJECT_DOMAIN,
             reason: str | None = None) -> dict | None:
    """写入一条记忆（直写通道与抽取通道同库；两域同守护栏）。敏感护栏前置。"""
    return _remember_dir(
        memory_dir(cwd, domain), summary, content,
        origin_session=origin_session, event_sink=event_sink,
        user_domain=(domain == USER_DOMAIN),
        message=f"memory: {summary} [session:{origin_session}]",
        reason=reason)


def _remember_dir(dir_: Path, summary: str, content: str, *, origin_session: str,
                  event_sink, user_domain: bool, message: str,
                  eid: str | None = None, reason: str | None = None) -> dict | None:
    if user_domain and not user_enabled():
        return None                      # off：用户域零读写（显式面也拒）
    if canary_hit(summary + "\n" + content):
        # P5：拒绝也是事件——空提交留审计痕（不落被拒内容本体）
        commit_domain(dir_,
                      f"memory: reject 护栏拦截 [session:{origin_session}]",
                      allow_empty=True)
        if event_sink:
            event_sink("memory_blocked", {"summary": summary[:80]})
        return None
    # P7 稳定 id：域键|溯源|摘要|内容 的 sha1 前 8 位——同内容重抽不换 id
    # （uuid 仅保留给显式指定 eid 的恢复路径）
    summary = " ".join((summary or "").split())          # 二轮修#16：单行化
    origin_session = " ".join((origin_session or "").split())  # （换行=串键/伪造溯源）
    eid = eid or hashlib.sha1(
        f"{dir_.name}|{origin_session}|{summary}|{content}".encode()
    ).hexdigest()[:8]
    fname = f"{eid}.md"
    (dir_).mkdir(parents=True, exist_ok=True)
    (dir_ / fname).write_text(
        f"---\nid: {eid}\nsummary: {summary}\n"
        f"origin_session: {origin_session}\n"
        f"reason: {' '.join((reason or 'inferred').split())}\n"
        f"created_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n---\n\n{content}\n",
        encoding="utf-8")
    # P5 并发安全：manifest 读改写+LRU 淘汰+commit 全段在域锁内（用户域是
    # 全局目录，多会话进程/线程并发写是常态）。三轮修：锁超时 fail-closed
    # 放弃 manifest 变更（原"照更"的超时路径无锁整文件 RMW——与持锁方的
    # 写互相覆盖=丢条目/复活；.md 已写无妨：不进 manifest 即不注入）
    with _domain_lock(dir_) as locked:
        if not locked:
            if event_sink:
                event_sink("memory_blocked", {"summary": summary[:80],
                                              "why": "domain_lock_busy"})
            return None
        m = _manifest(dir_)
        # P7 幂等：同 id（同内容重抽）原位更新而非重复入库
        dup = next((x for x in m.get("entries") or [] if x.get("id") == eid), None)
        entry = {"id": eid, "file": fname, "summary": summary,
                 "content": content[:2000], "origin_session": origin_session,
                 "reason": reason or "inferred",
                 "created_at": (dup or {}).get("created_at")
                 or time.strftime("%Y-%m-%dT%H:%M:%S")}
        if dup is not None:
            m["entries"] = [entry if x.get("id") == eid else x
                            for x in m.get("entries") or []]
        else:
            m.setdefault("entries", []).append(entry)
        # LRU 上限：最旧淘汰
        if len(m["entries"]) > MAX_ENTRIES:
            for old in m["entries"][:len(m["entries"]) - MAX_ENTRIES]:
                try:
                    (dir_ / old["file"]).unlink(missing_ok=True)
                except OSError:
                    pass
            m["entries"] = m["entries"][-MAX_ENTRIES:]
        _save_manifest(dir_, m)
        if locked:
            # 一次写入=一次 commit（LRU 淘汰的删除同 commit 入史可找回）
            _commit_locked(dir_, message)
        if event_sink:
            event_sink("memory_written",
                       {"id": eid, "summary": summary[:80],
                        "origin": origin_session})
        return entry  # 二轮修#14：dup 原位更新不挪尾——[-1] 会返回别的条目


def forget_all(cwd: Path, keyword: str, *, event_sink=None) -> int:
    """"忘掉 X"两域生效：project 全查 + user（开关开时）。返回总删除数。"""
    n = forget(cwd, keyword, event_sink=event_sink)
    if user_enabled():
        n += forget(cwd, keyword, event_sink=event_sink, domain=USER_DOMAIN)
    return n


def _remove_entry(dir_: Path, m: dict, eid: str) -> dict | None:
    """按 id 从 manifest 摘除并删文件。"""
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


def promote(cwd: Path, eid: str, *, event_sink=None) -> dict | None:
    """手动提升：项目域条目 → 用户域（原域删除，溯源保留）。CLI 面。"""
    if not user_enabled():
        log.warning("promote 需要 LOADN_USER_MEMORY=on")
        return None
    dir_ = memory_dir(cwd, PROJECT_DOMAIN)
    # 复查修#6：manifest 读改写入域锁（与 remember/forget 同款——此前锁外
    # RMW，与引擎写入并发丢更新）
    with _domain_lock(dir_) as locked:
        if not locked:
            raise MemoryOpError("记忆域锁忙，稍后重试")
        m = _manifest(dir_)
        e = _remove_entry(dir_, m, eid)
        if e is None:
            return None
        _save_manifest(dir_, m)
        if locked:
            _commit_locked(dir_, f"memory: 提升 {eid} 至用户域")
    out = remember(cwd, e.get("summary") or eid, e.get("content") or "",
                   origin_session=e.get("origin_session") or "?",
                   event_sink=event_sink, domain=USER_DOMAIN,
                   reason=e.get("reason"))
    if event_sink:
        event_sink("memory_promoted", {"id": eid})
    return out


def boundary_of(cwd: Path) -> str:
    return str(_manifest(memory_dir(cwd)).get("boundary", {}).get(
        "message_id") or "")


def advance_boundary(cwd: Path, message_id: str) -> None:
    # 二轮修#15：manifest RMW 入域锁（与 remember/forget 同款——否则与
    # 写入并发时丢 entries）
    dir_ = memory_dir(cwd)
    with _domain_lock(dir_) as locked:
        if not locked:
            return                   # 六轮修 B6：锁忙放弃（同族 fail-closed）
        m = _manifest(dir_)
        m["boundary"] = {"message_id": message_id}
        _save_manifest(dir_, m)


# ---------------------------------------------------------------- P7 来源标注
def content_hash8(content: str) -> str:
    """内容指纹（memory_hits 携带；sources 对比判定 存在/已修改）。"""
    return hashlib.sha1((content or "").encode()).hexdigest()[:8]


def find_entry(domain_key: str, eid: str, *, project_dir: Path | None = None):
    """按域键+条目 id 找条目（sources 解析用）。返回 (域目录, 条目|None)。

    domain_key："user" / "p:<hex>" / "project"（P7 注入的域枚举名——须由
    调用方给 project_dir=该会话的项目域目录，否则 project 域条目会被误判
    为已删除，复查修#3）。"""
    d = None
    if domain_key == PROJECT_DOMAIN:
        d = project_dir
    elif domain_key != PROJECT_DOMAIN:
        d = domain_dir_by_key(domain_key)
    if d is None:
        return None, None
    e = next((x for x in entries_of(d) if x.get("id") == eid), None)
    return d, e


# ---------------------------------------------------------------- P6 管理面操作
# 编辑/删除/恢复/历史：webui 记忆管理页与 CLI 共用；护栏与域锁同上文通道。
class MemoryOpError(Exception):
    """管理面操作失败（携带用户可读原因）。"""


def create_entry(dir_: Path, summary: str, content: str, *,
                 actor: str = "webui", event_sink=None) -> dict | None:
    """管理面新建条目（按域目录直建；护栏/锁/commit 与引擎通道同构）。"""
    summary = (summary or "").strip()
    if not summary or not (content or "").strip():
        raise MemoryOpError("summary 与 content 不能为空")
    return _remember_dir(
        dir_, summary, content, origin_session=f"manual:{actor}",
        event_sink=event_sink, user_domain=(dir_.name == USER_DIR_NAME),
        message=f"memory: manual:{actor} 新增 {summary[:30]}",
        reason="explicit")


def edit_entry(dir_: Path, eid: str, *, content: str | None = None,
               summary: str | None = None, actor: str = "webui",
               event_sink=None) -> dict:
    """编辑条目（正文/摘要；frontmatter 其余字段与溯源保留）。
    护栏重跑：命中即拒（fail-closed，不部分写入）。保存即 commit
    `memory: manual:<actor> 编辑 <id>`。"""
    # 三轮修：单行化（#15/#16 修了 remember 通道，编辑通道漏——多行
    # summary 使 parse_frontmatter 在首个 \n--- 提前闭合=元数据全灭/伪键注入）
    new_summary = " ".join((summary or "").split()) or None
    with _domain_lock(dir_) as locked:
        if not locked:
            raise MemoryOpError("记忆域锁忙，稍后重试")
        m = _manifest(dir_)
        e = next((x for x in m.get("entries") or [] if x.get("id") == eid), None)
        if e is None:
            raise MemoryOpError(f"条目不存在: {eid}")
        body = content if content is not None else (e.get("content") or "")
        summ = new_summary or (e.get("summary") or eid)
        if canary_hit(summ + "\n" + body):
            raise MemoryOpError("护栏拦截：内容含蜜罐/凭证形态，拒绝保存")
        # 重写 .md：保留原 frontmatter 的 id/溯源，更新 summary/正文
        from loadn.util import parse_frontmatter
        meta, _ = parse_frontmatter(entry_file_text(dir_, e))
        meta["summary"] = summ
        fm = "\n".join(f"{k}: {v}" for k, v in meta.items())
        (dir_ / e["file"]).write_text(
            f"---\n{fm}\n---\n\n{body}\n", encoding="utf-8")
        e["summary"] = summ
        e["content"] = body[:2000]
        e.setdefault("reason", "inferred")
        e.pop("draft", None)      # P12：保存即转正（draft 教训去角标）
        _save_manifest(dir_, m)
        if locked:
            _commit_locked(dir_, f"memory: manual:{actor} 编辑 {eid}")
        if event_sink:
            event_sink("memory_edited", {"id": eid, "actor": actor})
        return e


def delete_entry(dir_: Path, eid: str, *, actor: str = "webui",
                 event_sink=None) -> dict:
    """删除条目（git 史保留可恢复）。删除即 commit。"""
    with _domain_lock(dir_) as locked:
        if not locked:
            raise MemoryOpError("记忆域锁忙，稍后重试")
        m = _manifest(dir_)
        e = _remove_entry(dir_, m, eid)
        if e is None:
            raise MemoryOpError(f"条目不存在: {eid}")
        _save_manifest(dir_, m)
        if locked:
            _commit_locked(dir_, f"memory: manual:{actor} 删除 {eid}")
        if event_sink:
            event_sink("memory_deleted", {"id": eid, "actor": actor})
        return e


def file_history(dir_: Path, fname: str, limit: int = 30) -> list[dict]:
    """单条目提交史（新→旧）：[{hash, date, subject}]。无 git 史返回 []。"""
    if not (dir_ / ".git").exists():
        return []
    r = _git(dir_, "log", "--format=%h%x09%ad%x09%s", "--date=short",
              "-n", str(limit), "--", fname)
    if r is None or r.returncode != 0:
        return []
    out = []
    for ln in (r.stdout or "").strip().splitlines():
        h, date, subject = (ln.split("\t", 2) + ["", ""])[:3]
        out.append({"hash": h, "date": date, "subject": subject})
    return out


def version_text(dir_: Path, fname: str, ref: str) -> str:
    """某提交下该文件全文（读不到抛 MemoryOpError）。"""
    r = _git(dir_, "show", f"{ref}:{fname}")
    if r is None or r.returncode != 0:
        raise MemoryOpError(f"读取版本失败: {ref}")
    return r.stdout


def restore_version(dir_: Path, eid: str, ref: str, *, actor: str = "webui",
                    event_sink=None) -> dict:
    """把 eid 在 ref 版本的正文恢复为当前内容。已删条目按原 id 重建
    （溯源从版本 frontmatter 读回）——删→历史在→可恢复。禁整仓 reset：
    只物化单条目，另立 restore 提交。"""
    fname = f"{eid}.md"
    text = version_text(dir_, fname, ref)
    from loadn.util import parse_frontmatter
    meta, body = parse_frontmatter(text)
    body = (body or "").strip() + "\n"
    exists = any(x.get("id") == eid for x in entries_of(dir_))
    if exists:
        e = edit_entry(dir_, eid, content=body, actor=actor, event_sink=None)
    else:
        e = _remember_dir(
            dir_, str(meta.get("summary") or eid), body,
            origin_session=str(meta.get("origin_session") or "?"),
            event_sink=None, user_domain=(dir_.name == USER_DIR_NAME),
            message=f"memory: manual:{actor} 恢复 {eid}@{ref}", eid=eid,
            reason=str(meta.get("reason") or "inferred"))
        if e is None:
            raise MemoryOpError("恢复被拒（护栏或用户域关闭）")
    with _domain_lock(dir_) as locked:
        if locked:
            _commit_locked(dir_, f"memory: manual:{actor} 恢复 {eid}@{ref}")
    if event_sink:
        event_sink("memory_restored", {"id": eid, "ref": ref, "actor": actor})
    return e
