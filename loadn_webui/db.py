"""SQLite 状态库（WAL）：sessions / messages / turns / artifacts / session_events / kv。

铁律承袭 前身项目：无头 claude 会话不直接碰这个库——台账走
workspace 的 state.json 与 PROGRESS.md（agent 按宪法 §2 自维护，DB 不
做同步；轮换/唤醒 anchor 引导 agent 读盘续作）。

活跃性在 turn 层（queued→running→done|error|stopped|interrupted），
sessions 只有 active/archived（会话永续，可随时 --resume 续聊），
另有 starred 收藏标记（与归档正交，归档的收藏恢复后仍回收藏夹）。

侧栏分区（2026-09-22）：pinned 置顶 / starred 收藏 / category_id 自定义分类，
三者互斥语义由前端「移动到」维护（服务端只存位）；归档仍是 status。
任务与项目统一支持（projects 也有 pinned/starred/category_id）。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import PATHS

# R7 回滚门禁：每次加列/加表 +1；RELEASE.json 记此值，rollback 时比对。
# additive-only 契约：只加列/加表（旧代码可跑新 schema，多余列无害）。
# rev9：categories.icon（侧栏空间图标）+ artifacts 归属三列 + agent_files 表
# rev10：会话内子任务（subtasks 表 + turns.subtask_id 打标列）
SCHEMA_REV = 10
from .util import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,              -- 形如 20260902_1430-a1b2
  title TEXT,
  profile TEXT,                     -- researcher|coder|assistant|...
  status TEXT CHECK(status IN ('active','archived')) DEFAULT 'active',
  starred INTEGER DEFAULT 0,            -- 1=收藏（侧边栏收藏夹置顶展示）
  pinned INTEGER DEFAULT 0,             -- 1=置顶（侧栏「置顶」分区）
  category_id INTEGER,                  -- 自定义分类（categories.id；NULL=默认「最近」）
  claude_session_id TEXT,           -- 当前引擎的会话 id（claude/loadn=UUID，opencode=ses_…）
  session_fresh INTEGER DEFAULT 1,  -- 1=从未启动，下次 --session-id；0=--resume
  resume_failures INTEGER DEFAULT 0,-- resume 连续失败计数（≥2 轮换新会话）
  pending_anchor TEXT,              -- 会话轮换后暂存的交接 anchor（下一 turn 注入 prompt 后清空）
  workspace TEXT,
  skills_json TEXT,                 -- 挂载的 skill 名单
  mcp_json TEXT,                    -- 会话级 MCP 覆盖（合并全局后落 .mcp.json）
  cost_usd REAL DEFAULT 0,
  usage_json TEXT,                  -- 累计 token 用量
  engine TEXT DEFAULT 'claude',     -- 本会话锁定的执行引擎（engines/ 注册名）
  engine_session_ids TEXT,          -- 非当前引擎的会话 id 存档 JSON map（引擎切换可取回）
  engine_override TEXT,             -- 聊天框切换的会话级覆盖（NULL = 跟随 profile/默认）
  params_json TEXT,                 -- 会话级参数覆盖 JSON（model/effort/max_turns/…；NULL = 跟随 profile）
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  turn_id INTEGER,                  -- 关联 turns.id（user 消息与 assistant 回复同 turn）
  role TEXT,                        -- user|assistant
  content TEXT,
  blocks_json TEXT,                 -- assistant 的工具卡片摘要 [{name,brief,is_error}]
  memory_hits_json TEXT,            -- P7 本 turn 注入记忆清单 [{id,domain,reason,hash}]
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  message_id INTEGER,
  status TEXT CHECK(status IN ('queued','running','done','error','stopped','interrupted')),
  mode TEXT DEFAULT 'foreground',   -- foreground|background（仅 UI 语义，执行同构）
  claude_session_id TEXT,
  resume INTEGER DEFAULT 0,
  pid INTEGER,
  exit_code INTEGER,
  duration_s REAL,
  cost_usd REAL,
  usage_json TEXT,
  models_json TEXT,                 -- result.modelUsage：按模型 token/成本拆分
  engine TEXT,                      -- 执行引擎（engines/ 注册名；收养/记账用）
  num_turns INTEGER,
  error TEXT,
  log_out TEXT, log_err TEXT,
  started_at TEXT, finished_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  path TEXT,                        -- 工作区相对路径（artifacts/ 下）
  kind TEXT,                        -- md|html|docx|code|data|image|other
  title TEXT,
  summary TEXT,                     -- 一行中文摘要（titlegen 回填；NULL=未回填）
  size INTEGER, mtime REAL,
  created_by TEXT DEFAULT 'agent',  -- agent|skill|user|export
  turn_id INTEGER,                  -- 归属 turn（agent_files 轨迹合并；NULL=未知）
  agent_id TEXT,                    -- 归属子代理（sub_N；NULL=主代理/全局产物）
  agent_name TEXT,                  -- 子代理人名（引擎取名；NULL 同上）
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,   -- 即 SSE event id（Last-Event-ID 补发游标）
  session_id TEXT,
  turn_id INTEGER,
  type TEXT,
  data_json TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,              -- new_session_id(title) 同款格式（目录安全）
  title TEXT,
  status TEXT CHECK(status IN ('active','archived')) DEFAULT 'active',
  starred INTEGER DEFAULT 0,        -- 1=收藏（与任务同款侧栏分区语义）
  pinned INTEGER DEFAULT 0,         -- 1=置顶
  category_id INTEGER,              -- 自定义分类（categories.id；NULL=默认「最近」）
  workspace TEXT,                   -- 项目工作区绝对路径（共享目录属主真源）
  profile TEXT,                     -- 项目宪法渲染 profile + 子任务默认角色
  skills_json TEXT,                 -- 子任务默认 skills
  mcp_json TEXT,                    -- 子任务默认 MCP 覆盖
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,                    -- 侧栏自定义分区名（唯一）
  icon TEXT,                             -- 空间图标（前端图标键或 emoji 字符；NULL=Tag 兜底）
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_files (       -- 子代理文件写入轨迹（产物归属真源）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  turn_id INTEGER,                      -- 关联 turns.id
  agent_id TEXT,                        -- sub_N（NULL=主代理写入，仅 turn 级轨迹）
  agent_name TEXT,                      -- 子代理人名
  path TEXT,                            -- 工作区相对路径（tool input file_path）
  seen_at TEXT,
  UNIQUE(turn_id, path)                 -- 同 turn 同路径后写覆盖（最后写者归属）
);

CREATE TABLE IF NOT EXISTS subtasks (           -- 会话内子任务（turn 自动分类打标，rev10）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),
  title TEXT,                           -- ≤12 字子任务名（分类器生成）
  status TEXT CHECK(status IN ('active','archived')) DEFAULT 'active',
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subtask_session ON subtasks(session_id, id);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS users (           -- 多用户（八轮）：账号密码登录
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,               -- pbkdf2$iter$salt$hash（标准库）
  role TEXT CHECK(role IN ('admin','user')) DEFAULT 'user',
  display_name TEXT,
  disabled INTEGER DEFAULT 0,
  created_at TEXT, last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (   -- 登录会话（cookie sid → user）
  id TEXT PRIMARY KEY,                       -- 32B urlsafe 随机
  user_id INTEGER REFERENCES users(id),
  created_at TEXT, expires_at TEXT, last_seen_at TEXT,
  agent TEXT                                 -- 登录时 UA 摘要（审计可读）
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT REFERENCES sessions(id),   -- 到点向该会话投递一条 turn（new_session 恒 NULL）
  kind TEXT DEFAULT 'message',               -- message=投递到现有会话 | new_session=到点新建会话
  label TEXT,                                -- 人读用途（如 FAO 冷却重考）
  prompt TEXT,                               -- 投递的 prompt（确认纪律照常生效）
  due_at TEXT,                               -- 下次触发时刻（iso()，字典序即时序；cron job 是缓存值）
  every_s INTEGER,                           -- 递归间隔秒；NULL=单次或 cron 模式
  cron TEXT,                                 -- 5 段 cron 表达式；NULL = at/every 模式
  max_fires INTEGER DEFAULT 1,               -- 触发次数上限（防跑飞；fires 达限置 done）
  fires INTEGER DEFAULT 0,
  status TEXT CHECK(status IN ('active','paused','done')) DEFAULT 'active',
  last_fired_at TEXT,
  title TEXT,                                -- new_session：会话标题（缺省用 label）
  profile TEXT,                              -- new_session：profile 名（空 = auto 匹配）
  engine TEXT,                              -- new_session：写新会话 engine_override（空 = 跟随默认）
  destination TEXT DEFAULT 'dashboard',    -- P11: dashboard|notify|notify+artifact
  is_system INTEGER DEFAULT 0,             -- P11: 内置 heartbeat 标记（面板区分）
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS shares (
  token TEXT PRIMARY KEY,              -- 20 hex 随机能力令牌（URL 即凭证，不可猜）
  session_id TEXT REFERENCES sessions(id),
  path TEXT,                           -- 工作区相对路径（限 artifacts/ 下）
  created_at TEXT,
  UNIQUE(session_id, path)
);

CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);
CREATE INDEX IF NOT EXISTS idx_turn_session ON turns(session_id, id);
CREATE INDEX IF NOT EXISTS idx_evt_session ON session_events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_evt_turn ON session_events(turn_id, id);
CREATE INDEX IF NOT EXISTS idx_art_session ON artifacts(session_id, id);
CREATE INDEX IF NOT EXISTS idx_job_due ON scheduled_jobs(status, due_at);

CREATE TABLE IF NOT EXISTS webhooks (          -- P3 事件触发（token 即凭证，删行即吊销）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT UNIQUE NOT NULL,        -- 20 hex 高熵（share 同构）
  name TEXT NOT NULL,
  profile TEXT,                      -- 空 = auto（关键词匹配）
  prompt_template TEXT NOT NULL,     -- 须含 {{payload}}（仅字面替换，禁求值）
  enabled INTEGER DEFAULT 1,
  allowed_ips_json TEXT,             -- JSON 数组；空 = 不限（client.host 直配，不信 XFF）
  rate_limit_per_min INTEGER DEFAULT 6,
  last_fired_at TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS webhook_runs (      -- 触发→会话映射（外部按 run id 轮询结果）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hook_id INTEGER REFERENCES webhooks(id),
  session_id TEXT REFERENCES sessions(id),
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hook_run ON webhook_runs(hook_id, id);

CREATE TABLE IF NOT EXISTS target_policies (     -- P10 按目标权限三档
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match TEXT NOT NULL,             -- 域名（精确/*.suffix）或动作类名
  kind TEXT CHECK(kind IN ('host','action')),
  mode TEXT CHECK(mode IN ('always','ask','never')),
  scope_note TEXT,                 -- 窄化说明（exact/domain-action/target）
  created_from TEXT,               -- manual | approval:<id> | skill-suggestion
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS channel_bindings (    -- P9 渠道 chat↔会话绑定
  chat_id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id),
  last_turn_id INTEGER DEFAULT 0,    -- 回信增量游标（已推送的 turn id）
  created_at TEXT
);
"""

