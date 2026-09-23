"""会话工作区：脚手架、宪法渲染、状态双轨（承袭 papergo/workspace.py）。

每个会话一个目录 workspace/<sid>/，无头会话 cwd 即此目录——CLAUDE.md
宪法自动加载，"宪法随会话走，不依赖全局安装"。
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from . import profile as profile_mod
from . import skills as skills_mod
from .config import CODE_ROOT, CONFIG, PATHS
from .util import get_logger

log = get_logger(__name__)
from .util import iso, slugify

# 工作区目录契约（与 prompts/workspace.md.tmpl 保持一致）
SESSION_DIRS = ["inputs", "artifacts", "work", "notes", "logs"]
# 项目子任务的私有任务目录集：inputs 不在其中——附件与共享材料统一落项目根
# inputs/（兄弟任务共读），PROGRESS.md/state.json/notes/artifacts 全任务级
TASK_DIRS = ["artifacts", "work", "notes", "logs"]

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
    from .config import behavior_file
    tmpl = behavior_file("prompts", "workspace.md.tmpl").read_text()
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
    """全局 config.mcp.servers + 会话级覆盖 合并落 ws/.mcp.json（标准项目级格式）。

    W4/B3：stdio server 的 command+args 记哈希锁（.mcp-lock.json）——
    变更（rug pull）≠ 原哈希 → 审计 policy_change + 日志显著告警。
    """
    import hashlib
    merged = dict(CONFIG.mcp.servers or {})
    merged.update(session_mcp or {})
    # 会话级禁用哨兵：值 False = 本会话关掉这个全局 server（属性面板三态
    # 切换）——从合并结果剔除。真实 server 配置恒为 truthy dict，不会误伤
    for name in [k for k, v in merged.items() if v is False]:
        del merged[name]
    p = ws / ".mcp.json"
    if merged:
        p.write_text(json.dumps({"mcpServers": merged}, ensure_ascii=False, indent=2))
        locks = {}
        for name, cfg in merged.items():
            key = f"{cfg.get('command', '')} {' '.join(cfg.get('args') or [])}"
            locks[name] = hashlib.sha256(key.encode()).hexdigest()[:16]
        lp = ws / ".mcp-lock.json"
        old = {}
        try:
            old = json.loads(lp.read_text())
        except (OSError, json.JSONDecodeError):
            pass
        for name, h in locks.items():
            if name in old and old[name] != h:
                log.warning("[B3] MCP server %s 的 command/args 变更（rug pull"
                            " 风险）：%s → %s——须人工确认", name, old[name], h)
                from .audit import audit as _audit
                _audit("policy_change",
                       {"what": "mcp_command_changed", "server": name,
                        "old": old[name], "new": h})
        lp.write_text(json.dumps(locks, ensure_ascii=False, indent=2))
    else:
        p.unlink(missing_ok=True)


def project_root_of(sid: str) -> Path | None:
    """会话所属项目的根目录（共享 inputs/宪法属主）；非子任务返回 None。

    项目首任务（promote 升级）workspace 即项目根本身——返回值与其 ws 相同，
    调用方按需比较。
    """
    from . import db as db_mod
    try:
        with db_mod.conn() as c:
            row = db_mod.get_session(c, sid)
            if row is not None and row["project_id"]:
                proj = db_mod.get_project(c, row["project_id"])
                if proj is not None:
                    return Path(proj["workspace"])
    except Exception:
        pass
    return None


def inputs_dir_of(sid: str) -> Path:
    """附件上传/共享输入目录：项目子任务 → 项目根 inputs/（兄弟共读）；
    独立会话 → 本工作区 inputs/。"""
    proj = project_root_of(sid)
    return (proj / "inputs") if proj is not None else (ws_of(sid) / "inputs")


def session_env(ws: Path, sid: str) -> dict:
    """引擎无关的会话环境变量（.claude/settings.json env 块的共享子集）。

    claude CLI 经 settings.json 读；loadn/opencode 由 engine.py 经
    TurnCall.env_extra 注入 spawn 环境——同一份内容，不双轨漂移。
    项目子任务：artifacts/notes/work 指任务目录，inputs/项目根指路（共享），
    另带 LOADN_PROJECT_ROOT 供 agent 访问项目级材料。
    """
    fetch = CODE_ROOT / "scripts" / "fetch_page.py"
    import sys as _sys
    _cli = (shutil.which("wd") or shutil.which("loadn-web")
            or str(Path(_sys.executable).parent / "loadn-web"))
    cli = Path(_cli)
    proj = project_root_of(sid)
    inputs = (proj / "inputs") if proj is not None else (ws / "inputs")
    env = {
        "LOADN_SESSION_ID": sid, "WORKDADDY_SESSION_ID": sid,
        "LOADN_ARTIFACTS": str(ws / "artifacts"), "WORKDADDY_ARTIFACTS": str(ws / "artifacts"),
        "LOADN_NOTES": str(ws / "notes"), "WORKDADDY_NOTES": str(ws / "notes"),
        "LOADN_WORK": str(ws / "work"), "WORKDADDY_WORK": str(ws / "work"),
        "LOADN_INPUTS": str(inputs), "WORKDADDY_INPUTS": str(inputs),
        **({"LOADN_PROJECT_ROOT": str(proj), "WORKDADDY_PROJECT_ROOT": str(proj)}
           if proj is not None else {}),
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

    claude 落 .claude/settings.json；loadn 落 .loadn/settings.json（其
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
    # W1-b 三态：deny → disallow（现状管道）；allow → permissions.allow；
    # ask 引擎侧原生（.loadn permissions.ask），claude 侧由 hook 拦截提示。
    tools = getattr(prof, "tools", None) or {}
    disallow = ["AskUserQuestion", *ZAI_INJECTED_TOOLS,
                *prof.disallowed_tools, *tools.get("deny", [])]
    allow = tools.get("allow", [])
    perm = {"disallow": disallow}
    if allow:
        perm["allow"] = allow
    settings["permissions"] = perm
    # W1-b 执行点 A：PreToolUse 策略钩子（W1-0 POC 实证 bypass 下仍生效）。
    # 命令绝对路径（引擎 hooks 以 shell 在 cwd 执行）。
    hook_cmd = f'"{sys.executable}" -m loadn_webui policy-check'
    # claude 格式（matcher 嵌套）
    settings["hooks"] = {"PreToolUse": [{
        "matcher": "Bash|Write|Edit|MultiEdit|WebFetch",
        "hooks": [{"type": "command", "command": hook_cmd}],
    }]}
    d = ws / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2))
    # loadn 引擎规则（PermissionEngine 三态原生；hooks 同体）
    agent = {"permissions": {"deny": ["AskUserQuestion", *prof.disallowed_tools,
                                      *tools.get("deny", [])]}}
    if allow:
        agent["permissions"]["allow"] = allow
    if tools.get("ask"):
        agent["permissions"]["ask"] = tools["ask"]
    # loadn 引擎格式（平铺 command 列表，HookRunner.load 语义——matcher 概念
    # 引擎侧无，策略匹配在 policy-check 内部按 tool_name 分派）
    agent["hooks"] = {"PreToolUse": [{"command": hook_cmd}]}
    ad = ws / ".loadn"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "settings.json").write_text(json.dumps(agent, ensure_ascii=False, indent=2))
    # 旧 .agent 路径兼容写（一版后移除；引擎侧读新带旧兜底）
    legacy = ws / ".agent"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "settings.json").write_text(json.dumps(agent, ensure_ascii=False, indent=2))


def write_constitution(ws: Path, text: str) -> None:
    """宪法单点双写：CLAUDE.md（claude/hahaness 读）+ AGENTS.md（opencode 读）。

    内容同一份——引擎中途切换零重建；rerender 也只走这里，防两文件漂移。
    """
    (ws / "CLAUDE.md").write_text(text)
    (ws / "AGENTS.md").write_text(text)


def _init_ledger_files(ws: Path, sid: str, title: str, prof: profile_mod.Profile) -> None:
    """state.json / PROGRESS.md 初始桩（不存在才写——重建场景不覆盖已有台账）。

    引擎会话 id 不落 state.json：真相在 DB sessions.claude_session_id，
    写个随机 uuid 进来只会误导 agent（v0.5 前的陈旧字段，已删）。
    """
    if not (ws / "state.json").exists():
        (ws / "state.json").write_text(json.dumps({
            "session_id": sid, "title": title, "profile": prof.name,
            "updated_at": iso(), "history": [],
        }, ensure_ascii=False, indent=2))
    if not (ws / "PROGRESS.md").exists():
        (ws / "PROGRESS.md").write_text(
            f"# {title}\n\n- session: `{sid}` ｜ profile: {prof.name}\n"
            f"- created: {iso()}\n\n## 进展\n")


def _plant_canary(ws: Path, sid: str) -> None:
    # W5.5：会话 canary 蜜罐（泄露指示物；命中即熔断）
    try:
        from . import canary as _canary
        if not (ws / "notes" / ".canary_tokens.md").exists():
            _canary.plant(sid, ws=ws)
            _canary.invalidate_cache()
    except Exception:                                  # noqa: BLE001
        pass


def scaffold(sid: str, title: str, prof: profile_mod.Profile, skill_names: list[str],
             session_mcp: dict | None = None, *, project: bool = False) -> Path:
    """创建 workspace/<sid>/ 全套目录与初始文件。

    project=True：本次 scaffold 的目录是项目属主目录（宪法 SESSION_ID=pid、
    TITLE=项目名、settings env 写 PROJECT_ID）——子任务创建时不走这里
    （见 create_session 的 project_id 分支：scaffold_task_dir 建私有任务目录）。
    """
    ws = PATHS["workspace"] / sid
    ws.mkdir(parents=True, exist_ok=True)
    for d in SESSION_DIRS:
        (ws / d).mkdir(parents=True, exist_ok=True)

    mounted = skills_mod.mount(ws, skill_names)
    write_mcp_json(ws, session_mcp)
    write_settings(ws, sid, prof, project=project)
    write_constitution(ws, render_claude_md(sid, title, prof, mounted))
    _init_ledger_files(ws, sid, title, prof)
    _plant_canary(ws, sid)
    return ws


def scaffold_task_dir(ws: Path, sid: str, title: str, prof: profile_mod.Profile,
                      skill_names: list[str], session_mcp: dict | None) -> Path:
    """项目子任务的私有任务目录脚手架（v0.6 起子任务不再共享项目 cwd）。

    与 scaffold 的差异：①目录集用 TASK_DIRS（无 inputs——共享材料在项目根
    inputs/，env $LOADN_INPUTS 指路）；②不落 CLAUDE.md/AGENTS.md——宪法属
    项目根，引擎经祖先链加载（claude CLI 与 loadn context.py 均向上收集）；
    ③settings 是任务级的（SESSION_ID=本任务，宪法不改）。
    """
    ws.mkdir(parents=True, exist_ok=True)
    for d in TASK_DIRS:
        (ws / d).mkdir(parents=True, exist_ok=True)
    skills_mod.mount(ws, skill_names)
    write_mcp_json(ws, session_mcp)
    write_settings(ws, sid, prof)
    _init_ledger_files(ws, sid, title, prof)
    _plant_canary(ws, sid)
    return ws


def rerender(sid: str, title: str | None = None, prof_name: str | None = None,
             skills: list[str] | None = None) -> None:
    """profile/skills 变化后重渲染宪法（下一 turn 生效）。

    项目子任务：宪法/settings 属项目根不动，但 v0.6 起任务目录是私有的——
    重挂任务级 skills + 刷新任务级 settings（env/禁用工具随 profile 变化）。
    """
    from . import db as db_mod
    with db_mod.conn() as c:
        row = db_mod.get_session(c, sid)
    if row is not None and row["project_id"]:
        ws = ws_of(sid)
        prof = profile_mod.get(prof_name or row["profile"] or "assistant")
        if skills is None:
            skills = sorted(p.name for p in (ws / ".claude" / "skills").glob("*")
                            if p.is_dir() or p.is_symlink())
        skills_mod.mount(ws, skills)
        write_settings(ws, sid, prof)
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

    独立会话/项目属主两处同值；项目子任务指向 <项目根>/tasks/<NN>-<slug>/
    私有任务目录（v0.6 起，不再共享项目 cwd）。不缓存（测试隔离 + 主键查询廉价）。
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


def _next_task_slot(proj_ws: Path, title: str) -> Path:
    """项目根下分配 tasks/<NN>-<slug>/ 任务目录（NN=现有最大序号+1，目录可读且稳定）。"""
    tasks = proj_ws / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    n = 1
    try:
        for p in tasks.iterdir():
            if p.is_dir() and p.name[:2].isdigit():
                n = max(n, int(p.name[:2]) + 1)
    except OSError:
        pass
    base = "-".join(slugify(title).split("-")[:4])[:40] or "task"
    ws = tasks / f"{n:02d}-{base}"
    while ws.exists():                       # 极端撞名（同号重名）：尾号递增
        ws = ws.with_name(f"{ws.name}-{uuid.uuid4().hex[:4]}")
    return ws


def create_session(title: str, prof: profile_mod.Profile, skills: list[str] | None,
                   session_mcp: dict | None = None, *,
                   project_id: str | None = None) -> tuple[str, Path]:
    """建会话（API 入口）：脚手架 + DB 行。返回 (sid, ws)。

    project_id 非空 = 项目子任务：**独立任务目录** <项目根>/tasks/<NN>-<slug>/
    （v0.6 起，旧版共享项目 cwd 的「CC 多窗口语义」废除——PROGRESS/state/
    notes/artifacts/记忆全部任务级，杜绝兄弟任务上下文串扰）。项目根只保留
    共享物：宪法（CLAUDE.md，祖先链加载）+ inputs/。对话历史/引擎会话/SSE
    全按 sid 独立（旧版即如此）。
    """
    from . import db as db_mod

    title = (title or "").strip() or "新任务"
    if project_id:
        with db_mod.conn() as c:
            proj = db_mod.get_project(c, project_id)
        if proj is None:
            raise KeyError(f"project not found: {project_id}")
        pws = Path(proj["workspace"])
        if not (pws / "CLAUDE.md").exists():
            # 项目目录被手删：按项目存的配置整套重建（scaffold 全套）
            scaffold(proj["id"], proj["title"], profile_mod.get(proj["profile"]),
                     json.loads(proj["skills_json"] or "[]"),
                     json.loads(proj["mcp_json"] or "{}"), project=True)
        (pws / "inputs").mkdir(parents=True, exist_ok=True)   # 共享输入目录
        sid = _new_unique_id(title)
        ws = _next_task_slot(pws, title)
        scaffold_task_dir(ws, sid, title, prof,
                          skills if skills is not None
                          else json.loads(proj["skills_json"] or "[]"),
                          session_mcp if session_mcp is not None
                          else json.loads(proj["mcp_json"] or "{}"))
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
