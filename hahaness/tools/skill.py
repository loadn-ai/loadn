"""Skill 工具——按需注入 SKILL.md 正文（对位 claude CLI 的 Skill 调度）。

AUTO_REGISTER = False：enum 依赖工作区发现结果，由 build_agent 在发现
非空且未被 disallow 时手动注册（与 Task 同范式——空 enum 不进默认装配）。
"""
from __future__ import annotations

from hahaness.core.skills import SkillInfo, load_skill_body
from hahaness.tools.base import Tool, ToolContext, ToolError

AUTO_REGISTER = False


class SkillTool(Tool):
    """加载指定 skill 的正文进上下文（比索引+Read 少一个来回）。"""

    name = "Skill"
    description = (
        "加载一个 skill 的完整指令正文。可用的 skill 名单见 system 提示"
        "的索引；命中任务场景时先调本工具拿正文，再按其指引干活。"
    )
    read_only = True

    def __init__(self, skills: dict[str, SkillInfo]) -> None:
        self.skills = skills
        self.input_schema = {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": sorted(skills),
                    "description": "要加载的 skill 名（见索引）",
                },
                "args": {"type": "string",
                         "description": "传给 skill 的参数（替换正文中的 $ARGUMENTS）"},
            },
            "required": ["command"],
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        name = args.get("command")
        if not name or not isinstance(name, str):
            raise ToolError("缺少必填参数 command（skill 名，见索引）")
        info = self.skills.get(name)
        if info is None:
            raise ToolError(f"未知 skill：{name}（可用：{sorted(self.skills)}）")
        body = load_skill_body(info, args=str(args.get("args") or ""))
        head = f"（skill：{name}"
        if args.get("args"):
            head += f"，args={args['args']}"
        head += "）\n"
        return head + body


# build_agent 手动注册（AUTO_REGISTER=False）