ACTIVE_TURN_STATUSES = ("queued", "running")


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    PATHS["db"].parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(PATHS["db"], timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    try:
        c.executescript(SCHEMA)
        _migrate(c)
        # R7 回滚门禁：记录 schema 版本（只在值变化时写——每次连接都写会
        # 引发写锁竞争，实测 61 用例 ReadTimeout 的根因）
        try:
            row = c.execute("SELECT value FROM kv WHERE key='schema_rev'"
                            ).fetchone()
            if row is None or row[0] != str(SCHEMA_REV):
                c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES"
                          "('schema_rev', ?)", (str(SCHEMA_REV),))
        except sqlite3.OperationalError:
            pass
        yield c
        c.commit()
    finally:
        c.close()


def _migrate(c: sqlite3.Connection) -> None:
    """旧库补列（CREATE TABLE IF NOT EXISTS 不会给已有表加列）。"""
    cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
    if "starred" not in cols:
        c.execute("ALTER TABLE sessions ADD COLUMN starred INTEGER DEFAULT 0")
    if "engine" not in cols:
        c.execute("ALTER TABLE sessions ADD COLUMN engine TEXT DEFAULT 'claude'")
    if "engine_session_ids" not in cols:
        c.execute("ALTER TABLE sessions ADD COLUMN engine_session_ids TEXT")
    if "engine_override" not in cols:
        # 聊天框引擎切换的会话级覆盖（NULL = 跟随 profile/全局默认）
        c.execute("ALTER TABLE sessions ADD COLUMN engine_override TEXT")
    jcols = {r["name"] for r in c.execute("PRAGMA table_info(scheduled_jobs)")}
    if "destination" not in jcols:      # P11 投递档
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN destination TEXT "
                  "DEFAULT 'dashboard'")
    if "is_system" not in jcols:        # P11 内置 heartbeat 标记
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN is_system INTEGER "
                  "DEFAULT 0")
    tcols = {r["name"] for r in c.execute("PRAGMA table_info(turns)")}
    if "models_json" not in tcols:
        c.execute("ALTER TABLE turns ADD COLUMN models_json TEXT")
    if "engine" not in tcols:
        # 执行引擎名（daemon 停机期间切引擎时，收养/_finish 用对 spec）
        c.execute("ALTER TABLE turns ADD COLUMN engine TEXT")
    jcols = {r["name"] for r in c.execute("PRAGMA table_info(scheduled_jobs)")}
    acols = {r["name"] for r in c.execute("PRAGMA table_info(artifacts)")}
    if "summary" not in acols:
        # 一行中文摘要（titlegen 回填）；扫描重跑不覆盖已回填的 title/summary
        c.execute("ALTER TABLE artifacts ADD COLUMN summary TEXT")
    if "kind" not in jcols:
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN kind TEXT DEFAULT 'message'")
    if "cron" not in jcols:
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN cron TEXT")
    if "title" not in jcols:
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN title TEXT")
    if "profile" not in jcols:
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN profile TEXT")
    if "engine" not in jcols:
        c.execute("ALTER TABLE scheduled_jobs ADD COLUMN engine TEXT")
    scols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
    if "project_id" not in scols:
        # 项目子任务（独立任务目录）：NULL = 独立会话（存量全部如此，零迁移）
        c.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sess_project ON sessions(project_id)")
    if "pending_anchor" not in scols:
        # 会话轮换（resume 连败/token 超限）的交接 anchor：注入下一 turn prompt
        c.execute("ALTER TABLE sessions ADD COLUMN pending_anchor TEXT")
    if "params_json" not in scols:
        # 会话级参数覆盖（属性面板）：NULL = 全部跟随 profile
        c.execute("ALTER TABLE sessions ADD COLUMN params_json TEXT")
    if "pinned" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0")
    if "category_id" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN category_id INTEGER")
    pcols = {r["name"] for r in c.execute("PRAGMA table_info(projects)")}
    for col, ddl in (("starred", "INTEGER DEFAULT 0"), ("pinned", "INTEGER DEFAULT 0"),
                     ("category_id", "INTEGER")):
        if col not in pcols:
            # 任务/项目统一的侧栏分区位（置顶/收藏/自定义分类）
            c.execute(f"ALTER TABLE projects ADD COLUMN {col} {ddl}")
    # 八轮（多用户）：属主列——cookie 会话通道的隔离判定（NULL=legacy
    # token 时代，setup 首个 admin 建立时归并）
    for tbl in ("sessions", "projects", "scheduled_jobs", "webhooks",
                "channel_bindings", "categories"):
        tinfo = {r["name"] for r in c.execute(f"PRAGMA table_info({tbl})")}
        if "owner_id" not in tinfo:
            c.execute(f"ALTER TABLE {tbl} ADD COLUMN owner_id INTEGER")

    # rev9（多 Agent 工作台）：categories.icon + artifacts 归属三列
    cc = {r["name"] for r in c.execute("PRAGMA table_info(categories)")}
    if "icon" not in cc:
        c.execute("ALTER TABLE categories ADD COLUMN icon TEXT")
    acols = {r["name"] for r in c.execute("PRAGMA table_info(artifacts)")}
    for col, ddl in (("turn_id", "INTEGER"), ("agent_id", "TEXT"),
                     ("agent_name", "TEXT")):
        if col not in acols:
            c.execute(f"ALTER TABLE artifacts ADD COLUMN {col} {ddl}")

    # rev10（会话内子任务）：turns.subtask_id 打标列（subtasks 表走 SCHEMA 建表）
    tcols = {r["name"] for r in c.execute("PRAGMA table_info(turns)")}
    if "subtask_id" not in tcols:
        c.execute("ALTER TABLE turns ADD COLUMN subtask_id INTEGER")

    # 三轮修（backlog 清）：artifacts (session_id,path) 唯一索引——先清
    # 存量重复行（保最新），建索引后 upsert_artifact 的 ON CONFLICT 原子化。
    # 门禁：索引已在=零成本直过（DELETE 只在首建前跑一次——每连接都全表
    # GROUP BY 是无谓写事务，与在途写并发时引发 busy 等待）
    try:
        has_idx = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='uq_art_session_path'").fetchone()
        if not has_idx:
            c.execute(
                "DELETE FROM artifacts WHERE rowid NOT IN (SELECT MAX(rowid)"
                " FROM artifacts GROUP BY session_id, path)")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                      "uq_art_session_path ON artifacts(session_id, path)")
    except sqlite3.OperationalError:
        pass        # 旧库容错（索引建失败时 upsert 走旧路径等值不炸）


