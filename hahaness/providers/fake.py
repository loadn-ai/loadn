"""FakeProvider——零 token 测试驱动（HAHANESS_PROVIDER=fake 即启用）。

两种模式：
- script 模式（单测）：FakeProvider(script=[[Chunk, ...], ...])，每次 chat
  弹出一个轮次的 chunk 列表；script 弹空后回落单 text chunk "(fake done)"。
  轮次未以 stop/error 收尾时自动补一个固定 usage 的 stop（chunk 流契约要求
  终结，loop 才能正常落账收轮）。
- 控制文件模式（e2e/CLI）：无 script 时读 $HAHANESS_FAKE_DIR（默认 cwd/.fake）
  下的控制文件，文件名与语义对齐 宿主平台 fake_claude.py（同名同语义，测试
  基建可平移）：reply / tools / todos / fail / fastfail / bigusage /
  giantline / hang。每轮 usage 固定 input=1000/output=200/cache_read=10000
  （bigusage 覆写为 600000/2000/500000——大上下文轮换测试用），model 取 env
  HAHANESS_FAKE_MODEL（默认 "fake"）。

多轮语义：tools/todos 场景首轮发 tool_use chunk（stop_reason=tool_use），
等 loop 回填 tool_result（即最后一条 user 消息含 tool_result 块）后下一轮
才回文本——节拍：工具调用→结果→文本（与真实引擎一致）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from hahaness.providers import Chunk
from hahaness.types import Message, TextBlock, ToolDef, ToolResultBlock

logger = logging.getLogger(__name__)

_DEFAULT_USAGE = {"input_tokens": 1000, "output_tokens": 200,
                  "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 0}
_BIG_USAGE = {"input_tokens": 600000, "output_tokens": 2000,
              "cache_read_input_tokens": 500000, "cache_creation_input_tokens": 0}
_DEFAULT_TOOL_CALLS = [{"name": "Bash", "input": {"command": "echo hello-fake"}}]
_DEFAULT_TODOS = [{"content": "检索来源", "status": "pending", "activeForm": "检索来源中"}]


class FakeProvider:
    """零 token provider：script 回放 / .fake 控制文件驱动，二选一。"""

    def __init__(self, script: list[list[Chunk]] | None = None):
        self._script: list[list[Chunk]] | None = \
            [list(round_) for round_ in script] if script is not None else None
        self.last_model: str | None = None   # per-call 覆盖的记录（测试断言用）

    @property
    def model_name(self) -> str:
        return "fake"

    async def chat(self, messages: list[Message], tools: list[ToolDef],
                   system: str, *, stream: bool = True,
                   model: str | None = None,
                   use_cache: bool = True) -> AsyncIterator[Chunk]:
        """一轮 = 一段完整 chunk 流（text/tool_use/error 或 hang 后收尾）。"""
        self.last_model = model
        self.last_use_cache = use_cache
        if self._script is not None:
            round_chunks = self._script.pop(0) if self._script \
                else [Chunk(kind="text_delta", text="(fake done)")]
            for chunk in round_chunks:
                yield chunk
            if not round_chunks or round_chunks[-1].kind not in ("stop", "error"):
                yield _stop_chunk(_DEFAULT_USAGE, "end_turn", _fake_model())
            return
        async for chunk in self._control_round(messages):
            yield chunk

    # ------------------------------------------------------------ 控制文件
    async def _control_round(self, messages: list[Message]) -> AsyncIterator[Chunk]:
        ctrl_dir = Path(os.environ.get("HAHANESS_FAKE_DIR")
                        or Path.cwd() / ".fake")
        model = _fake_model()

        def has(name: str) -> bool:
            return (ctrl_dir / name).exists()

        usage = _BIG_USAGE if has("bigusage") else _DEFAULT_USAGE

        if has("fastfail"):
            # resume 秒拒文案与 claude CLI 一致（Session not found）
            # 驱动有界的 resume×2→轮换→fresh 秒拒 终止链。"already in use" 的
            # fresh 翻转分支由真实 CLI 锁路径覆盖（transcript 存在即拒），不走本旋钮
            yield Chunk(kind="error", error="Session not found", retriable=False)
            return
        if has("fail"):
            yield Chunk(kind="error", error="fake internal failure",
                        retriable=False)
            return
        if has("hang"):
            await asyncio.sleep(300)   # 上层 stall/stop 逻辑的靶子
            yield Chunk(kind="text_delta", text="(fake done)")
            yield _stop_chunk(usage, "end_turn", model)
            return

        pending = not _last_user_tool_results(messages)
        if has("todos") and pending:
            yield _tool_use_chunk(0, "TodoWrite", {"todos": _read_todos(ctrl_dir)})
            yield _stop_chunk(usage, "tool_use", model)
            return
        if has("tools") and pending:
            calls = _read_tool_calls(ctrl_dir / "tools")
            for i, call in enumerate(calls):
                yield _tool_use_chunk(i, call.get("name") or "Bash",
                                      call.get("input") or {})
            yield _stop_chunk(usage, "tool_use", model)
            return

        # 文本轮次（reply / giantline / 默认回声）
        yield Chunk(
            kind="usage",
            usage={k: usage[k] for k in
                   ("input_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens")},
            model=model,
        )
        if has("giantline"):
            yield Chunk(kind="text_delta", text="x" * 200_000)
        else:
            reply_file = ctrl_dir / "reply"
            text = reply_file.read_text() if reply_file.exists() \
                else f"收到：{_last_user_text(messages)[:60]}（fake）"
            yield Chunk(kind="text_delta", text=text)
        yield _stop_chunk(usage, "end_turn", model)


# ---------------------------------------------------------------- 内部工具
def _fake_model() -> str:
    return os.environ.get("HAHANESS_FAKE_MODEL") or "fake"


def _stop_chunk(usage: dict, stop_reason: str, model: str) -> Chunk:
    return Chunk(kind="stop", usage=dict(usage), stop_reason=stop_reason,
                 model=model)


def _tool_use_chunk(i: int, name: str, tool_input: dict) -> Chunk:
    """单 chunk 完整 tool_use（参数整片 JSON——JsonAccumulator 装配路径不变）。"""
    return Chunk(kind="input_json_delta", tool_use_id=f"fake_tool_{i + 1}",
                 tool_name=name,
                 partial_json=json.dumps(tool_input or {}, ensure_ascii=False))


def _last_user_tool_results(messages: list[Message]) -> list[ToolResultBlock]:
    """最后一条 user 消息里的 tool_result 块（tools/todos 的换轮判据）。"""
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if last_user is None:
        return []
    return [b for b in last_user.content if isinstance(b, ToolResultBlock)]


def _last_user_text(messages: list[Message]) -> str:
    last_user = next((m for m in reversed(messages) if m.role == "user"), None)
    if last_user is None:
        return ""
    return "\n".join(b.text for b in last_user.content
                     if isinstance(b, TextBlock))


def _read_tool_calls(path: Path) -> list[dict]:
    """tools 控制文件：[{"name","input"}, ...]（单对象也收）；损坏回落默认。"""
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return [dict(c) for c in _DEFAULT_TOOL_CALLS]
    if isinstance(parsed, dict):
        parsed = [parsed]
    if isinstance(parsed, list) and all(isinstance(x, dict) for x in parsed):
        return parsed or [dict(c) for c in _DEFAULT_TOOL_CALLS]
    return [dict(c) for c in _DEFAULT_TOOL_CALLS]


def _read_todos(ctrl_dir: Path) -> list[dict]:
    """todos 控制文件：[{content,status,activeForm}, ...]；损坏回落默认。"""
    try:
        parsed = json.loads((ctrl_dir / "todos").read_text())
        if isinstance(parsed, dict) and isinstance(parsed.get("todos"), list):
            parsed = parsed["todos"]
    except (OSError, json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, list) and parsed:
        return parsed
    return [dict(t) for t in _DEFAULT_TODOS]
