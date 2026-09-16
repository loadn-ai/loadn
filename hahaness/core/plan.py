"""TaskPlanner（v0.2：并行拆分调度）——任务前置评估层。

接到任务先过一次便宜的规划调用，判定「可并行独立子任务集 / 单链流程」：
可拆 → SubagentManager 并行扇出，子结果注入主循环收敛（主 agent 保留
整合/补做/验证的完整能力）；不可拆 / 解析失败 → 直跑现有循环（零风险降级）。

拆分护栏（防过度拆分——同 cwd 写操作会互相踩踏）：
- 子任务上限 PLAN_MAX_SUBTASKS（默认 3，信号量 4 留余量）
- 只拆「互相独立、可分别验证」的部分；单链长流程（编译/训练）不拆——
  那类耗时是物理的，正确解法是 Bash run_in_background，不是并行子代理
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from hahaness.types import Message, TextBlock
from hahaness.util import get_logger

log = get_logger(__name__)

PLAN_PROMPT = """你是任务规划器。评估下面的任务是否值得拆成多个【互相独立】的并行子任务。

拆分条件（全部满足才拆）：
- 任务包含 2-{max_sub} 个互相独立、可分别完成可分别验证的部分（不同文件/不同方面/不同来源）
- 子任务之间没有共享可变状态（不会改同一批文件）
- 每个子任务单独跑完的产物能被明确描述

不要拆的情况：单一长流程（编译/安装/训练要跑很久的）；强顺序依赖（B 必须等 A）；
一句话能答的问答；改动高度耦合的同一处代码。长流程的正确解法是把长命令放后台跑，
不是拆子代理。

只输出 JSON（不要任何其他文字）：
{{"parallelizable": true, "subtasks": [{{"prompt": "子任务完整描述（自包含）", "type": "general"}}], "reason": "一句话依据"}}
或
{{"parallelizable": false, "reason": "一句话依据"}}

任务：{task}"""


@dataclass
class SubTask:
    prompt: str
    subagent_type: str = "general"   # general | explore


@dataclass
class Plan:
    parallelizable: bool = False
    subtasks: list[SubTask] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def serial(cls, reason: str = "") -> Plan:
        return cls(parallelizable=False, reason=reason)


class TaskPlanner:
    """一次规划调用 → Plan。任何失败都降级为 serial（直跑）。"""

    def __init__(self, provider, max_subtasks: int = 3) -> None:
        self.provider = provider
        self.max_subtasks = max_subtasks

    async def plan(self, task: str) -> Plan:
        try:
            chunks = self.provider.chat(
                [Message(role="user", content=[TextBlock(
                    text=PLAN_PROMPT.format(max_sub=self.max_subtasks, task=task[:8000]))])],
                [], "你是任务规划器，只输出 JSON。")
            text = ""
            async for c in chunks:
                if c.kind == "text_delta":
                    text += c.text
                elif c.kind == "error":
                    return Plan.serial(f"planner error: {c.error[:120]}")
            return self._parse(text, task)
        except Exception as e:  # noqa: BLE001 — 规划失败一律降级直跑
            log.warning("planner 异常（降级直跑）：%s", e)
            return Plan.serial(f"planner exception: {e!r}")

    def _parse(self, text: str, task: str) -> Plan:
        data = _extract_json(text)
        if not isinstance(data, dict):
            return Plan.serial("planner 输出非 JSON")
        if not data.get("parallelizable"):
            return Plan.serial(str(data.get("reason") or "")[:200])
        subs: list[SubTask] = []
        for item in (data.get("subtasks") or [])[: self.max_subtasks]:
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("prompt") or "").strip()
            if not prompt:
                continue
            stype = str(item.get("type") or "general")
            if stype not in ("general", "explore", "plan"):
                stype = "general"
            subs.append(SubTask(prompt=prompt, subagent_type=stype))
        # 单个子任务没有并行意义；写敏感的 plan 型不并行扇出（只留 general/explore）
        if len(subs) < 2:
            return Plan.serial("拆分后子任务不足 2 个")
        return Plan(parallelizable=True, subtasks=subs,
                    reason=str(data.get("reason") or "")[:200])


def _extract_json(text: str):
    """从模型输出抽第一个 JSON 对象（容忍 markdown 围栏/前后缀文本）。"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    start = -1
    return None