# ---------------------------------------------------------------- kv

def kv_get(c: sqlite3.Connection, key: str) -> str | None:
    row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, value))


def kv_del(c: sqlite3.Connection, key: str) -> None:
    c.execute("DELETE FROM kv WHERE key=?", (key,))


# ---------------------------------------------------------------- sessions
def create_session(c: sqlite3.Connection, **fields: Any) -> None:
    now = iso()
    fields.update(created_at=now, updated_at=now)
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    c.execute(f"INSERT INTO sessions({cols}) VALUES({ph})", tuple(fields.values()))


def get_session(c: sqlite3.Connection, sid: str) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()


def list_sessions(c: sqlite3.Connection, include_archived: bool = True) -> list[sqlite3.Row]:
    """全部会话（updated_at 倒序）。LEFT JOIN 带项目名（侧栏分组/搜索用）。"""
    q = ("SELECT s.*, p.title AS project_title FROM sessions s "
         "LEFT JOIN projects p ON p.id = s.project_id")
    if not include_archived:
        q += " WHERE s.status='active'"
    return c.execute(q + " ORDER BY s.updated_at DESC").fetchall()


def update_session(c: sqlite3.Connection, sid: str, touch: bool = True, **fields: Any) -> None:
    # 无字段 + touch：纯触碰（排队即置顶——列表按 updated_at 排序；旧实现
    # `if not fields: return` 让引擎排队路径的置顶触碰成了死代码）
    if not fields:
        if touch:
            c.execute("UPDATE sessions SET updated_at=? WHERE id=?", (iso(), sid))
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    # touch=False：收藏等纯标记位不扰动 updated_at（列表按它排序，点了星不该跳顶）
    if touch:
        c.execute(f"UPDATE sessions SET {sets}, updated_at=? WHERE id=?", (*fields.values(), iso(), sid))
    else:
        c.execute(f"UPDATE sessions SET {sets} WHERE id=?", (*fields.values(), sid))


