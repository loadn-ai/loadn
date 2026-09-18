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
from hahaness.core.skills import discover_skills
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

    # CC-Fingerprint 工具面整形：伪装通道隐藏 hahaness 特有工具（形状出戏）
    # + 注册 CC 名单内的 stub 工具（BashOutput/KillShell 映射真实后台治理）
    from hahaness.providers.fingerprint import stealth_mode
    stealth = bool(stealth_mode())
    if stealth:
        disallow.add("InteractiveShell")

    provider = build_provider(cfg)
    registry = ToolRegistry.default(disallow=disallow)
    tools = {name: registry.get(name) for name in registry.names()}
    if stealth:
        from hahaness.tools.cc_stubs import cc_stub_tools
        for t in cc_stub_tools():
            tools[t.name] = t

    # Skill 工具（发现非空才注册——空 enum 不进装配；AUTO_REGISTER=False）
    skills = discover_skills(cwd)
    if skills and "Skill" not in disallow:
        from hahaness.tools.skill import SkillTool
        tools["Skill"] = SkillTool(skills)

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
        from hahaness import hahaness_home
        from hahaness.core.agent_defs import load_agent_defs
        mgr = SubagentManager(
            registry=registry, provider_factory=lambda: build_provider(cfg),
            cwd=cwd, session_id=session.session_id,
            extra_types=load_agent_defs(cwd, hahaness_home() / "agents"),
            model_provider_factory=lambda m: build_provider({**cfg, "model": m}))
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
        planner=(TaskPlanner(_planner_provider(cfg))
                 if (mgr is not None and not no_plan) else None),
        ctx=ToolContext(cwd=cwd, workspace=cwd, supervisor=supervisor))
    # Bash 超限全文落盘位置：session scratch 目录（transcript.dir）——不落
    # cwd/logs（会改 git status → 打掉 system 缓存断点）
    core.ctx.extras["scratch_dir"] = str(session.transcript.dir)
    # 伪装层会话 id（metadata.user_id 的 session 后缀稳定派生；对主 provider
    # 与 planner/子代理的 provider 实例统一注入）
    if hasattr(provider, "_stealth_sid"):
        provider._stealth_sid = session.session_id
    core.compactor = Compactor(provider, small_model=cfg.get("small_model"))
    return AgentBundle(core=core, session=session, registry=registry,
                       mcp_conns=mcp_conns, supervisor=supervisor)


_WINDOW_HINTS = (("[1m]", 1_000_000), ("[2m]", 2_000_000))


def _planner_provider(cfg: dict):
    """planner 专用 provider：temperature=0（拆分判定要确定性，主循环不受影响）。"""
    cold = dict(cfg)
    cold["extra"] = {**(cfg.get("extra") or {}), "temperature": 0}
    return build_provider(cold)


def _window_of(model: str) -> int:
    """按模型名推断上下文窗口（[1m] 变体 → 1M；默认 200k）。"""
    low = (model or "").lower()
    for hint, n in _WINDOW_HINTS:
        if hint in low:
            return n
    return 200_000
