"""SubagentManager（工程详设 §4.7）：Task 工具 → 独立 AgentCore 实例池。

上下文隔离是它存在的全部意义：子代理独立 transcript/独立压缩，主上下文
只进 final text（≤TASK_OUTPUT_MAX_CHARS）。默认工具集不含 Task（禁递归）；
信号量并发 SUBAGENT_CONCURRENCY。用途型 subagent_type：
  general（默认子集）| explore（只读扇出搜索）| plan（架构设计）
+ 自定义类型（.claude/agents/*.md 经 agent_defs.load_agent_defs 注入
extra_types，项目级同名覆盖内建；frontmatter 可带 tools/model）。
planner（core/plan.py）仍只产内建三类型——自定义类型不经并行拆分调度。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from loadn.constants import SUBAGENT_CONCURRENCY, TASK_OUTPUT_MAX_CHARS
from loadn.core.loop import AgentCore, LoopSettings
from loadn.tools.base import Tool, ToolContext, ToolError

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
                 session_id: str, home: Path | None = None,
                 extra_types: dict | None = None,
                 model_provider_factory=None) -> None:
        # registry：loadn.tools.ToolRegistry（拿工具实例）
        # provider_factory：() -> Provider（子代理独立 provider 连接）
        # extra_types：自定义类型（.claude/agents/*.md，项目级覆盖同名内建）
        # model_provider_factory：(model: str) -> Provider（frontmatter 带
        #   model 的自定义类型用；None = 回落 provider_factory）
        self.registry = registry
        self.provider_factory = provider_factory
        self.cwd = Path(cwd)
        self.session_id = session_id
        self.home = home
        self.types = {**SUBAGENT_TYPES, **(extra_types or {})}
        self.model_provider_factory = model_provider_factory
        self._sem = asyncio.Semaphore(SUBAGENT_CONCURRENCY)
        self._n = 0

    async def task(self, prompt: str, subagent_type: str = "general",
                   description: str = "", emit=None) -> str:
        """跑一个子代理。emit 非空时外发 claude 形 Task 卡片事件（开始/结束）
        ——宿主 UI 渲染成工具卡，并行扇出期不再黑屏（2026-09-18：调研任务拆
        3 子代理跑 7 分钟零反馈的实况归因）。"""
        conf = self.types.get(subagent_type, self.types["general"])
        from loadn.core.session import SessionManager

        async with self._sem:
            self._n += 1
            sub_sid = f"{self.session_id}-sub{self._n}"
            card_id = f"sub_{self._n}"
            if emit is not None:
                # 形状纪律：主 emit 是 StreamJsonEmitter——message 必须是
                # Message 对象、块必须带 to_dict()（裸 dict 会 AttributeError
                # 崩 turn，2026-09-18 比赛调研 6 连崩的实况归因）
                from loadn.types import Message as _Msg
                from loadn.types import ToolUseBlock as _TUB
                await _fire_event(emit, {"type": "assistant", "message": _Msg(
                    role="assistant",
                    content=[_TUB(id=card_id, name="Task",
                                   input={"prompt": prompt[:300],
                                          "subagent_type": subagent_type,
                                          "description": description})])})
            session = SessionManager.create(
                self.cwd, title=description or sub_sid, home=self.home,
                parent_id=self.session_id, session_id=sub_sid)
            # 自定义类型可带 tools=None（缺省通用面）/model（独立 provider）
            tool_names = conf.get("tools") or GENERAL_TOOLS
            tools = {name: self.registry.get(name) for name in tool_names
                     if self.registry.get(name) is not None}
            model = conf.get("model")
            provider = (self.model_provider_factory(model)
                        if (model and self.model_provider_factory is not None)
                        else self.provider_factory())
            core = AgentCore(
                provider=provider, tools=tools, session=session,
                cwd=self.cwd,
                settings=LoopSettings(max_turns=50, no_compact=False),
                ctx=ToolContext(cwd=self.cwd))
            # 用途 system 附加（ContextAssembler 的宪法之上）
            core.assembler = _PatchedAssembler(
                core.assembler, conf["system_add"])

            # 归属转发：把子代理的工具活动（tool_use/tool_result）打上
            # 「子N·」标签转发到主 emit——用户能看到每个子任务在干什么、
            # 干到哪一步（thinking/text 噪声不转——多子代理交错会糊成一团）。
            # 注意形状：主 emit 是 StreamJsonEmitter，assistant 分支要求
            # message.content 是带 to_dict() 的块对象（ToolUseBlock）
            fwd_emit = None
            if emit is not None:
                from loadn.types import Message as _Msg
                from loadn.types import ToolUseBlock as _TUB
                tag = f"子{self._n}"

                async def fwd_emit(ev: dict) -> None:
                    t = ev.get("type")
                    if t == "assistant":
                        blocks = [b for b in ev["message"].content
                                  if isinstance(b, _TUB)]
                        if blocks:
                            await _fire_event(emit, {"type": "assistant", "message": _Msg(
                                role="assistant",
                                content=[_TUB(id=b.id, name=f"{tag}·{b.name}",
                                              input=b.input) for b in blocks])})
                    elif t == "tool_result":
                        await _fire_event(emit, {
                            "type": "tool_result",
                            "block": ev["block"],
                            "name": f"{tag}·{ev.get('name') or '?'}"})

            summary = await core.run_turn(prompt, emit=fwd_emit,
                                          stream_events=False)
        text = summary.text or ""
        if summary.subtype != "success":
            err = f"｜{summary.error}" if summary.error else ""
            text = f"（子代理未正常结束：{summary.subtype}{err}）\n{text}"
        if len(text) > TASK_OUTPUT_MAX_CHARS:
            text = text[:TASK_OUTPUT_MAX_CHARS] + "…[截断]"
        if emit is not None:
            # 结果卡走 tool_result 事件形状（StreamJsonEmitter 无 "user"
            # 分支——裸 user 事件会被静默丢弃，宿主永远看不到结果卡）
            from loadn.types import ToolResultBlock as _TRB
            await _fire_event(emit, {
                "type": "tool_result",
                "block": _TRB(tool_use_id=card_id, content=text[:2000],
                              is_error=summary.subtype != "success"),
                "name": "Task"})
        return text

    async def gather(self, subtasks: list, emit=None) -> list[str]:
        """并行扇出一组子任务（TaskPlanner 调度入口；信号量内部限流）。

        subtasks 是 core.plan.SubTask 列表。单个子代理失败不拖垮整批
        （task() 内部已把失败转成带标注的文本返回）。emit 透传给各子任务
        外发 Task 卡片事件。
        """
        import asyncio as _aio

        return list(await _aio.gather(
            *(self.task(st.prompt, subagent_type=st.subagent_type,
                        description=st.prompt[:60], emit=emit)
              for st in subtasks)))


async def _fire_event(emit, ev: dict) -> None:
    """emit 兼容同步/异步回调（与 loop._fire 同语义，避免循环导入）。"""
    if emit is None:
        return
    r = emit(ev)
    if hasattr(r, "__await__"):
        await r


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

    def __init__(self, manager: SubagentManager) -> None:
        self.manager = manager
        types = manager.types
        # 实例级 schema：enum 随自定义类型（.claude/agents）动态化
        self.input_schema = {
            "type": "object",
            "properties": {
                "prompt": {"type": "string",
                           "description": "子任务完整描述（自包含：目标/边界/交付物）"},
                "subagent_type": {
                    "type": "string", "enum": sorted(types),
                    "description": self._type_help(types)},
                "description": {"type": "string", "description": "3-5 词用途标签"},
            },
            "required": ["prompt"],
        }

    @staticmethod
    def _type_help(types: dict) -> str:
        base = ("general=通用 | explore=只读搜索 | plan=架构设计")
        custom = [n for n in types if n not in SUBAGENT_TYPES]
        if custom:
            base += " | 自定义：" + "、".join(sorted(custom))
        return base

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("prompt 不能为空")
        return await self.manager.task(
            prompt, subagent_type=str(args.get("subagent_type") or "general"),
            description=str(args.get("description") or ""))