def delete_session(c: sqlite3.Connection, sid: str) -> None:
    # 六轮修 B1：new_session 类 job 的 session_id 是「最新实例指针」（调度
    # 回填）——purge 一个实例不得删掉整个递归 job/内置心跳（先摘指针）
    c.execute("UPDATE scheduled_jobs SET session_id=NULL "
              "WHERE session_id=? AND kind='new_session'", (sid,))
    for t in ("messages", "turns", "artifacts", "session_events", "scheduled_jobs",
              "subtasks"):
        c.execute(f"DELETE FROM {t} WHERE session_id=?", (sid,))
    c.execute("DELETE FROM sessions WHERE id=?", (sid,))
    for key in (f"title_auto:{sid}", f"profile_auto:{sid}"):
        kv_del(c, key)


# ---------------------------------------------------------------- projects
def create_project(c: sqlite3.Connection, **fields: Any) -> None:
    fields.setdefault("status", "active")
    now = iso()
    fields.update(created_at=now, updated_at=now)
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    c.execute(f"INSERT INTO projects({cols}) VALUES({ph})", tuple(fields.values()))


def get_project(c: sqlite3.Connection, pid: str) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()


def list_projects(c: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM projects"
    if not include_archived:
        q += " WHERE status='active'"
    return c.execute(q + " ORDER BY updated_at DESC").fetchall()


def update_project(c: sqlite3.Connection, pid: str, touch: bool = True, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    # touch=False：置顶/收藏/分类等纯标记位不扰动 updated_at（与 sessions 同款）
    if touch:
        c.execute(f"UPDATE projects SET {sets}, updated_at=? WHERE id=?",
                  (*fields.values(), iso(), pid))
    else:
        c.execute(f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), pid))


