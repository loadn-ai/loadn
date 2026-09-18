"""StreamEventSynthesizer：Chunk 流 → Anthropic SSE 形事件（stream_json 层消费）。

Claude CLI 在 --verbose stream-json 下逐事件透传原生 SSE（{"type":
"stream_event","event":{...}}），宿主可用其做打字机渲染。hahaness 的
provider 层把各家协议统一成 Chunk，这里再合成回 Anthropic 事件形状——
provider 无关（anthropic/openai_compat/fake 同一路径），字段级对齐原生：

  message_start → content_block_start → content_block_delta* →
  content_block_stop → message_delta → message_stop

块边界推断：Chunk 协议无显式开/关块信号，按身份键迁移推断——
  ("thinking",) / ("text",) / ("tool", tool_use_id)
kind 或 tool_use_id 变化即关旧块开新块；input_json_delta 首片靠
tool_name != "" 识别（三家 provider 均保证首片带名）。message_start 惰性
（首个内容/usage chunk 触发——anthropic 首事件即 message_start，fake 的
text_round 则 text 先于 usage）。error chunk 不合成任何收尾（loop 会转
ProviderError）。流中断重试由调用方 reset() 换新 message id 整轮重发，
消费者以最终 assistant 整块事件为准。
"""
from __future__ import annotations

import uuid

from hahaness.providers import Chunk

# message_start 的 usage 只放 input 侧（openai/fake 在流首拿不到 output）
_INPUT_KEYS = ("input_tokens", "cache_read_input_tokens",
               "cache_creation_input_tokens")


class StreamEventSynthesizer:
    """一次 chat 尝试的 chunk → 事件合成器（重试轮 reset 复用实例）。"""

    def __init__(self, model: str = "") -> None:
        self.default_model = model or "unknown"
        self.reset()

    # ------------------------------------------------------------ 生命周期
    def reset(self) -> None:
        """开新一轮（新 message id、清块状态）。"""
        self._msg_id = ""
        self._model = ""
        self._started = False
        self._finished = False
        self._usage: dict = {}
        self._open: tuple | None = None       # 当前开块 (key, index)
        self._next_index = 0
        self.starts: list[int] = []
        self.stops: list[int] = []

    @property
    def message_id(self) -> str:
        return self._msg_id

    # ------------------------------------------------------------ 喂入
    def feed(self, c: Chunk) -> list[dict]:
        """喂一个 chunk，返回 0..n 个 Anthropic SSE 形事件 dict。"""
        if self._finished or c.kind == "error":
            return []
        if c.message_id:
            self._msg_id = c.message_id
        if c.model:
            self._model = c.model
        if c.kind == "usage":
            if c.usage:
                self._usage.update(c.usage)
            return self._ensure_start()
        if c.kind == "stop":
            if c.usage:
                self._usage.update(c.usage)
            return self._finish(c.stop_reason)
        if c.kind == "text_delta":
            out = [*self._ensure_start(), *self._switch(("text",), c)]
            out.append({"type": "content_block_delta",
                        "index": self._open[1],
                        "delta": {"type": "text_delta", "text": c.text}})
            return out
        if c.kind == "thinking_delta":
            out = [*self._ensure_start(), *self._switch(("thinking",), c)]
            out.append({"type": "content_block_delta",
                        "index": self._open[1],
                        "delta": {"type": "thinking_delta", "thinking": c.text}})
            return out
        if c.kind == "input_json_delta":
            key = ("tool", c.tool_use_id)
            out = [*self._ensure_start(), *self._switch(key, c)]
            out.append({"type": "content_block_delta",
                        "index": self._open[1],
                        "delta": {"type": "input_json_delta",
                                  "partial_json": c.partial_json}})
            return out
        return []

    # ------------------------------------------------------------ 内部
    def _ensure_start(self) -> list[dict]:
        if self._started:
            return []
        self._started = True
        if not self._msg_id:
            self._msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        usage = {k: self._usage[k] for k in _INPUT_KEYS if k in self._usage}
        usage.setdefault("output_tokens", 1)
        return [{"type": "message_start",
                 "message": {"id": self._msg_id, "type": "message",
                             "role": "assistant",
                             "model": self._model or self.default_model,
                             "content": [], "stop_reason": None,
                             "stop_sequence": None, "usage": usage}}]

    def _switch(self, key: tuple, c: Chunk) -> list[dict]:
        """块迁移：关旧开新（同块续流返回空）。"""
        if self._open is not None and self._open[0] == key:
            return []
        out = self._close()
        index = self._next_index
        self._next_index += 1
        if key[0] == "text":
            block = {"type": "text", "text": ""}
        elif key[0] == "thinking":
            block = {"type": "thinking", "thinking": ""}
        else:
            block = {"type": "tool_use", "id": c.tool_use_id,
                     "name": c.tool_name or "", "input": {}}
        self._open = (key, index)
        self.starts.append(index)
        out.append({"type": "content_block_start", "index": index,
                    "content_block": block})
        return out

    def _close(self) -> list[dict]:
        if self._open is None:
            return []
        index = self._open[1]
        self._open = None
        self.stops.append(index)
        return [{"type": "content_block_stop", "index": index}]

    def _finish(self, stop_reason: str) -> list[dict]:
        self._finished = True
        out = self._close()
        usage = dict(self._usage) or {"output_tokens": 0}
        out.append({"type": "message_delta",
                    "delta": {"stop_reason": stop_reason or "end_turn",
                              "stop_sequence": None},
                    "usage": usage})
        out.append({"type": "message_stop"})
        return out
