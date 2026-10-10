"""ToolSearch——MCP 工具懒加载的按需物化面（P2，skills 懒加载同构）。

单 server 工具数超阈值（MCP_LAZY_TOOL_THRESHOLD）时不全量注入 API tools
面（Docker MCP server 实测单家可吃 12.6 万 token），只注册本工具：参数
enum 即索引（name + description 首句 + 来源 server）。模型点名 → 物化进
core.tools（loop 每次 LLM 调用前重建工具面，下一次请求即带上）并在结果里
返回完整 inputSchema——同轮即可正确构造调用，禁止两跳猜参数。

会话内 schema 缓存：延迟索引持有 MCPTool 全量 spec（连接与 discover 同
寿命），物化零重取。不做跨会话持久缓存（边界）。

AUTO_REGISTER = False：enum 依赖 discover 结果，由 build_agent 在延迟
非空且未被 disallow 时手动注册（与 Skill 同范式）。
"""
from __future__ import annotations

import json

from loadn.tools.base import Tool, ToolContext, ToolError, disallow_matches, mcp_redirect_note

AUTO_REGISTER = False

# 索引行形态：name：description 首句（截 80 字）@server
_DESC_CLIP = 80


def _first_sentence(desc: str) -> str:
    """description 首句（中英句读皆断；无句读取整段截断）。"""
    for sep in ("。", "．", ".", "！", "!", "？", "?", "\n"):
        if sep in desc:
            head = desc.split(sep, 1)[0]
            if head.strip():
                desc = head
                break
    desc = " ".join(desc.split())          # 压平换行/连续空白
    return desc[:_DESC_CLIP]


class ToolSearchTool(Tool):
    """按名称物化延迟加载的 MCP 工具，返回其完整参数 schema。"""

    name = "ToolSearch"
    description = (
        "加载一个 MCP 工具的完整参数定义。参数 enum 列出当前未注入工具面的"
        "全部 MCP 工具（名称形如 mcp__<server>__<tool>）。要用其中某个：先调"
        "本工具拿它的完整 inputSchema（一次往返），再按 schema 构造参数直接"
        "调用该工具——无需试错猜参数。注意：这些工具运行在各自 MCP server 的"
        "独立环境里（文件系统/网络可能与本会话不同）；本会话的文件与命令操作"
        "请直接用内建工具（Bash/Read/Write 等已在工具面）。"
    )
    read_only = True

    def __init__(self, index: dict, tools_dict: dict,
                 disallow: set[str] | None = None) -> None:
        """index: {工具全名: MCPTool}（discover 的 deferred）；
        tools_dict: AgentCore.tools 同一 dict 对象——物化即原地插入，
        下一次 LLM 调用的工具面自动带上。disallow：注册黑名单（物化门，
        与 build_agent 注册判同源——延迟不得绕过 disallow）。"""
        self.index = index
        self.tools_dict = tools_dict
        self.disallow = set(disallow or ())
        lines = [f"{n}：{_first_sentence(t.description)}@{t.conn.name}"
                 for n, t in sorted(index.items())]
        self.input_schema = {
            "type": "object",
            "properties": {
                "tool": {
                    "type": "string",
                    "enum": sorted(index),
                    "description": "要加载的 MCP 工具全名。索引（名称：首句"
                                   "@server）：\n" + "\n".join(lines),
                },
            },
            "required": ["tool"],
        }

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        name = args.get("tool")
        if not name or not isinstance(name, str):
            raise ToolError("缺少必填参数 tool（延迟索引中的工具全名，见 enum）")
        if disallow_matches(name, self.disallow):
            # off 档执行域门（--disallowedTools，含通配类级）：build 预过滤
            # 后该名不在索引（盲猜），或直构索引在场——一律在物化点拦截并
            # 给改道路标（第 4 起实证 2026-10-10：deny 后模型原地重试 3 次
            # 放弃，从未试内建 Bash——裸拦截必须自带「该用什么」）。
            raise ToolError(f"工具 {name} 被会话策略禁用（disallow），"
                            "不可物化。" + mcp_redirect_note(self.tools_dict))
        t = self.index.get(name)
        if t is None:
            avail = sorted(set(self.index) - self.disallow)[:20]
            raise ToolError(f"未知或已全量注入的工具：{name}"
                            f"（延迟索引可用：{avail}）")
        if name not in self.tools_dict:
            self.tools_dict[name] = t       # 物化（下一次 LLM 调用进工具面）
            log_note = "已物化，下一次请求即可调用"
        else:
            log_note = "此前已物化"
        return (f"工具 {name}（来源 server：{t.conn.name}）。{log_note}。"
                "完整参数 schema：\n"
                + json.dumps(t.input_schema, ensure_ascii=False, indent=2))


# build_agent 手动注册（AUTO_REGISTER=False）