def delete_project(c: sqlite3.Connection, pid: str) -> None:
    c.execute("DELETE FROM projects WHERE id=?", (pid,))


def project_session_counts(c: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """每项目的子任务计数 {pid: {active: n, archived: n}}（一条 GROUP BY）。"""
    out: dict[str, dict[str, int]] = {}
    for r in c.execute("SELECT project_id, status, COUNT(*) AS n FROM sessions "
                       "WHERE project_id IS NOT NULL GROUP BY project_id, status"):
        out.setdefault(r["project_id"], {})[r["status"]] = r["n"]
    return out


def workspace_refcount(c: sqlite3.Connection, ws: str, *,
                       exclude_sid: str | None = None) -> int:
    """引用该 workspace 目录的行数（sessions 任意 status + projects 行）。

    purge 判定唯一真源：归档兄弟也占文件；>0 时删 session 只删 DB 不 rmtree。
    """
    n = c.execute("SELECT COUNT(*) AS n FROM sessions WHERE workspace=? "
                  "AND id!=?", (ws, exclude_sid or "")).fetchone()["n"]
    n += c.execute("SELECT COUNT(*) AS n FROM projects WHERE workspace=?",
                   (ws,)).fetchone()["n"]
    return n


# ---------------------------------------------------------------- categories（侧栏自定义分区）
def create_category(c: sqlite3.Connection, name: str, icon: str | None = None) -> int:
    now = iso()
    return c.execute(
        "INSERT INTO categories(name, icon, created_at, updated_at) VALUES(?,?,?,?)",
        (name, icon, now, now)).lastrowid


def get_category(c: sqlite3.Connection, cid: int) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()


def list_categories(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute("SELECT * FROM categories ORDER BY id").fetchall()


def update_category(c: sqlite3.Connection, cid: int, *, name: str | None = None,
                    icon: str | None = ...) -> None:
    """改名/改图标（icon 哨兵 ... = 不动；显式 None = 清回兜底 Tag）。"""
    sets, vals = ["updated_at=?"], [iso()]
    if name is not None:
        sets.append("name=?")
        vals.append(name)
    if icon is not ...:
        sets.append("icon=?")
        vals.append(icon)
    c.execute(f"UPDATE categories SET {', '.join(sets)} WHERE id=?", (*vals, cid))


def delete_category(c: sqlite3.Connection, cid: int) -> None:
    """删分类：成员（任务+项目）的 category_id 一并置空 → 回「最近」。"""
    c.execute("DELETE FROM categories WHERE id=?", (cid,))
    for t in ("sessions", "projects"):
        c.execute(f"UPDATE {t} SET category_id=NULL WHERE category_id=?", (cid,))


def category_name_taken(c: sqlite3.Connection, name: str) -> bool:
    return c.execute("SELECT 1 FROM categories WHERE name=?", (name,)).fetchone() is not None


# ---------------------------------------------------------------- turns
def create_turn(c: sqlite3.Connection, **fields: Any) -> int:
    now = iso()
    fields.setdefault("status", "queued")
    fields.update(created=now, started_at=None, updated_at=now)
    fields.pop("created", None)
    fields.update(updated_at=now)
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    cur = c.execute(f"INSERT INTO turns({cols}) VALUES({ph})", tuple(fields.values()))
    return cur.lastrowid


def get_turn(c: sqlite3.Connection, tid: int) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone()


def update_turn(c: sqlite3.Connection, tid: int, **fields: Any) -> int:
    """返回受影响行数（0=turn 行已删——分类器打标竞态判定用）。"""
    if not fields:
        return 0
    sets = ", ".join(f"{k}=?" for k in fields)
    cur = c.execute(f"UPDATE turns SET {sets}, updated_at=? WHERE id=?",
                    (*fields.values(), iso(), tid))
    return cur.rowcount


def active_turns(c: sqlite3.Connection, sid: str | None = None) -> list[sqlite3.Row]:
    ph = ",".join("?" * len(ACTIVE_TURN_STATUSES))
    if sid:
        return c.execute(
            f"SELECT * FROM turns WHERE session_id=? AND status IN ({ph}) ORDER BY id",
            (sid, *ACTIVE_TURN_STATUSES)).fetchall()
    return c.execute(
        f"SELECT * FROM turns WHERE status IN ({ph}) ORDER BY id", ACTIVE_TURN_STATUSES).fetchall()


# ---------------------------------------------------------------- messages / events / artifacts
def add_message(c: sqlite3.Connection, **fields: Any) -> int:
    # 六轮修 B5：lone surrogate 清洗（API JSON 体的 \udcXX 转义
    # json.loads 照单全收——sqlite bind 抛 UnicodeEncodeError 会把
    # _finish 内的记账炸成 turn 永久 running）
    from loadn.util import sanitize_text
    for k in ("content", "blocks_json"):
        if isinstance(fields.get(k), str):
            fields[k] = sanitize_text(fields[k])
    fields.setdefault("created_at", iso())
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    cur = c.execute(f"INSERT INTO messages({cols}) VALUES({ph})", tuple(fields.values()))
    return cur.lastrowid


def list_messages(c: sqlite3.Connection, sid: str) -> list[sqlite3.Row]:
    return c.execute("SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()


def add_event(c: sqlite3.Connection, sid: str, turn_id: int | None, type_: str, data: dict) -> int:
    from loadn.util import sanitize_text  # 三轮修：lone surrogate 清洗
    cur = c.execute(
        "INSERT INTO session_events(session_id,turn_id,type,data_json,created_at) VALUES(?,?,?,?,?)",
        (sid, turn_id, type_,
         sanitize_text(json.dumps(data, ensure_ascii=False, default=str)),
         iso()))
    return cur.lastrowid


def events_after(c: sqlite3.Connection, sid: str, last_id: int, limit: int = 2000) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT * FROM session_events WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
        (sid, last_id, limit)).fetchall()


def recent_events_for_turn(c: sqlite3.Connection, sid: str, turn_id: int,
                           limit: int) -> list[sqlite3.Row]:
    """某 turn 事件的尾部 limit 条（正序返回）：SSE 精准回放用，
    前端 live 只渲染尾窗，超窗的头部丢弃无损体验。"""
    return c.execute(
        "SELECT * FROM (SELECT * FROM session_events WHERE session_id=? AND turn_id=? "
        "ORDER BY id DESC LIMIT ?) ORDER BY id",
        (sid, turn_id, limit)).fetchall()


def prune_broadcast(c: sqlite3.Connection, cutoff_iso: str,
                    *, hard_keep: int = 5000) -> int:
    """三轮修（backlog 清）：广播行（session_id='*'，egress 代理每请求
    双写 audit+session_events）不挂任何 turn——prune_events 的 WHERE
    session_id=? 永不匹配 → 无界增长（生产实证 2.5 万行，275MB 库体积
    主因之一）。按保留窗清；hard_keep 兜底总量上限（0=不兜底）。挂
    scheduler.tick（20s 一轮）与 prune_events 顺带。"""
    import sqlite3 as _sq
    try:
        cur = c.execute(
            "DELETE FROM session_events WHERE session_id='*' AND rowid IN ("
            "SELECT rowid FROM session_events WHERE session_id='*' "
            "AND COALESCE(created_at,'') != '' AND created_at < ? "
            "LIMIT 20000)", (cutoff_iso,))
        removed = cur.rowcount or 0
        if hard_keep:
            row = c.execute(
                "SELECT COUNT(*) n FROM session_events "
                "WHERE session_id='*'").fetchone()
            if row and row["n"] > hard_keep:
                c.execute(
                    "DELETE FROM session_events WHERE session_id='*' AND "
                    "rowid IN (SELECT rowid FROM session_events WHERE "
                    "session_id='*' ORDER BY id LIMIT ?)",
                    (row["n"] - hard_keep,))
                removed += 1
        return removed
    except _sq.OperationalError:
        return 0                                    # 旧库缺列等


def prune_events(c: sqlite3.Connection, sid: str, retain_days: float,
                 hard_keep: int = 5000) -> int:
    """终态感知清理：终态超过 retain_days 天的 turn 的事件删除；
    活跃（running/queued）turn 永不清（精准回放依赖其完整性）；
    总量超 hard_keep 时从最老的终态 turn 事件兜底裁剪。

    按 started_at 近似终态时刻（duration 上限远小于 retain 天数，误差可忽略，
    cutoff 已多留 24h 缓冲）。返回删除行数。
    """
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc)
              - _dt.timedelta(days=retain_days + 1)).isoformat()
    final = ("done", "error", "stopped", "interrupted")
    marks = ",".join("?" * len(final))
    cur = c.execute(
        f"DELETE FROM session_events WHERE session_id=? AND turn_id IN ("
        f"SELECT id FROM turns WHERE session_id=? AND status IN ({marks}) "
        f"AND COALESCE(started_at,'') != '' AND COALESCE(started_at,'') < ?)",
        (sid, sid, *final, cutoff))
    removed = cur.rowcount or 0
    # 三轮修（backlog 清）：广播行清理（见 prune_broadcast）
    removed += prune_broadcast(c, cutoff, hard_keep=0)
    # 兜底：总量仍超 hard_keep → 从最老的**终态** turn 事件继续裁（活跃 turn 不动）
    row = c.execute("SELECT COUNT(*) n FROM session_events WHERE session_id=?", (sid,)).fetchone()
    if row and row["n"] > hard_keep:
        excess = row["n"] - hard_keep
        c.execute(
            f"DELETE FROM session_events WHERE rowid IN ("
            f"SELECT e.rowid FROM session_events e JOIN turns t ON t.id=e.turn_id "
            f"WHERE e.session_id=? AND t.status IN ({marks}) ORDER BY e.id LIMIT ?)",
            (sid, *final, excess))
        removed += excess
    return removed


