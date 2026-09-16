"""OpenAI chat-completions 兼容 provider（DeepSeek/GLM 等端点的三向转换）。

设计要点：
- 三向转换：hahaness Message（Anthropic content blocks）↔ OpenAI messages。
  assistant 的 tool_use → tool_calls（arguments 为 JSON 字符串）；user 的
  tool_result → role=tool 消息带 tool_call_id（端点不认 role=tool 时经
  cfg["extra"]["tool_role"]="user" 降级为 user 文本 + 附加说明）。
- 历史 thinking 块请求侧直接丢弃、只透传 text：chat-completions 的
  reasoning_content 没有 signature 概念，思维链无法验证续传，回传只会
  浪费 token 甚至触发端点报错。
- 响应侧 delta.reasoning_content → thinking_delta；tool_calls[i].function
  .arguments 增量 → input_json_delta（id/name 取该 index 首片）；finish_reason
  映射到 Anthropic 语义（tool_calls→tool_use、stop→end_turn、length→
  max_tokens），保证 loop 层 provider 无关。
- usage 靠 stream_options.include_usage 在最后一帧带回；端点不支持时
  stop.usage 置 {} 并 warning 一次（不中断流）。usage 映射：prompt_tokens→
  input_tokens、completion_tokens→output_tokens、prompt_tokens_details.
  cached_tokens→cache_read_input_tokens（存在时）。
- 与 anthropic.py 同构：SSE 自分帧（手动按 \\n 切行 + STREAM_LINE_MAX 兜底）、
  retry_call 包裹、已产出内容后断流抛 StreamInterrupted、chat() 以 stop/
  error 收尾。openai 侧无 message_start 可用，usage 只随 stop 一波。
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
from hahaness.types import Message, TextBlock, ToolDef, ToolResultBlock, ToolUseBlock

logger = logging.getLogger(__name__)

# finish_reason → Anthropic stop_reason（未知值原样透传）
_FINISH_MAP = {"stop": "end_turn", "tool_calls": "tool_use",
               "length": "max_tokens", "content_filter": "refusal"}
_MISSING_USAGE_WARN = (
    "chat-completions 端点未回 usage（不支持 stream_options.include_usage？），"
    "stop.usage 置空 {}")


class OpenAICompatProvider:
    """POST {base_url}/chat/completions 的流式/非流式 provider。

    cfg 即 provider_config() 返回的结构（base_url 直接拼 /chat/completions，
    鉴权仅 Bearer）；测试经 transport= 注入 httpx.MockTransport。
    """

    def __init__(self, cfg: dict, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        extra = cfg.get("extra") or {}
        self.extra = extra
        self.model = api_model_name(cfg.get("model") or "glm-5.3")
        self.tool_role = str(extra.get("tool_role", "tool") or "tool")
        base = (cfg.get("base_url") or "").rstrip("/")
        if not base:
            raise ValueError("OpenAICompatProvider: cfg['base_url'] 未配置")
        self._url = f"{base}/chat/completions"

        headers = {}
        api_key = cfg.get("api_key") or ""
        if api_key:
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

    # ------------------------------------------------------------ 请求构造
    def _body(self, messages: list[Message], tools: list[ToolDef],
              system: str, *, stream: bool) -> dict:
        body: dict = {
            "model": self.model,
            "max_tokens": int(self.extra.get("max_tokens", MODEL_MAX_OUTPUT_TOKENS)),
            **({"temperature": float(self.extra["temperature"])}
              if self.extra.get("temperature") is not None else {}),
            "messages": self._to_openai_messages(messages, system),
        }
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = [{
                "type": "function",
                "function": {"name": t.name, "description": t.description,
                             "parameters": t.input_schema},
            } for t in tools]
        return body

    def _to_openai_messages(self, messages: list[Message], system: str) -> list[dict]:
        """hahaness Message → OpenAI messages（system 单独参数置顶）。"""
        out: list[dict] = []
        if system:
            out.append({"role": "system", "content": system})
        for msg in messages:
            if msg.role == "assistant":
                text = "\n".join(b.text for b in msg.content
                                 if isinstance(b, TextBlock))
                # thinking 块丢弃：reasoning_content 无 signature，历史思维
                # 无法验证续传，回传既费 token 也可能被端点拒绝——只透传 text
                calls = [{
                    "id": b.id,
                    "type": "function",
                    "function": {"name": b.name,
                                 "arguments": json.dumps(b.input,
                                                         ensure_ascii=False)},
                } for b in msg.content if isinstance(b, ToolUseBlock)]
                if text or calls:
                    item: dict = {"role": "assistant"}
                    if text:
                        item["content"] = text
                    if calls:
                        item["tool_calls"] = calls
                    out.append(item)
            else:
                # user：tool_result 逐个转 role=tool（或降级 user），文本并入 user
                tool_items: list[dict] = []
                texts: list[str] = []
                for b in msg.content:
                    if isinstance(b, ToolResultBlock):
                        rtext = _result_text(b.content)
                        if b.is_error:
                            rtext = f"[ERROR] {rtext}"
                        if self.tool_role == "tool":
                            tool_items.append({"role": "tool",
                                               "tool_call_id": b.tool_use_id,
                                               "content": rtext})
                        else:
                            tool_items.append({
                                "role": "user",
                                "content": f"[tool_result {b.tool_use_id}]\n{rtext}",
                            })
                    elif isinstance(b, TextBlock):
                        texts.append(b.text)
                out.extend(tool_items)   # tool 消息紧跟上一条 assistant.tool_calls
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        return out

    # ------------------------------------------------------------ 流式
    async def _stream_once(self, messages: list[Message], tools: list[ToolDef],
                           system: str) -> AsyncIterator[Chunk]:
        body = self._body(messages, tools, system, stream=True)
        got_content = False
        done = False
        finish = ""
        usage: dict = {}
        slots: dict[int, dict] = {}        # tool_calls index → {id,name,named}
        try:
            async with self._client.stream("POST", self._url, json=body) as resp:
                if resp.status_code != 200:
                    raise _status_error(resp, await resp.aread())
                async for _event, payload in _iter_sse(resp):
                    if payload.strip() == "[DONE]":
                        done = True
                        break
                    d = _loads(payload)
                    if isinstance(d.get("error"), dict):
                        # 部分网关在 200 流里内嵌 error 帧（鉴权/配额类，不可重试）
                        err = d["error"]
                        yield Chunk(
                            kind="error",
                            error=f"{err.get('code') or 'error'}: "
                                  f"{err.get('message') or payload[:200]}",
                            retriable=False,
                        )
                        return
                    u = d.get("usage")
                    if isinstance(u, dict):
                        usage = _map_usage(u)
                    for choice in d.get("choices") or []:
                        finish = choice.get("finish_reason") or finish
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            got_content = True
                            yield Chunk(kind="thinking_delta", text=reasoning)
                        content = delta.get("content")
                        if content:
                            got_content = True
                            yield Chunk(kind="text_delta", text=content)
                        for tc in delta.get("tool_calls") or []:
                            got_content = True
                            idx = tc.get("index") or 0
                            slot = slots.setdefault(idx, {"id": "", "name": "",
                                                          "named": False})
                            fn = tc.get("function") or {}
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            frag = fn.get("arguments") or ""
                            if frag or not slot["named"]:
                                # 首片带 name（空参数也要发，loop 才知道有工具调用）
                                yield Chunk(
                                    kind="input_json_delta",
                                    tool_use_id=slot["id"],
                                    tool_name=slot["name"] if not slot["named"] else "",
                                    partial_json=frag,
                                )
                                slot["named"] = True
        except httpx.TransportError as exc:
            if got_content:
                raise StreamInterrupted(str(exc)) from exc
            raise
        if not done:
            # 与 anthropic 的 message_stop 同等严格要求：流必须以 [DONE] 终结。
            # 未产出内容时断流可安全重试（消费方零接收），只有已发内容才算中断。
            if got_content:
                raise StreamInterrupted("流中断：连接结束但未收到 [DONE]")
            raise httpx.ReadError("空流/断流：未收到内容且无 [DONE]")
        if not usage:
            logger.warning(_MISSING_USAGE_WARN)
        yield Chunk(kind="stop", usage=usage or {},
                    stop_reason=_FINISH_MAP.get(finish, finish))

    # ------------------------------------------------------------ 非流式
    async def _nonstream_once(self, messages: list[Message], tools: list[ToolDef],
                              system: str) -> list[Chunk]:
        """单次 JSON 响应 → 一次性发全量 chunks + stop（与流式同形态）。"""
        body = self._body(messages, tools, system, stream=False)
        resp = await self._client.post(self._url, json=body)
        if resp.status_code != 200:
            raise _status_error(resp, resp.content)
        d = resp.json()
        usage = _map_usage(d.get("usage") or {})
        chunks: list[Chunk] = []
        choice = (d.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        if message.get("reasoning_content"):
            chunks.append(Chunk(kind="thinking_delta",
                                text=message["reasoning_content"]))
        if message.get("content"):
            got = message["content"]
            chunks.append(Chunk(kind="text_delta", text=got if isinstance(got, str)
                                else json.dumps(got, ensure_ascii=False)))
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            chunks.append(Chunk(
                kind="input_json_delta",
                tool_use_id=tc.get("id") or "",
                tool_name=fn.get("name") or "",
                partial_json=fn.get("arguments") or "{}",
            ))
        finish = choice.get("finish_reason") or ""
        chunks.append(Chunk(kind="stop", usage=usage,
                            stop_reason=_FINISH_MAP.get(finish, finish)))
        if not usage:
            logger.warning(_MISSING_USAGE_WARN)
        return chunks


# ---------------------------------------------------------------- 转换工具
def _result_text(content: object) -> str:
    """tool_result.content（str 或 block 列表）→ 纯文本（图片占位）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text") or "")
                else:
                    parts.append(f"[{item.get('type') or 'block'}]")
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _map_usage(u: dict) -> dict:
    """OpenAI usage → Anthropic 形态（cached_tokens 存在时才带 cache 侧）。"""
    out: dict = {}
    if "prompt_tokens" in u:
        out["input_tokens"] = u["prompt_tokens"]
    if "completion_tokens" in u:
        out["output_tokens"] = u["completion_tokens"]
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is not None:
        out["cache_read_input_tokens"] = cached
    return out


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
    text = raw[:500].decode("utf-8", "replace")
    detail = text
    parsed = _loads(text)
    if parsed.get("error"):
        err = parsed["error"]
        detail = err.get("message") if isinstance(err, dict) else str(err)
    return httpx.HTTPStatusError(
        f"HTTP {resp.status_code}: {detail or text}",
        request=resp.request, response=resp,
    )


async def _iter_sse(resp: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """aiter_bytes 累积缓冲手动按 \\n 分帧 → (event, data)；与 anthropic.py 同构。

    chat-completions 的帧无 event 行（仅 data），event 恒为 ""；data: [DONE]
    哨兵由调用方判定。
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
                if parts:
                    yield event, "\n".join(parts)
                event, parts = "", []
    if parts:
        yield event, "\n".join(parts)
