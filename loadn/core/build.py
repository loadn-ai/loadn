"""AgentCore 装配工厂：从配置组装一个可 run_turn 的引擎实例。

CLI 与 SubagentManager/测试共用此入口——保证工具面/权限/钩子/MCP/压缩的
接线只有一处真相。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loadn.core.compactor import Compactor
from loadn.core.loop import AgentCore, LoopSettings
from loadn.core.plan import TaskPlanner
from loadn.core.session import SessionManager
from loadn.core.skills import discover_skills
from loadn.core.subagent import SubagentManager, TaskTool
from loadn.providers import build_provider, provider_config
from loadn.tools import ToolRegistry
from loadn.tools.base import ToolContext
from loadn.util import get_logger

log = get_logger(__name__)


@dataclass
class AgentBundle:
    core: AgentCore
    session: SessionManager
    registry: ToolRegistry
    mcp_conns: list
    supervisor: object | None = None   # ProcessSupervisor（Bash 后台任务/退出钩子）


async def build_agent(cwd: Path, *, session_id: str | None = None,
                      disallow: list[str] | None = None,
                      permission_mode: str = "bypassPermissions",
                      max_turns: int | None = None, no_compact: bool = False,
                      no_plan: bool = False,
                      grind: bool = False,
                      budget_minutes: float | None = None,
                      context_window: int | None = None, enable_task: bool = True,
                      enable_mcp: bool = True, cfg: dict | None = None) -> AgentBundle:
    """组装 AgentCore。

    session_id 给定 = 用该 id（transcript 已存在即续跑，否则 init 补记新档）；
    未给定 = 生成新会话。disallow：工具黑名单（MCP 动态工具同受约束）。
    """
    cwd = Path(cwd)
    cfg = cfg or provider_config()
    disallow = set(disallow or [])

    # P3-5a：loadn.ext 扩展先于一切装配加载——register_provider 进全局
    # 注册面（build_provider 生效）、register_tool/on 后面接线
    from loadn.core import ext as ext_mod
    ext = ext_mod.load_extensions(cwd)
    from loadn import providers as _prov
    for pname, factory in ext.providers.items():
        _prov.register_provider(pname, factory)

    # CC-Fingerprint 工具面整形：伪装通道隐藏 loadn 特有工具（形状出戏）
    # + 注册 CC 名单内的 stub 工具（BashOutput/KillShell 映射真实后台治理）
    from loadn.providers.fingerprint import stealth_mode
    stealth = bool(stealth_mode())
    if stealth:
        disallow.add("InteractiveShell")

    provider = build_provider(cfg)
    registry = ToolRegistry.default(disallow=disallow)
    tools = {name: registry.get(name) for name in registry.names()}
    if stealth:
        from loadn.tools.cc_stubs import cc_stub_tools
        for t in cc_stub_tools():
            tools[t.name] = t

    # 扩展工具（同名=整体替换内置——pi 语义，留痕可见）
    for name, t in ext.tools.items():
        if name in disallow:
            continue
        if name in tools:
            log.info("loadn.ext：扩展整体替换内置工具 %s", name)
        tools[name] = t

    # Skill 工具（发现非空才注册——空 enum 不进装配；AUTO_REGISTER=False）
    skills = discover_skills(cwd)
    if skills and "Skill" not in disallow:
        from loadn.tools.skill import SkillTool
        tools["Skill"] = SkillTool(skills)

    # MCP 动态工具（单个失败降级警告，不阻断会话）。P2 懒加载：超阈值
    # server 的工具不进工具面，经 ToolSearch 按需物化（一次往返拿全 schema）
    mcp_conns: list = []
    mcp_deferred: dict = {}
    if enable_mcp:
        try:
            from loadn.mcp.client import discover
            mcp_tools, mcp_raw_deferred, mcp_conns = await discover(cwd)
            for name, t in mcp_tools.items():
                if name not in disallow:
                    tools[name] = t
            # disallow 预过滤：被禁工具不进延迟索引——enum 不可见、直接
            # 调用当场物化路径（loop）也到不了它，fail-closed 而非调用时报错
            mcp_deferred = {n: t for n, t in mcp_raw_deferred.items()
                            if n not in disallow}
        except Exception as e:  # noqa: BLE001
            log.warning("MCP 发现失败（跳过）：%s", e)
            mcp_deferred = {}
    if mcp_deferred and "ToolSearch" not in disallow:
        from loadn.tools.tool_search import ToolSearchTool
        tools["ToolSearch"] = ToolSearchTool(mcp_deferred, tools,
                                             disallow=set(disallow))
        log.info("ToolSearch 就绪（%d 个 MCP 工具延迟可物化）",
                 len(mcp_deferred))

    session = (SessionManager.resume(session_id, cwd) if session_id
               else SessionManager.create(cwd))

    # Task 子代理（子代理自身构建时 enable_task=False——默认禁递归）
    mgr = None
    if enable_task and "Task" not in disallow:
        from loadn import loadn_home
        from loadn.core.agent_defs import load_agent_defs
        mgr = SubagentManager(
            registry=registry, provider_factory=lambda: build_provider(cfg),
            cwd=cwd, session_id=session.session_id,
            extra_types=load_agent_defs(cwd, loadn_home() / "agents"),
            model_provider_factory=lambda m: build_provider({**cfg, "model": m}))
        tools["Task"] = TaskTool(mgr)
    # P2-4：Runtime Task 注册表面（agent 与宿主都可枚举/取消）
    from loadn.core.task_tools import TaskCancelTool, TaskListTool
    tools["TaskList"] = TaskListTool()
    tools["TaskCancel"] = TaskCancelTool()

    window = context_window or _window_of(cfg.get("model") or "")
    from loadn.supervisor.process import ProcessSupervisor
    supervisor = ProcessSupervisor()
    core = AgentCore(
        provider=provider, tools=tools, session=session, cwd=cwd,
        settings=LoopSettings(max_turns=max_turns, permission_mode=permission_mode,
                              no_compact=no_compact, no_plan=no_plan,
                              grind=grind,
                              budget_s=(budget_minutes * 60
                                        if budget_minutes else None),
                              context_window=window),
        subagents=mgr,
        mcp_deferred=mcp_deferred,
        planner=(TaskPlanner(_planner_provider(cfg))
                 if (mgr is not None and not no_plan) else None),
        ctx=ToolContext(cwd=cwd, workspace=cwd, supervisor=supervisor))
    # Bash 超限全文落盘位置：session scratch 目录（transcript.dir）——不落
    # cwd/logs（会改 git status → 打掉 system 缓存断点）
    core.ctx.extras["scratch_dir"] = str(session.transcript.dir)
    # P3-5a：扩展事件 handler 挂进 HookRunner（AgentCore 已自建 runner）
    if ext.handlers:
        ext_mod.apply_handlers(core.hooks, ext.handlers)
    # 伪装层会话 id（metadata.user_id 的 session 后缀稳定派生；对主 provider
    # 与 planner/子代理的 provider 实例统一注入）
    if hasattr(provider, "_stealth_sid"):
        provider._stealth_sid = session.session_id
    core.compactor = Compactor(provider, small_model=cfg.get("small_model"))
    return AgentBundle(core=core, session=session, registry=registry,
                       mcp_conns=mcp_conns, supervisor=supervisor)


# P1-5：窗口单一真源移至 loadn/models.json（core/models.py 读口）；
# _WINDOW_HINTS 两点猜测 + 静默 200k 的旧形态已废


def _planner_provider(cfg: dict):
    """planner 专用 provider：temperature=0（拆分判定要确定性，主循环不受影响）。"""
    cold = dict(cfg)
    cold["extra"] = {**(cfg.get("extra") or {}), "temperature": 0}
    return build_provider(cold)


def _window_of(model: str) -> int:
    """上下文窗口：目录 > [Nm] 变体覆盖 > 保守 fallback 128k+warning。"""
    from loadn.core import models as models_mod
    return models_mod.window_of(model)