def upsert_artifact(c: sqlite3.Connection, **fields: Any) -> None:
    """三轮修（backlog 清）：SELECT→INSERT 竞态改 ON CONFLICT 原子 upsert
    （原两线程池路径并发扫同一新文件=双 INSERT 重复行，fetchone 永远只
    命中第一行，mtime 更新丢失+摘要双计费）。依赖唯一索引（_migrate 建）。"""
    fields["updated_at"] = iso()
    fields.setdefault("created_at", iso())
    sets = ", ".join(f"{k}=?" for k in fields)
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    try:
        c.execute(
            f"INSERT INTO artifacts({cols}) VALUES({ph}) "
            f"ON CONFLICT(session_id, path) DO UPDATE SET {sets}",
            (*fields.values(), *fields.values()))
    except sqlite3.OperationalError:
        # 六轮修 B2：唯一索引建置失败过的库（无 conflict target=SQL 拒）
        # ——回落旧 SELECT→UPDATE/INSERT 路径（慢但正确，下轮 _migrate
        # 建好索引自动回到原子路径）
        row = c.execute(
            "SELECT id FROM artifacts WHERE session_id=? AND path=?",
            (fields["session_id"], fields["path"])).fetchone()
        if row:
            c.execute(f"UPDATE artifacts SET {sets} WHERE id=?",
                      (*fields.values(), row["id"]))
        else:
            c.execute(f"INSERT INTO artifacts({cols}) VALUES({ph})",
                      tuple(fields.values()))


