"""会话工作区：脚手架、宪法渲染、状态双轨（承袭 papergo/workspace.py）。

每个会话一个目录 workspace/<sid>/，无头会话 cwd 即此目录——CLAUDE.md
宪法自动加载，"宪法随会话走，不依赖全局安装"。
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from . import profile as profile_mod
from . import skills as skills_mod
from .config import CODE_ROOT, CONFIG, PATHS
from .util import iso, slugify

# 工作区目录契约（与 prompts/workspace.md.tmpl 保持一致）
SESSION_DIRS = ["inputs", "artifacts", "work", "notes", "logs"]

# Z.AI 网关服务端注入的 MCP 工具（GLM Coding Plan 自带 web_reader/4_5v 视觉）。
# 2026-09-10 实测：调用在网关服务端执行，不经客户端派发，permissions.disallow 拦不住
# （真正的约束靠宪法 §3）；列在这里只是兜底——若网关将来改为客户端派发即可硬拦。
ZAI_INJECTED_TOOLS = ["mcp__web_reader", "mcp__4_5v_mcp"]


def new_session_id(title: str) -> str:
    base = "".join(w for w in slugify(title).split("-")[:4]) or "task"
    rand = uuid.uuid4().hex[:4]
    return f"{datetime.now().strftime('%Y%m%d_%H%M')}-{base}{rand}"[:60]


def render_claude_md(sid: str, title: str, prof: profile_mod.Profile,
                     skills: list[str]) -> str:
    tmpl = (PATHS["prompts"] / "workspace.md.tmpl").read_text()
    fetch = CODE_ROOT / "scripts" / "fetch_page.py"
    repl = {
        "{{SESSION_ID}}": sid,
        "{{TITLE}}": title,
        "{{PROFILE_NAME}}": prof.name,
        "{{PROFILE_MD}}": prof.render_context(),
        "{{SKILLS_LIST}}": ", ".join(f"`{s}`" for s in skills) or "（本会话未挂载 skill）",
        "{{FETCH_PAGE}}": str(fetch) if fetch.exists() else "（未部署）",
    }
    out = tmpl
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def write_mcp_json(ws: Path, session_mcp: dict | None) -> None:
    """全局 config.mcp.servers + 会话级覆盖 合并落 ws/.mcp.json（标准项目级格式）。"""
    merged = dict(CONFIG.mcp.servers or {})
    merged.update(session_mcp or {})
    p = ws / ".mcp.json"
    if merged:
        p.write_text(json.dumps({"mcpServers": merged}, ensure_ascii=False, indent=2))
    else:
        p.unlink(missing_ok=True)


def session_env(ws: Path, sid: str) -> dict:
    """引擎无关的会话环境变量（.claude/settings.json env 块的共享子集）。

    claude CLI 经 settings.json 读；hahaness/opencode 由 engine.py 经
    TurnCall.env_extra 注入 spawn 环境——同一份内容，不双轨漂移。
    """
    fetch = CODE_ROOT / "scripts" / "fetch_page.py"
    import sys as _sys
    _cli = (shutil.which("wd") or shutil.which("loadn-web")
            or str(Path(_sys.executable).parent / "loadn-web"))
    cli = Path(_cli)
    env = {
        "LOADN_SESSION_ID": sid, "WORKDADDY_SESSION_ID": sid,
        "LOADN_ARTIFACTS": str(ws / "artifacts"), "WORKDADDY_ARTIFACTS": str(ws / "artifacts"),
        "LOADN_NOTES": str(ws / "notes"), "WORKDADDY_NOTES": str(ws / "notes"),
        "LOADN_WORK": str(ws / "work"), "WORKDADDY_WORK": str(ws / "work"),
        **({"LOADN_FETCH_PAGE": str(fetch), "WORKDADDY_FETCH_PAGE": str(fetch)} if fetch.exists() else {}),
        **({"LOADN_CLI": str(cli), "WORKDADDY_CLI": str(cli)} if cli.exists() else {}),
        **({"LOADN_CDP_URL": CONFIG.resources.cdp_url, "WORKDADDY_CDP_URL": CONFIG.resources.cdp_url}
           if CONFIG.resources.cdp_url else {}),
        **({"LOADN_PROXY": CONFIG.resources.proxy, "WORKDADDY_PROXY": CONFIG.resources.proxy}
           if CONFIG.resources.proxy else {}),
    }
    return env


def write_settings(ws: Path, sid: str, prof: profile_mod.Profile,
                   *, project: bool = False) -> None:
    """项目级 settings：env 注入 + 便利配置 + 工具禁用清单（权限本身由 CLI flag 统一）。

    claude 落 .claude/settings.json；hahaness 落 .agent/settings.json（其
    PermissionEngine 的规则来源）。密钥永远不进 env（Bash 里 env 一打就泄露）；
    agent 用 `"$LOADN_CLI" r …`（旧 $WORKDADDY_CLI 兼容），CLI 自己读 config.yaml。
    project=True（项目属主目录）：env 写 WORKDADDY_PROJECT_ID 而非
    SESSION_ID——共享目录的 settings 不能背某个子任务的 id（子任务的
    per-session 值由 engine 恒注入 spawn env）。
    """
    env = session_env(ws, sid)
    if project:
        _proj_map = {"LOADN_SESSION_ID": "LOADN_PROJECT_ID",
                     "WORKDADDY_SESSION_ID": "WORKDADDY_PROJECT_ID"}
        env = {(_proj_map.get(k, k)): v
               for k, v in env.items()}
    settings = {"env": env}
    if prof.model:
        settings["model"] = prof.model
    # 无头 -p 模式没有交互弹窗：AskUserQuestion 只会报错并诱导 agent 乱猜内容，
    # 一律禁用（宪法 §2.6：要澄清就把问题写进回复，等用户下一条消息）。
    settings["permissions"] = {"disallow": ["AskUserQuestion", *ZAI_INJECTED_TOOLS,
                                            *prof.disallowed_tools]}
    d = ws / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2))
    # hahaness 规则（deny 语义同上；ZAI 注入工具只对 claude 网关有意义，不列）
    agent = {"permissions": {"deny": ["AskUserQuestion", *prof.disallowed_tools]}}
    ad = ws / ".agent"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "settings.json").write_text(json.dumps(agent, ensure_ascii=False, indent=2))


def write_constitution(ws: Path, text: str) -> None:
    """宪法单点双写：CLAUDE.md（claude/hahaness 读）+ AGENTS.md（opencode 读）。

    内容同一份——引擎中途切换零重建；rerender 也只走这里，防两文件漂移。
    """
    (ws / "CLAUDE.md").write_text(text)
    (ws / "AGENTS.md").write_text(text)


def scaffold(sid: str, title: str, prof: profile_mod.Profile, skill_names: list[str],
             session_mcp: dict | None = None, *, project: bool = False) -> Path:
    """创建 workspace/<sid>/ 全套目录与初始文件。

    project=True：本次 scaffold 的目录是项目属主目录（宪法 SESSION_ID=pid、
    TITLE=项目名、settings env 写 PROJECT_ID）——子任务创建时不走这里
    （见 create_session 的 project_id 分支：只补目录，绝不覆盖共享文件）。
    """
    ws = PATHS["workspace"] / sid
    ws.mkdir(parents=True, exist_ok=True)
    for d in SESSION_DIRS:
        (ws / d).mkdir(parents=True, exist_ok=True)

    mounted = skills_mod.mount(ws, skill_names)
    write_mcp_json(ws, session_mcp)
    write_settings(ws, sid, prof, project=project)
    write_constitution(ws, render_claude_md(sid, title, prof, mounted))
    if not (ws / "state.json").exists():
        (ws / "state.json").write_text(json.dumps({
            "session_id": sid, "title": title, "profile": prof.name,
            "claude_session_id": str(uuid.uuid4()),
            "updated_at": iso(), "history": [],
        }, ensure_ascii=False, indent=2))
    if not (ws / "PROGRESS.md").exists():
        (ws / "PROGRESS.md").write_text(
            f"# {title}\n\n- session: `{sid}` ｜ profile: {prof.name}\n"
            f"- created: {iso()}\n\n## 进展\n")
    return ws


def rerender(sid: str, title: str | None = None, prof_name: str | None = None,
             skills: list[str] | None = None) -> None:
    """profile/skills 变化后重渲染宪法（下一 turn 生效）。

    守卫：项目子任务直接跳过——共享宪法/settings/skills 挂载属项目，
    子任务改写会覆盖别的子任务正在用的文件（改项目配置走项目端点）。
    """
    from . import db as db_mod
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
    if row is not None and row["project_id"]:
        return
    ws = ws_path(sid)
    st = read_state(sid)
    title = title or st.get("title", sid)
    prof = profile_mod.get(prof_name or st.get("profile", "assistant"))
    if skills is None:
        skills = sorted(p.name for p in (ws / ".claude" / "skills").glob("*")
                        if p.is_dir() or p.is_symlink())
    mounted = skills_mod.mount(ws, skills)   # 禁用项会被剔除——宪法按实际挂载渲染
    write_settings(ws, sid, prof)   # settings 随 profile 变化一并刷新（env/禁用工具）
    write_constitution(ws, render_claude_md(sid, title, prof, mounted))


def render_project_constitution(pid: str, title: str, prof: profile_mod.Profile,
                                skill_names: list[str]) -> None:
    """项目宪法重渲染（项目改名/改 profile 用）——只动项目目录，子任务零影响。"""
    ws = ws_path(pid)
    mounted = skills_mod.mount(ws, skill_names)
    write_settings(ws, pid, prof, project=True)
    write_constitution(ws, render_claude_md(pid, title, prof, mounted))


def ws_path(sid: str) -> Path:
    return ws_of(sid)


def ws_of(sid: str) -> Path:
    """会话真实工作区：sessions.workspace 列 → 回落 workspace/<sid>。

    项目子任务共享项目目录（workspace 列指过去）；独立会话两处同值。
    不缓存（测试隔离 + 主键查询廉价）。
    """
    from . import db as db_mod
    try:
        with db_mod.conn() as c:
            row = db_mod.get_session(c, sid)
        if row is not None and row["workspace"]:
            return Path(row["workspace"])
    except Exception:
        pass
    return PATHS["workspace"] / sid


def read_state(sid: str) -> dict:
    try:
        return json.loads((ws_path(sid) / "state.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(sid: str, **fields) -> None:
    st = read_state(sid)
    st.update(fields)
    st["updated_at"] = iso()
    p = ws_path(sid) / "state.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    tmp.replace(p)


def _new_unique_id(title: str) -> str:
    """new_session_id + 查重循环（sessions/projects 两表 + 目录都不撞）。"""
    from . import db as db_mod
    ident = new_session_id(title)
    while True:
        with db_mod.conn() as c:
            if (not db_mod.get_session(c, ident)
                    and not db_mod.get_project(c, ident)):
                break
        ident = new_session_id(title + uuid.uuid4().hex[:4])
    return ident


def create_project(title: str, prof: profile_mod.Profile, skills: list[str] | None,
                   session_mcp: dict | None = None) -> tuple[str, Path]:
    """建项目（工作区属主）：scaffold 全套 + projects 行。返回 (pid, ws)。"""
    from . import db as db_mod

    title = (title or "").strip() or "新项目"
    skill_names = skills if skills is not None else list(prof.skills)
    pid = _new_unique_id(title)
    ws = scaffold(pid, title, prof, skill_names, session_mcp, project=True)
    with db_mod.conn() as c:
        db_mod.create_project(
            c, id=pid, title=title, workspace=str(ws), profile=prof.name,
            skills_json=json.dumps(skill_names, ensure_ascii=False),
            mcp_json=json.dumps(session_mcp or {}, ensure_ascii=False))
    return pid, ws


def create_session(title: str, prof: profile_mod.Profile, skills: list[str] | None,
                   session_mcp: dict | None = None, *,
                   project_id: str | None = None) -> tuple[str, Path]:
    """建会话（API 入口）：脚手架 + DB 行。返回 (sid, ws)。

    project_id 非空 = 项目子任务（CC 多窗口语义）：workspace 指向项目目录
    **只补缺失目录，不写任何共享文件**（宪法/settings/.mcp.json/state.json/
    PROGRESS.md/skills 挂载全属项目——覆盖会让兄弟子任务静默换宪法）。
    对话历史/引擎会话/SSE 全按 sid 独立。
    """
    from . import db as db_mod

    title = (title or "").strip() or "新任务"
    if project_id:
        with db_mod.conn() as c:
            proj = db_mod.get_project(c, project_id)
        if proj is None:
            raise KeyError(f"project not found: {project_id}")
        ws = Path(proj["workspace"])
        if not (ws / "CLAUDE.md").exists():
            # 项目目录被手删：按项目存的配置整套重建（scaffold 全套）
            scaffold(proj["id"], proj["title"], profile_mod.get(proj["profile"]),
                     json.loads(proj["skills_json"] or "[]"),
                     json.loads(proj["mcp_json"] or "{}"), project=True)
        else:
            for d in SESSION_DIRS:
                (ws / d).mkdir(parents=True, exist_ok=True)
        sid = _new_unique_id(title)
        with db_mod.conn() as c:
            db_mod.create_session(
                c, id=sid, title=title, profile=prof.name,
                claude_session_id=str(uuid.uuid4()), session_fresh=1,
                workspace=str(ws), project_id=project_id,
                skills_json=json.dumps(
                    skills if skills is not None else json.loads(proj["skills_json"] or "[]"),
                    ensure_ascii=False),
                mcp_json=json.dumps(
                    session_mcp if session_mcp is not None
                    else json.loads(proj["mcp_json"] or "{}"), ensure_ascii=False))
        return sid, ws

    skill_names = skills if skills is not None else list(prof.skills)
    sid = _new_unique_id(title)
    ws = scaffold(sid, title, prof, skill_names, session_mcp)
    with db_mod.conn() as c:
        db_mod.create_session(
            c, id=sid, title=title, profile=prof.name, claude_session_id=str(uuid.uuid4()),
            session_fresh=1, workspace=str(ws),
            skills_json=json.dumps(skill_names, ensure_ascii=False),
            mcp_json=json.dumps(session_mcp or {}, ensure_ascii=False))
    return sid, ws
