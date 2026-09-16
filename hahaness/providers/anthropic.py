"""Anthropic Messages API provider（原生协议；Z.AI 等 Anthropic 形态网关兼容）。

设计要点：
- chat() 统一以 stop / error chunk 收尾：HTTP 非 200 与网络异常先经 retry_call
  重试（首 chunk 前），仍失败则转 error chunk（retriable 标记交给 loop 决策）。
  唯一例外是 StreamInterrupted——已产出内容后断流，直接上抛给 loop 走续传。
- SSE 自分帧：不依赖 SSE 库，httpx aiter_bytes 累积缓冲手动按 \\n 切行
  （STREAM_LINE_MAX 兜底防超长行撑爆内存，base64 图片免疫），event/data
  两段式解析，注释行与 ping 忽略。
- 事件 → Chunk 映射对齐 claude CLI stream-json 语义：message_start 发 input
  侧 usage 波，message_stop 发全量 usage + stop_reason；tool 参数以
  input_json_delta 增量透传，由 JsonAccumulator（loop 持有）拼装——provider
  不做参数装配，流式/非流式对 loop 呈现同一消费形态。
- thinking 的 signature_delta 不透传（hahaness 不验证签名），仅 warning 一次。
- 读超时不设（read=None）：判死交给 loop 的 stall 逻辑，provider 层不掺和。
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from hahaness.constants import MODEL_MAX_OUTPUT_TOKENS, STREAM_LINE_MAX
from hahaness.providers import Chunk, api_model_name
from hahaness.providers.retry import (
    StreamInterrupted,
    is_retriable_exc,
    retry_call,
)
from hahaness.types import Message, ToolDef

logger = logging.getLogger(__name__)

_ANTHROPIC_VERSION = "2023-06-01"
_USAGE_KEYS = ("input_tokens", "output_tokens",
               "cache_read_input_tokens", "cache_creation_input_tokens")
_INPUT_USAGE_KEYS = ("input_tokens", "cache_read_input_tokens",
                     "cache_creation_input_tokens")
# SSE error 事件里视为瞬态（可重试）的网关错误类型
_RETRIABLE_ERROR_TYPES = {"rate_limit_error", "overloaded_error", "api_error"}


class JsonAccumulator:
    """input_json_delta 增量拼装器（loop 持有；流式/非流式同一装配路径）。

    feed() 按 tool_use_id 追加片段；finish() 取出并 json.loads——拼接不完整
    或非法 JSON 一律回落 {}（工具参数损坏不炸循环，坏 input 交给模型自救）。
    """

    def __init__(self) -> None:
        self._bufs: dict[str, list[str]] = {}

    def feed(self, tool_use_id: str, partial_json: str) -> None:
        """追加一段参数 JSON 增量。"""
        self._bufs.setdefault(tool_use_id, []).append(partial_json or "")

    def finish(self, tool_use_id: str) -> dict:
        """取出并解析该 tool_use 的完整参数；异常/非 dict 一律 {}。"""
        parts = self._bufs.pop(tool_use_id, None)
        raw = "".join(parts) if parts else ""
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            logger.warning("tool_use %s 参数 JSON 装配失败（%d 字符）→ 回落 {}",
                           tool_use_id, len(raw))
            return {}
        if not isinstance(parsed, dict):
            logger.warning("tool_use %s 参数非 JSON 对象 → 回落 {}", tool_use_id)
            return {}
        return parsed


class AnthropicProvider:
    """POST {base_url}/v1/messages 的流式/非流式 provider。

    cfg 即 provider_config() 返回的结构；测试经 transport=（或
    cfg["extra"]["transport"]）注入 httpx.MockTransport，零 token 全分支。
    """

    def __init__(self, cfg: dict, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        extra = cfg.get("extra") or {}
        self.extra = extra
        self.model = api_model_name(cfg.get("model") or "glm-5.3")
        base = (cfg.get("base_url") or "").rstrip("/")
        if not base:
            raise ValueError("AnthropicProvider: cfg['base_url'] 未配置")
        self._url = f"{base}/v1/messages"

        headers = {"anthropic-version": _ANTHROPIC_VERSION}
        api_key = cfg.get("api_key") or ""
        if api_key:
            # Z.AI 网关两种鉴权头都收，一并带上（与 claude CLI 行为一致）
            headers["x-api-key"] = api_key
            headers["authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            headers=headers,
            transport=transport or extra.get("transport"),
            timeout=httpx.Timeout(connect=15.0, read=None, write=60.0, pool=15.0),
        )

    @property
    def model_name(self) -> str:
        return self.model

    # ------------------------------------------------------------ 对外入口
    async def chat(self, messages: list[Message], tools: list[ToolDef],
                   system: str, *, stream: bool = True) -> AsyncIterator[Chunk]:
        """一轮 assistant 响应的完整 chunk 流（以 stop/error 收尾，或抛流中断）。"""

        async def _once() -> AsyncIterator[Chunk]:
            if stream:
                async for chunk in self._stream_once(messages, tools, system):
                    yield chunk
            else:
                for chunk in await self._nonstream_once(messages, tools, system):
                    yield chunk

        try:
            async for chunk in retry_call(_once):
                yield chunk
        except StreamInterrupted:
            raise
        except Exception as exc:
            yield Chunk(kind="error", error=_exc_text(exc),
                        retriable=is_retriable_exc(exc))

    # ------------------------------------------------------------ 请求体
    def _body(self, messages: list[Message], tools: list[ToolDef],
              system: str, *, stream: bool) -> dict:
        body: dict = {
            "model": self.model,
            "max_tokens": int(self.extra.get("max_tokens", MODEL_MAX_OUTPUT_TOKENS)),
            "messages": [m.to_dict() for m in messages],   # 原生 content blocks 直传
        }
        if stream:
            body["stream"] = True
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [t.to_api() for t in tools]
        return body

    # ------------------------------------------------------------ 流式
    async def _stream_once(self, messages: list[Message], tools: list[ToolDef],
                           system: str) -> AsyncIterator[Chunk]:
        body = self._body(messages, tools, system, stream=True)
        got_content = False
        terminal = False
        usage: dict = {}
        stop_reason = ""
        message_id = ""
        blocks: dict[int, dict] = {}          # index → {type,id,name,named}
        warned_signature = False
        try:
            async with self._client.stream("POST", self._url, json=body) as resp:
                if resp.status_code != 200:
                    raise _status_error(resp, await resp.aread())
                async for event, payload in _iter_sse(resp):
                    if event == "message_start":
                        msg = _loads(payload).get("message") or {}
                        message_id = msg.get("id") or ""
                        u = msg.get("usage") or {}
                        usage.update({k: u[k] for k in _USAGE_KEYS if k in u})
                        yield Chunk(
                            kind="usage",
                            usage={k: usage[k] for k in _INPUT_USAGE_KEYS if k in usage},
                            model=msg.get("model") or "",
                            message_id=message_id,
                        )
                    elif event == "content_block_start":
                        d = _loads(payload)
                        cb = d.get("content_block") or {}
                        blocks[d.get("index", -1)] = {
                            "type": cb.get("type") or "",
                            "id": cb.get("id") or "",
                            "name": cb.get("name") or "",
                            "named": False,
                        }
                    elif event == "content_block_delta":
                        d = _loads(payload)
                        delta = d.get("delta") or {}
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            got_content = True
                            yield Chunk(kind="text_delta", text=delta.get("text") or "")
                        elif dtype == "thinking_delta":
                            got_content = True
                            yield Chunk(kind="thinking_delta",
                                        text=delta.get("thinking") or "")
                        elif dtype == "input_json_delta":
                            got_content = True
                            slot = blocks.get(d.get("index", -1)) or {}
                            first = not slot.get("named")
                            slot["named"] = True
                            yield Chunk(
                                kind="input_json_delta",
                                tool_use_id=slot.get("id") or "",
                                tool_name=(slot.get("name") or "") if first else "",
                                partial_json=delta.get("partial_json") or "",
                            )
                        elif dtype == "signature_delta":
                            # thinking 签名不透传：hahaness 不验证 signature，
                            # 延续性由网关自行处理——仅提示一次
                            if not warned_signature:
                                warned_signature = True
                                logger.warning(
                                    "thinking signature_delta 丢弃（hahaness 不透传 signature）")
                    elif event == "message_delta":
                        d = _loads(payload)
                        stop_reason = (d.get("delta") or {}).get("stop_reason") or stop_reason
                        u = d.get("usage") or {}
                        usage.update({k: u[k] for k in _USAGE_KEYS if k in u})
                    elif event == "message_stop":
                        terminal = True
                        yield Chunk(kind="stop", usage=dict(usage),
                                    stop_reason=stop_reason, message_id=message_id)
                        return
                    elif event == "error":
                        terminal = True
                        err = _loads(payload).get("error") or {}
                        etype = err.get("type") or "error"
                        yield Chunk(
                            kind="error",
                            error=f"{etype}: {err.get('message') or payload[:200]}",
                            retriable=etype in _RETRIABLE_ERROR_TYPES,
                        )
                        return
                    # ping / content_block_stop / 未知事件：忽略
        except httpx.TransportError as exc:
            if got_content:
                raise StreamInterrupted(str(exc)) from exc
            raise
        if not terminal:
            if got_content:
                raise StreamInterrupted("流中断：连接结束但未收到 message_stop")
            raise httpx.ReadError("空流：未收到任何 SSE 事件")

    # ------------------------------------------------------------ 非流式
    async def _nonstream_once(self, messages: list[Message], tools: list[ToolDef],
                              system: str) -> list[Chunk]:
        """单次 JSON 响应 → 一次性发全量 chunks + stop（与流式同形态）。"""
        body = self._body(messages, tools, system, stream=False)
        resp = await self._client.post(self._url, json=body)
        if resp.status_code != 200:
            raise _status_error(resp, resp.content)
        d = resp.json()
        usage = {k: v for k, v in (d.get("usage") or {}).items() if k in _USAGE_KEYS}
        message_id = d.get("id") or ""
        chunks = [Chunk(
            kind="usage",
            usage={k: usage[k] for k in _INPUT_USAGE_KEYS if k in usage},
            model=d.get("model") or "",
            message_id=message_id,
        )]
        for block in d.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                chunks.append(Chunk(kind="text_delta", text=block.get("text") or ""))
            elif btype == "thinking":
                chunks.append(Chunk(kind="thinking_delta",
                                    text=block.get("thinking") or ""))
            elif btype == "tool_use":
                # 完整 input 一次性作为单片 JSON 增量——JsonAccumulator 装配路径不变
                chunks.append(Chunk(
                    kind="input_json_delta",
                    tool_use_id=block.get("id") or "",
                    tool_name=block.get("name") or "",
                    partial_json=json.dumps(block.get("input") or {},
                                            ensure_ascii=False),
                ))
        chunks.append(Chunk(kind="stop", usage=usage,
                            stop_reason=d.get("stop_reason") or "",
                            message_id=message_id))
        return chunks


# ---------------------------------------------------------------- 内部工具
def _exc_text(exc: BaseException) -> str:
    text = str(exc).strip() or repr(exc)
    return f"{type(exc).__name__}: {text}"


def _loads(payload: str) -> dict:
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _status_error(resp: httpx.Response, raw: bytes) -> httpx.HTTPStatusError:
    """非 200 统一转 HTTPStatusError，body 里的 error.message 提出来做错误文本。"""
    text = raw[:500].decode("utf-8", "replace")
    detail = text
    parsed = _loads(text)
    if parsed.get("error"):
        detail = parsed["error"].get("message") or text
    return httpx.HTTPStatusError(
        f"HTTP {resp.status_code}: {detail}",
        request=resp.request, response=resp,
    )


async def _iter_sse(resp: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """aiter_bytes 累积缓冲手动按 \\n 分帧 → (event, data) 序列。

    不用 aiter_lines：其内部行缓冲不受我们控制，超长行（base64 图片）防御
    只能自己做——缓冲无换行且超过 STREAM_LINE_MAX 即判流损坏。
    """
    buffer = b""
    event = ""
    parts: list[str] = []
    async for piece in resp.aiter_bytes():
        buffer += piece
        while True:
            nl = buffer.find(b"\n")
            if nl < 0:
                if len(buffer) > STREAM_LINE_MAX:
                    raise httpx.ReadError(
                        f"SSE 单行超过 STREAM_LINE_MAX={STREAM_LINE_MAX} 字节")
                break
            line, buffer = buffer[:nl], buffer[nl + 1:]
            text = line.decode("utf-8", "replace").rstrip("\r")
            if text.startswith("event:"):
                event = text[len("event:"):].strip()
            elif text.startswith("data:"):
                parts.append(text[len("data:"):].strip())
            elif not text:
                # 空行 = 事件边界
                if parts:
                    yield event, "\n".join(parts)
                event, parts = "", []
            # 注释行（: keep-alive）与其他字段忽略
    if parts:
        yield event, "\n".join(parts)