# ---------------------------------------------------------------- agent_files（产物归属轨迹）
def record_agent_file(c: sqlite3.Connection, sid: str, turn_id: int, path: str,
                      agent_id: str | None = None,
                      agent_name: str | None = None) -> None:
    """tool_use 写入轨迹（_consume 原子 upsert）：同 turn 同路径最后写者归属。
    agent_id=NULL 是主代理写入——只贡献 turn 级轨迹，不参与 agent 归属。"""
    c.execute(
        "INSERT INTO agent_files(session_id, turn_id, agent_id, agent_name,"
        " path, seen_at) VALUES(?,?,?,?,?,?) ON CONFLICT(turn_id, path) DO"
        " UPDATE SET agent_id=excluded.agent_id,"
        " agent_name=excluded.agent_name, seen_at=excluded.seen_at",
        (sid, turn_id, agent_id, agent_name, path, iso()))


# ---------------------------------------------------------------- subtasks（会话内子任务）
def create_subtask(c: sqlite3.Connection, sid: str, title: str) -> int:
    now = iso()
    return c.execute(
        "INSERT INTO subtasks(session_id, title, created_at, updated_at)"
        " VALUES(?,?,?,?)", (sid, title, now, now)).lastrowid


def list_subtasks(c: sqlite3.Connection, sid: str) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT * FROM subtasks WHERE session_id=? ORDER BY id", (sid,)).fetchall()


def find_subtask_by_title(c: sqlite3.Connection, sid: str,
                          title: str) -> sqlite3.Row | None:
    """同名复用（belt-and-braces：LLM 复用决策之外的第二道合并闸）。"""
    return c.execute(
        "SELECT * FROM subtasks WHERE session_id=? AND title=? ORDER BY id LIMIT 1",
        (sid, title)).fetchone()


def update_subtask(c: sqlite3.Connection, stid: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE subtasks SET {sets}, updated_at=? WHERE id=?",
              (*fields.values(), iso(), stid))


# ---------------------------------------------------------------- shares（产物分享）
def find_share(c: sqlite3.Connection, sid: str, path: str) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM shares WHERE session_id=? AND path=?",
                     (sid, path)).fetchone()


def create_share(c: sqlite3.Connection, sid: str, path: str, token: str) -> None:
    c.execute("INSERT INTO shares(token, session_id, path, created_at) VALUES(?,?,?,?)",
              (token, sid, path, iso()))


