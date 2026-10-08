"""会话工作区：脚手架、宪法渲染、状态双轨（承袭前身/workspace.py）。

每个会话一个目录 workspace/<sid>/，无头会话 cwd 即此目录——CLAUDE.md
宪法自动加载，"宪法随会话走，不依赖全局安装"。
"""
from __future__ import annotations

import json
import os
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


def _exec_env_block() -> str:
    """执行环境说明（十二轮修）：内建 Bash 与 mcp__sandbox__* 是**两个
    执行域**——模型不知道会踩错（实证：直跑档位下任务选了 sandbox MCP 的
    bash 进了外置容器，找不到宿主代码仓）。按档位渲染路标。"""
    try:
        from .security.sandbox import resolve_tier
        eff, _why = resolve_tier()
    except Exception:                                  # noqa: BLE001
        eff, _why = "off", ""
    if eff in ("off", ""):
        return (
            "- 本会话**直跑档位**：内建 Bash/Read/Write/Grep 就在**宿主机**，"
            "cwd 是本工作区，宿主全机可访问（/data / /mnt / /home 等真实路径）。\n"
            "- `mcp__sandbox__*` 是**外置资源容器**（独立文件系统，home 在 "
            "/home/gem——里面没有宿主机代码/数据）。它只是浏览器/OCR 等资源"
            "桥接，**要读本机代码或文件时绝不用它**——用内建 Bash。\n"
            "- 两者井水不犯河水：在 sandbox 容器里找不到的路径≠路径不存在，"
            "先回到内建 Bash 再下结论。")
    return (
        f"- 本会话**隔离档位（{eff}）**：内建 Bash 在平台沙箱内——宿主文件"
        "系统按沙箱挂载表可见（工作区可写，其余按只读/不可见）。\n"
        "- `mcp__sandbox__*` 是**另一个外置资源容器**（独立文件系统）——"
        "与沙箱不同域，只作浏览器/OCR 等资源桥接。\n"
        "- 需要沙箱外的宿主资源时：声明需求停下等用户，不要在两个容器间"
        "来回猜路径。")


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
        "{{EXEC_ENV}}": _exec_env_block(),
    }
    out = tmpl
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def _sid_of_ws(ws: Path) -> str:
    """ws → 会话 id（browser MCP 的审批回调面用）。查不到回落 ws.name。"""
    try:
        from . import db as db_mod
        with db_mod.conn() as c:
            row = c.execute(
                "SELECT id FROM sessions WHERE workspace=? "
                "ORDER BY created_at DESC LIMIT 1", (str(ws),)).fetchone()
        return row["id"] if row else ws.name
    except Exception:                                  # noqa: BLE001
        return ws.name


def write_mcp_json(ws: Path, session_mcp: dict | None) -> None:
    """全局 config.mcp.servers + 会话级覆盖 合并落 ws/.mcp.json（标准项目级格式）。

    W4/B3：stdio server 的 command+args 记哈希锁（.mcp-lock.json）——
    变更（rug pull）≠ 原哈希 → 审计 policy_change + 日志显著告警。
    """
    import hashlib
    merged = dict(CONFIG.mcp.servers or {})
    merged.update(session_mcp or {})
    # P3-4：codemode 受限执行域（默认关；开启即注入 codemode.run）
    if CONFIG.security.codemode_enabled and "codemode" not in merged:
        merged["codemode"] = {
            "command": str(Path(sys.executable).parent / "loadn-web"),
            "args": ["_codemode-mcp"],
        }
    # P2-5：浏览器 CUA（平台侧 MCP server 注入——引擎零改动拿到 browser.*）
    if "browser" not in merged and CONFIG.resources.cdp_url:
        merged["browser"] = {
            "command": str(Path(sys.executable).parent / "loadn-web"),
            "args": ["_browser-mcp"],
            # P13 敏感冻结审批需要 sid。二轮修#5：项目子任务的 ws.name 是
            # "01-slug" 不是 sid——查 DB 拿真 sid（create 后 session 行必有）
            "env": {"LOADN_BROWSER_SID": _sid_of_ws(ws)},
        }
    # P3-3：LSP 诊断回注（平台侧 MCP server——引擎拿到 mcp__lsp__diagnostics）
    if CONFIG.security.lsp_enabled and "lsp" not in merged:
        merged["lsp"] = {
            "command": str(Path(sys.executable).parent / "loadn-web"),
            "args": ["_lsp-mcp"],
        }
    # 会话级禁用哨兵：值 False = 本会话关掉这个全局 server（属性面板三态
    # 切换）——从合并结果剔除。真实 server 配置恒为 truthy dict，不会误伤。
    # 须在注入门**之后**跑：先剔除后注入会被注入门补回（M7 突变对赌实证
    # 的真 bug——本会话关 codemode 无效）
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
                from .security.audit import audit as _audit
                _audit("policy_change",
                       {"what": "mcp_command_changed", "server": name,
                        "old": old[name], "new": h})
        lp.write_text(json.dumps(locks, ensure_ascii=False, indent=2))
    else:
        p.unlink(missing_ok=True)
    write_egress_snapshot(ws)


