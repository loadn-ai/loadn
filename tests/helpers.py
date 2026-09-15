"""测试共用：脚本化 provider 与最小工具（不依赖 providers.fake / tools 实现）。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from hahaness.providers import Chunk
from hahaness.tools.base import Tool, ToolContext, ToolError
from hahaness.types import Message, ToolDef


def text_round(text: str, model: str = "fake") -> list[Chunk]:
    return [Chunk(kind="text_delta", text=text),
            Chunk(kind="usage", usage={"input_tokens": 100, "cache_read_input_tokens": 10},
                  model=model),
            Chunk(kind="stop", usage={"input_tokens": 100, "output_tokens": 20,
                                      "cache_read_input_tokens": 10,
                                      "cache_creation_input_tokens": 5},
                  stop_reason="end_turn")]


def tool_round(tool_id: str, name: str, args: dict, model: str = "fake") -> list[Chunk]:
    import json as _json
    raw = _json.dumps(args, ensure_ascii=False)
    return [Chunk(kind="usage", usage={"input_tokens": 100}, model=model),
            Chunk(kind="input_json_delta", tool_use_id=tool_id, tool_name=name,
                  partial_json=raw[:len(raw) // 2]),
            Chunk(kind="input_json_delta", tool_use_id=tool_id,
                  partial_json=raw[len(raw) // 2:]),
            Chunk(kind="stop", usage={"input_tokens": 100, "output_tokens": 30},
                  stop_reason="tool_use")]


class ScriptedProvider:
    """rounds[i] = 第 i+1 次 chat 的 chunk 列表；耗尽后回放 "(scripted done)"。"""

    def __init__(self, rounds: list[list[Chunk]]) -> None:
        self.rounds = rounds
        self.i = 0
        self.calls: list[list[Message]] = []
        self.model_name = "scripted"

    async def chat(self, messages: list[Message], tools: list[ToolDef],
                   system: str, *, stream: bool = True) -> AsyncIterator[Chunk]:
        self.calls.append(list(messages))
        chunks = self.rounds[self.i] if self.i < len(self.rounds) else text_round("(scripted done)")
        self.i += 1
        for c in chunks:
            if c.kind == "error":
                yield c
                return
            yield c


class EchoTool(Tool):
    name = "Echo"
    description = "回显输入"
    input_schema = {"type": "object", "properties": {"msg": {"type": "string"}},
                    "required": ["msg"]}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        return f"echo: {args.get('msg')}"


class BoomTool(Tool):
    name = "Boom"
    description = "总是抛 ToolError"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> str:
        raise ToolError("炸了（预期内）")
