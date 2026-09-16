"""AgentCore 装配工厂：从配置组装一个可 run_turn 的引擎实例。

CLI 与 SubagentManager/测试共用此入口——保证工具面/权限/钩子/MCP/压缩的
接线只有一处真相。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hahaness.core.compactor import Compactor
from hahaness.core.loop import AgentCore, LoopSettings
from hahaness.core.plan import TaskPlanner
from hahaness.core.session import SessionManager
from hahaness.core.subagent import SubagentManager, TaskTool
from hahaness.providers import build_provider, provider_config
from hahaness.tools import ToolRegistry
from hahaness.tools.base import ToolContext
from hahaness.util import get_logger

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
                      context_window: int | None = None, enable_task: bool = True,
                      enable_mcp: bool = True, cfg: dict | None = None) -> AgentBundle:
    """组装 AgentCore。

    session_id 给定 = 用该 id（transcript 已存在即续跑，否则 init 补记新档）；
    未给定 = 生成新会话。disallow：工具黑名单（MCP 动态工具同受约束）。
    """
    cwd = Path(cwd)
    cfg = cfg or provider_config()
    disallow = set(disallow or [])

    provider = build_provider(cfg)
    registry = ToolRegistry.default(disallow=disallow)
    tools = {name: registry.get(name) for name in registry.names()}

    # MCP 动态工具（单个失败降级警告，不阻断会话）
    mcp_conns: list = []
    if enable_mcp:
        try:
            from hahaness.mcp.client import discover
            mcp_tools, mcp_conns = await discover(cwd)
            for name, t in mcp_tools.items():
                if name not in disallow:
                    tools[name] = t
        except Exception as e:  # noqa: BLE001
            log.warning("MCP 发现失败（跳过）：%s", e)

    session = (SessionManager.resume(session_id, cwd) if session_id
               else SessionManager.create(cwd))

    # Task 子代理（子代理自身构建时 enable_task=False——默认禁递归）
    mgr = None
    if enable_task and "Task" not in disallow:
        mgr = SubagentManager(
            registry=registry, provider_factory=lambda: build_provider(cfg),
            cwd=cwd, session_id=session.session_id)
        tools["Task"] = TaskTool(mgr)

    window = context_window or _window_of(cfg.get("model") or "")
    from hahaness.supervisor.process import ProcessSupervisor
    supervisor = ProcessSupervisor()
    core = AgentCore(
        provider=provider, tools=tools, session=session, cwd=cwd,
        settings=LoopSettings(max_turns=max_turns, permission_mode=permission_mode,
                              no_compact=no_compact, no_plan=no_plan,
                              context_window=window),
        subagents=mgr,
        planner=(TaskPlanner(provider) if (mgr is not None and not no_plan) else None),
        ctx=ToolContext(cwd=cwd, workspace=cwd, supervisor=supervisor))
    core.compactor = Compactor(provider)
    return AgentBundle(core=core, session=session, registry=registry,
                       mcp_conns=mcp_conns, supervisor=supervisor)


_WINDOW_HINTS = (("[1m]", 1_000_000), ("[2m]", 2_000_000))


def _window_of(model: str) -> int:
    """按模型名推断上下文窗口（[1m] 变体 → 1M；默认 200k）。"""
    low = (model or "").lower()
    for hint, n in _WINDOW_HINTS:
        if hint in low:
            return n
    return 200_000