def get_share(c: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM shares WHERE token=?", (token,)).fetchone()


# ---------------------------------------------------------------- scheduled_jobs（定时调度）
def create_job(c: sqlite3.Connection, **fields: Any) -> int:
    fields.setdefault("fires", 0)
    fields.setdefault("status", "active")
    now = iso()
    fields.update(created_at=now, updated_at=now)
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    return c.execute(f"INSERT INTO scheduled_jobs({cols}) VALUES({ph})",
                     tuple(fields.values())).lastrowid


def get_job(c: sqlite3.Connection, jid: int) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM scheduled_jobs WHERE id=?", (jid,)).fetchone()


def list_jobs(c: sqlite3.Connection, sid: str | None = None,
              active_only: bool = False) -> list[sqlite3.Row]:
    q, args = "SELECT * FROM scheduled_jobs", []
    conds = []
    if sid:
        conds.append("session_id=?")
        args.append(sid)
    if active_only:
        conds.append("status='active'")
    if conds:
        q += " WHERE " + " AND ".join(conds)
    return c.execute(q + " ORDER BY due_at", args).fetchall()


def update_job(c: sqlite3.Connection, jid: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE scheduled_jobs SET {sets}, updated_at=? WHERE id=?",
              (*fields.values(), iso(), jid))


def delete_job(c: sqlite3.Connection, jid: int) -> bool:
    return bool(c.execute("DELETE FROM scheduled_jobs WHERE id=?", (jid,)).rowcount)


def due_jobs(c: sqlite3.Connection, now_iso: str) -> list[sqlite3.Row]:
    """到点待触发的 active job（服务停机期间过期的也在内——重启补投一次）。"""
    return c.execute(
        "SELECT * FROM scheduled_jobs WHERE status='active' AND due_at<=? ORDER BY due_at",
        (now_iso,)).fetchall()


# ---------------------------------------------------------------- webhooks（P3 事件触发）
def create_hook(c: sqlite3.Connection, **fields: Any) -> int:
    fields.setdefault("enabled", 1)
    fields.setdefault("rate_limit_per_min", 6)
    now = iso()
    fields.update(created_at=now, updated_at=now)
    cols = ", ".join(fields.keys())
    ph = ", ".join("?" for _ in fields)
    return c.execute(f"INSERT INTO webhooks({cols}) VALUES({ph})",
                     tuple(fields.values())).lastrowid


def get_hook(c: sqlite3.Connection, hid: int) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM webhooks WHERE id=?", (hid,)).fetchone()


def get_hook_by_token(c: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return c.execute("SELECT * FROM webhooks WHERE token=?", (token,)).fetchone()


def list_hooks(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute("SELECT * FROM webhooks ORDER BY id").fetchall()


def update_hook(c: sqlite3.Connection, hid: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE webhooks SET {sets}, updated_at=? WHERE id=?",
              (*fields.values(), iso(), hid))


def delete_hook(c: sqlite3.Connection, hid: int) -> bool:
    c.execute("DELETE FROM webhook_runs WHERE hook_id=?", (hid,))
    return bool(c.execute("DELETE FROM webhooks WHERE id=?", (hid,)).rowcount)


def add_hook_run(c: sqlite3.Connection, hook_id: int, session_id: str) -> int:
    return c.execute(
        "INSERT INTO webhook_runs(hook_id, session_id, created_at) VALUES(?,?,?)",
        (hook_id, session_id, iso())).lastrowid


def get_hook_run(c: sqlite3.Connection, hook_id: int, run_id: int) -> sqlite3.Row | None:
    return c.execute(
        "SELECT * FROM webhook_runs WHERE id=? AND hook_id=?",
        (run_id, hook_id)).fetchone()


# ---------------------------------------------------------------- 统计
# usage 里的数值键（engine session 累计 merge 与统计共用；server_tool_use 等非数值键不并）
NUMERIC_USAGE_KEYS = ("input_tokens", "output_tokens",
                      "cache_read_input_tokens", "cache_creation_input_tokens")


def usage_totals(c: sqlite3.Connection, sid: str | None = None) -> dict:
    from .integrations import pricing as pricing_mod  # 延迟 import 防环
    q = "SELECT usage_json, models_json, cost_usd FROM turns"
    args: tuple = ()
    if sid:
        q += " WHERE session_id=?"
        args = (sid,)
    agg = dict.fromkeys(NUMERIC_USAGE_KEYS, 0)
    cost = 0.0
    cost_api = 0.0
    for r in c.execute(q, args):
        cost += r["cost_usd"] or 0
        try:
            u = json.loads(r["usage_json"]) if r["usage_json"] else {}
        except (TypeError, json.JSONDecodeError):
            u = {}
        for k in NUMERIC_USAGE_KEYS:
            agg[k] += u.get(k) or 0
        # 真实成本：优先按 models_json 逐模型计价，缺则整 turn usage 按默认模型
        try:
            mu = json.loads(r["models_json"]) if r["models_json"] else None
        except (TypeError, json.JSONDecodeError):
            mu = None
        if mu:
            for name, m in mu.items():
                if not isinstance(m, dict):
                    continue
                cost_api += pricing_mod.cost_api_usd(
                    name, input_t=m.get("inputTokens") or 0,
                    cache_read_t=m.get("cacheReadInputTokens") or 0,
                    cache_write_t=m.get("cacheCreationInputTokens") or 0,
                    output_t=m.get("outputTokens") or 0)
        else:
            cost_api += pricing_mod.cost_api_usd(
                pricing_mod.DEFAULT_MODEL,
                input_t=u.get("input_tokens") or 0,
                cache_read_t=u.get("cache_read_input_tokens") or 0,
                cache_write_t=u.get("cache_creation_input_tokens") or 0,
                output_t=u.get("output_tokens") or 0)
    tin, tout = agg["input_tokens"], agg["output_tokens"]
    return {"in": tin, "out": tout, "total": tin + tout,
            "cache_read": agg["cache_read_input_tokens"],
            "cache_write": agg["cache_creation_input_tokens"],
            "total_all": sum(agg.values()),
            "cost_usd": round(cost, 4), "cost_api_usd": round(cost_api, 4)}


def daily_usage(c: sqlite3.Connection, days: int = 7) -> list[dict]:
    rows = c.execute(
        """SELECT substr(started_at,1,10) day, COUNT(*) n, SUM(COALESCE(cost_usd,0)) cost
           FROM turns WHERE started_at IS NOT NULL
             AND substr(started_at,1,10) >= date('now', ?)
           GROUP BY day ORDER BY day""",
        (f"-{days - 1} day",)).fetchall()
    return [{"day": r["day"], "turns": r["n"], "cost_usd": round(r["cost"] or 0, 4)} for r in rows]


def to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}