def write_egress_snapshot(ws: Path, mode: str | None = None) -> None:
    """会话 egress 快照（.loadn/egress.json）——hook 门与 proxy 对齐的桥。

    背景：bwrap 沙箱内 policy-check hook 读不到数据根 config.yaml（挂载
    矩阵不含它），会拿出厂默认白名单误拦已放行的域。快照落 ws（rw 挂载
    天然可见），hook 回退链「快照 > CONFIG」；全局白名单/档位变更时
    rematerialize_sessions 刷新全部活跃会话（会话 params.egress 档由
    调用方并入 mode 传入）。**只读快照**——agent 改它不生效（proxy 才是
    真门）。
    """
    from .config import CONFIG
    from .security.net_policy import normalized_allow
    merged = mode if mode in ("off", "warn", "enforce") else None
    payload = {"mode": merged or str(CONFIG.security.egress_mode),
               "allow": normalized_allow(CONFIG.security.egress_allow),
               "updated_at": iso()}
    d = ws / ".loadn"
    d.mkdir(exist_ok=True)
    (d / "egress.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


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
        # 自定义服务指路（资源页「＋新增服务」）：LOADN_SVC_<NAME>_URL。
        # 密钥不注入——凭证不进沙箱/引擎 env 的既定边界（vault svc:<name>
        # 平台侧保管；agent 走 $LOADN CLI 或平台代理，鉴权路径后续按需接）
        **{f"LOADN_SVC_{s['name'].upper().replace('-', '_')}_URL": str(s["url"])
           for s in (CONFIG.resources.custom_services or [])
           if isinstance(s, dict) and s.get("name") and s.get("url")},
        # 跨项目只读数据共享指路（bwrap 档 ro-bind 同路径；off 档无挂载
        # 边界但路径本可读——env 让 agent 知道 sanctioned 的共享面在哪。
        # resolve 与 _shared_binds 挂载点同口径——symlink 路径不悬空）
        **({"LOADN_SHARED_RO": os.pathsep.join(
            str(Path(p).expanduser().resolve())
            for p in CONFIG.security.shared_readonly
            if Path(p).expanduser().exists())}
           if CONFIG.security.shared_readonly else {}),
        # 宿主机资源桥接全量指路（path:mode；ro=只读共享、rw=可写授权、
        # dev=设备节点）。与 _shared_binds 挂载同源——agent 据此知道哪些
        # 宿主资源是被显式授权可用的
        **({"LOADN_HOST_BRIDGES": os.pathsep.join(
            f"{Path(b['path']).expanduser()}:{b.get('mode', 'ro')}"
            for b in CONFIG.security.resource_bridges
            if str(b.get("path") or "").strip())}
           if CONFIG.security.resource_bridges else {}),
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
    # P0-2 信任门：物化的项目级 settings 对引擎是「项目资源」——此刻同步
    # admit（webui 与引擎共享 LOADN_HOME，trust.json 互通），否则引擎把
    # 平台自己的安全钩子当未信任资源降级跳过。agent 后续改动这些文件 →
    # 摘要不匹配 → 引擎按未信任处理（fail-closed 方向正确）。rerender
    # 每次重物化都刷新摘要，自愈。
    try:
        from loadn import truststore as _truststore
        _truststore.admit(ws)
    except Exception:                                   # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "信任门 admit 失败（引擎将按未信任降级项目级 settings）：%s", ws,
            exc_info=True)


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
    # W5.5：会话 canary 蜜罐（泄露指示物；命中即熔断）。
    # security.canary_enabled=False → 不布放（检测面同步关闭，fail-soft）
    try:
        if not CONFIG.security.canary_enabled:
            return
        from .security import canary as _canary
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
