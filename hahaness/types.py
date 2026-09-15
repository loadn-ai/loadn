"""消息层与工具定义的核心数据结构（对齐 Anthropic content blocks）。

持久化（transcript JSONL / stream-json 输出）即此结构——序列化用 dict 形态
（to_dict/from_dict），字段名与 claude CLI stream-json 事件保持一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]


@dataclass
class TextBlock:
    text: str

    def to_dict(self) -> dict:
        return {"type": "text", "text": self.text}

    @classmethod
    def from_dict(cls, d: dict) -> TextBlock:
        return cls(text=d.get("text") or "")


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str = ""   # Anthropic 延续性签名；openai-compat 侧为空

    def to_dict(self) -> dict:
        out = {"type": "thinking", "thinking": self.thinking}
        if self.signature:
            out["signature"] = self.signature
        return out

    @classmethod
    def from_dict(cls, d: dict) -> ThinkingBlock:
        return cls(thinking=d.get("thinking") or "", signature=d.get("signature") or "")


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": "tool_use", "id": self.id, "name": self.name,
                "input": self.input}

    @classmethod
    def from_dict(cls, d: dict) -> ToolUseBlock:
        return cls(id=d.get("id") or "", name=d.get("name") or "",
                   input=d.get("input") or {})


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: Any            # str 或 block 列表（图片等）
    is_error: bool = False

    def to_dict(self) -> dict:
        return {"type": "tool_result", "tool_use_id": self.tool_use_id,
                "content": self.content, "is_error": self.is_error}

    @classmethod
    def from_dict(cls, d: dict) -> ToolResultBlock:
        return cls(tool_use_id=d.get("tool_use_id") or "", content=d.get("content"),
                   is_error=bool(d.get("is_error")))


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


def block_from_dict(d: dict) -> ContentBlock | None:
    t = d.get("type")
    if t == "text":
        return TextBlock.from_dict(d)
    if t == "thinking":
        return ThinkingBlock.from_dict(d)
    if t == "tool_use":
        return ToolUseBlock.from_dict(d)
    if t == "tool_result":
        return ToolResultBlock.from_dict(d)
    return None


@dataclass
class Message:
    role: Role
    content: list[ContentBlock] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"role": self.role,
                "content": [b.to_dict() for b in self.content]}

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        content = [b for b in (block_from_dict(x)
                               for x in d.get("content") or []
                               if isinstance(x, dict)) if b is not None]
        return cls(role=d.get("role") or "user", content=content)

    def text_parts(self) -> str:
        return "\n".join(b.text for b in self.content if isinstance(b, TextBlock))


# ---------------------------------------------------------------- 工具/会话状态
@dataclass
class ToolDef:
    """Provider 层的工具声明（tools 字段）。"""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    timeout_s: int | None = None          # 工具级超时覆盖
    truncation: dict = field(default_factory=dict)   # TruncationPolicy 展示用
    permission: dict = field(default_factory=dict)

    def to_api(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


@dataclass
class Todo:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"               # pending|in_progress|completed
    activeForm: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "subject": self.subject,
                "description": self.description, "status": self.status,
                "activeForm": self.activeForm}


@dataclass
class SessionState:
    """会话内可变状态（kv 元数据；与 transcript 事件对齐重建）。"""
    todos: list[Todo] = field(default_factory=list)
    compact_points: list[str] = field(default_factory=list)   # compact 事件 uuid
    files_touched: dict[str, float] = field(default_factory=dict)   # path → mtime（Edit 守卫）
    background_tasks: dict[str, dict] = field(default_factory=dict)  # id → ProcInfo
    meta: dict = field(default_factory=dict)
