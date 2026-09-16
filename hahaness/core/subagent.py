"""SubagentManager（工程详设 §4.7）：Task 工具 → 独立 AgentCore 实例池。

上下文隔离是它存在的全部意义：子代理独立 transcript/独立压缩，主上下文
只进 final text（≤TASK_OUTPUT_MAX_CHARS）。默认工具集不含 Task（禁递归）；
信号量并发 SUBAGENT_CONCURRENCY。用途型 subagent_type：
  general（默认子集）| explore（只读扇出搜索）| plan（架构设计）。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from hahaness.constants import SUBAGENT_CONCURRENCY, TASK_OUTPUT_MAX_CHARS
from hahaness.core.loop import AgentCore, LoopSettings
from hahaness.tools.base import Tool, ToolContext, ToolError

SUBAGENT_PROMPT = """你是被派来独立完成一项子任务的 agent。纪律：
- 你的最终回复就是给派发方的全部交付物——只返回最终结果原文（结论/清单/
  路径/数据），不寒暄、不解释过程、不附工作日志。
- 结果要自包含：派发方看不到你的中间过程，需要的证据（文件路径、行号、
  数字）必须写进最终回复。
- 完成即收束；无法完成时返回「阻塞：<原因>」一行。
"""

# 各用途的工具面（全部无 Task——禁递归）
GENERAL_TOOLS = ["Bash", "Read", "Write", "Edit", "Grep", "Glob",
                 "WebFetch", "WebSearch", "TodoWrite"]
EXPLORE_TOOLS = ["Read", "Grep", "Glob", "Bash", "WebFetch"]   # Bash 只读用法靠提示约束
PLAN_TOOLS = ["Read", "Grep", "Glob", "WebFetch"]

SUBAGENT_TYPES = {
    "general": {"tools": GENERAL_TOOLS,
                "system_add": "你是通用子代理，动手完成子任务并返回结果。"},
    "explore": {"tools": EXPLORE_TOOLS,
                "system_add": "你是只读探索子代理：扇出搜索、快速定位，只返回"
                              "结论与出处（文件:行号），不修改任何文件。"},
    "plan": {"tools": PLAN_TOOLS,
             "system_add": "你是架构设计子代理：产出实施计划（步骤/文件/风险），"
                           "只读调研，不修改任何文件。"},
}


class SubagentManager:
    def __init__(self, *, registry, provider_factory, cwd: Path,
                 session_id: str, home: Path | None = None) -> None:
        # registry：hahaness.tools.ToolRegistry（拿工具实例）
        # provider_factory：() -> Provider（子代理独立 provider 连接）
        self.registry = registry
        self.provider_factory = provider_factory
        self.cwd = Path(cwd)
        self.session_id = session_id
        self.home = home
        self._sem = asyncio.Semaphore(SUBAGENT_CONCURRENCY)
        self._n = 0

    async def task(self, prompt: str, subagent_type: str = "general",
                   description: str = "") -> str:
        conf = SUBAGENT_TYPES.get(subagent_type,
                                  SUBAGENT_TYPES["general"])
        from hahaness.core.session import SessionManager

        async with self._sem:
            self._n += 1
            sub_sid = f"{self.session_id}-sub{self._n}"
            session = SessionManager.create(
                self.cwd, title=description or sub_sid, home=self.home,
                parent_id=self.session_id, session_id=sub_sid)
            tools = {name: self.registry.get(name) for name in conf["tools"]
                     if self.registry.get(name) is not None}
            core = AgentCore(
                provider=self.provider_factory(), tools=tools, session=session,
                cwd=self.cwd,
                settings=LoopSettings(max_turns=50, no_compact=False),
                ctx=ToolContext(cwd=self.cwd))
            # 用途 system 附加（ContextAssembler 的宪法之上）
            core.assembler = _PatchedAssembler(
                core.assembler, conf["system_add"])
            summary = await core.run_turn(prompt)
        text = summary.text or ""
        if summary.subtype != "success":
            err = f"｜{summary.error}" if summary.error else ""
            text = f"（子代理未正常结束：{summary.subtype}{err}）\n{text}"
        if len(text) > TASK_OUTPUT_MAX_CHARS:
            text = text[:TASK_OUTPUT_MAX_CHARS] + "…[截断]"
        return text

    async def gather(self, subtasks: list) -> list[str]:
        """并行扇出一组子任务（TaskPlanner 调度入口；信号量内部限流）。

        subtasks 是 core.plan.SubTask 列表。单个子代理失败不拖垮整批
        （task() 内部已把失败转成带标注的文本返回）。
        """
        import asyncio as _aio

        return list(await _aio.gather(
            *(self.task(st.prompt, subagent_type=st.subagent_type,
                        description=st.prompt[:60]) for st in subtasks)))


class _PatchedAssembler:
    """用途 system 附加的薄包装（explore/plan 的只读纪律）。"""

    def __init__(self, inner, extra: str) -> None:
        self._inner = inner
        self._extra = extra

    def build(self, **kw) -> str:
        return SUBAGENT_PROMPT.strip() + "\n\n" + self._extra + "\n\n---\n" \
            + self._inner.build(**kw)


class TaskTool(Tool):
    """Task 工具本体（由 AgentCore 装配时注册；tools/task.py 的占位不进注册表）。"""

    name = "Task"
    description = ("派子代理独立完成子任务（独立上下文，只回传最终结果）。"
                   "搜索/调研/方案设计优先用它，避免污染主上下文。")
    input_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "子任务完整描述（自包含：目标/边界/交付物）"},
            "subagent_type": {"type": "string", "enum": list(SUBAGENT_TYPES),
                              "description": "general=通用 | explore=只读搜索 | plan=架构设计"},
            "description": {"type": "string", "description": "3-5 词用途标签"},
        },
        "required": ["prompt"],
    }

    def __init__(self, manager: SubagentManager) -> None:
        self.manager = manager

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("prompt 不能为空")
        return await self.manager.task(
            prompt, subagent_type=str(args.get("subagent_type") or "general"),
            description=str(args.get("description") or ""))
