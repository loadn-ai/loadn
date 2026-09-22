"""AnthropicProvider 单测：零 token，MockTransport 喂录制 SSE 字节流，不联网。

覆盖：happy path（text+thinking+双 tool_use 分片 → JsonAccumulator 还原）、
usage 两波、error 事件、429 重试后成功、非 200 → error chunk、流中断抛
StreamInterrupted、空流可重试、非流式、JsonAccumulator 边界。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from loadn.providers import Chunk
from loadn.providers.anthropic import AnthropicProvider, JsonAccumulator
from loadn.providers.retry import StreamInterrupted
from loadn.types import Message, TextBlock, ToolDef

SSE_HEADERS = {"content-type": "text/event-stream"}


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def make_provider(handler) -> tuple[AnthropicProvider, dict]:
    captured: dict = {}

    def wrap(request):
        captured["request"] = request
        captured["body"] = json.loads(request.read())
        return handler(request)

    cfg = {"provider": "anthropic", "base_url": "http://mock.test",
           "api_key": "sk-test", "model": "glm-5.3", "extra": {}}
    return AnthropicProvider(cfg, transport=httpx.MockTransport(wrap)), captured


async def collect(agen):
    return [c async for c in agen]


# ---------------------------------------------------------------- happy path
async def test_happy_path_full():
    stream = b"".join([
        sse("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "model": "glm-5.3",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 1000,
                      "cache_creation_input_tokens": 5, "output_tokens": 1}}}),
        sse("content_block_start", {"index": 0, "content_block": {"type": "text"}}),
        sse("content_block_delta", {"index": 0, "delta": {"type": "text_delta",
                                                          "text": "Hel"}}),
        sse("content_block_delta", {"index": 0, "delta": {"type": "text_delta",
                                                          "text": "lo"}}),
        sse("content_block_stop", {"index": 0}),
        sse("content_block_start", {"index": 1,
                                    "content_block": {"type": "thinking"}}),
        sse("content_block_delta", {"index": 1, "delta": {"type": "thinking_delta",
                                                          "thinking": "先想想"}}),
        sse("content_block_delta", {"index": 1, "delta": {"type": "signature_delta",
                                                          "signature": "EqQC"}}),
        sse("content_block_stop", {"index": 1}),
        sse("content_block_start", {"index": 2, "content_block": {
            "type": "tool_use", "id": "tu_1", "name": "get_weather", "input": {}}}),
        sse("content_block_delta", {"index": 2, "delta": {
            "type": "input_json_delta", "partial_json": '{"ci'}}),
        sse("content_block_delta", {"index": 2, "delta": {
            "type": "input_json_delta", "partial_json": 'ty": "Paris"}'}}),
        sse("content_block_stop", {"index": 2}),
        sse("content_block_start", {"index": 3, "content_block": {
            "type": "tool_use", "id": "tu_2", "name": "Bash", "input": {}}}),
        sse("content_block_delta", {"index": 3, "delta": {
            "type": "input_json_delta", "partial_json": '{"command": "ls"}'}}),
        sse("content_block_stop", {"index": 3}),
        sse("message_delta", {"delta": {"stop_reason": "tool_use"},
                              "usage": {"output_tokens": 42}}),
        sse("message_stop", {"type": "message_stop"}),
    ])
    tools = [ToolDef(name="get_weather", description="查天气",
                     input_schema={"type": "object"})]
    provider, captured = make_provider(
        lambda req: httpx.Response(200, content=stream, headers=SSE_HEADERS))

    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock(text="巴黎天气")])],
        tools, "你是测试助手"))

    # ---- 请求侧契约
    body = captured["body"]
    assert body["model"] == "glm-5.3"
    assert body["max_tokens"] == 32768
    assert body["stream"] is True
    # system 走缓存块数组形态（三断点之一）
    assert body["system"] == [{"type": "text", "text": "你是测试助手",
                               "cache_control": {"type": "ephemeral"}}]
    # 末消息末块断点（消息侧三断点之二）
    assert body["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "巴黎天气",
         "cache_control": {"type": "ephemeral"}}]}]
    # tools 末项断点（三断点之三；input_schema 未被污染——只加顶层键）
    assert body["tools"] == [{"name": "get_weather", "description": "查天气",
                              "input_schema": {"type": "object"},
                              "cache_control": {"type": "ephemeral"}}]
    headers = captured["request"].headers
    assert headers["x-api-key"] == "sk-test"
    assert headers["authorization"] == "Bearer sk-test"
    assert headers["anthropic-version"] == "2023-06-01"

    # ---- usage 第一波（input 侧）
    first = chunks[0]
    assert first.kind == "usage"
    assert first.usage == {"input_tokens": 10,
                           "cache_read_input_tokens": 1000,
                           "cache_creation_input_tokens": 5}
    assert first.model == "glm-5.3"
    assert first.message_id == "msg_1"

    # ---- text / thinking / signature
    texts = [c.text for c in chunks if c.kind == "text_delta"]
    assert texts == ["Hel", "lo"]
    thinks = [c.text for c in chunks if c.kind == "thinking_delta"]
    assert thinks == ["先想想"]
    assert not any(c.kind == "error" for c in chunks)   # signature_delta 被吞掉

    # ---- 双 tool_use：input_json_delta 分片 + 首片带名
    deltas = [c for c in chunks if c.kind == "input_json_delta"]
    tu1 = [c for c in deltas if c.tool_use_id == "tu_1"]
    tu2 = [c for c in deltas if c.tool_use_id == "tu_2"]
    assert tu1[0].tool_name == "get_weather" and tu1[0].partial_json == '{"ci'
    assert tu1[1].tool_name == "" and tu1[1].partial_json == 'ty": "Paris"}'
    assert tu2[0].tool_name == "Bash" and tu2[0].partial_json == '{"command": "ls"}'

    # ---- JsonAccumulator 还原 input dict
    acc = JsonAccumulator()
    for c in deltas:
        acc.feed(c.tool_use_id, c.partial_json)
    assert acc.finish("tu_1") == {"city": "Paris"}
    assert acc.finish("tu_2") == {"command": "ls"}

    # ---- stop：全量 usage + stop_reason
    stop = chunks[-1]
    assert stop.kind == "stop"
    assert stop.stop_reason == "tool_use"
    assert stop.usage == {"input_tokens": 10, "cache_read_input_tokens": 1000,
                          "cache_creation_input_tokens": 5, "output_tokens": 42}
    assert stop.message_id == "msg_1"


async def test_json_accumulator_edges():
    acc = JsonAccumulator()
    acc.feed("a", "{")
    assert acc.finish("a") == {}            # 非法 JSON → {}
    acc.feed("b", "")
    assert acc.finish("b") == {}            # 空 → {}
    acc.feed("c", "[1, 2]")
    assert acc.finish("c") == {}            # 非 dict → {}
    assert acc.finish("never") == {}        # 未见过的 id → {}
    acc.feed("d", '{"ok": ')
    acc.feed("d", "true}")
    assert acc.finish("d") == {"ok": True}


# ---------------------------------------------------------------- 错误路径
async def test_error_event_retriable_and_not():
    provider, _ = make_provider(lambda req: httpx.Response(200, content=b"".join([
        sse("message_start", {"message": {"id": "m", "usage": {"input_tokens": 3}}}),
        sse("error", {"type": "error", "error": {"type": "overloaded_error",
                                                 "message": "Overloaded"}}),
    ]), headers=SSE_HEADERS))
    chunks = await collect(provider.chat([Message(role="user",
                                                  content=[TextBlock("hi")])], [], ""))
    assert chunks[-1].kind == "error"
    assert "overloaded_error" in chunks[-1].error and "Overloaded" in chunks[-1].error
    assert chunks[-1].retriable is True

    provider2, _ = make_provider(lambda req: httpx.Response(200, content=sse(
        "error", {"error": {"type": "invalid_request_error",
                            "message": "bad schema"}}), headers=SSE_HEADERS))
    chunks2 = await collect(provider2.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert len(chunks2) == 1 and chunks2[0].kind == "error"
    assert chunks2[0].retriable is False


async def test_429_retry_then_success(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    ok_stream = sse("message_start", {"message": {"id": "m", "usage": {
        "input_tokens": 1}}}) + \
        sse("content_block_delta", {"delta": {"type": "text_delta", "text": "ok"}}) + \
        sse("message_delta", {"delta": {"stop_reason": "end_turn"},
                              "usage": {"output_tokens": 2}}) + \
        sse("message_stop", {})
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={"type": "error",
                                             "error": {"message": "quota"}})
        return httpx.Response(200, content=ok_stream, headers=SSE_HEADERS)

    provider, _ = make_provider(handler)
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert state["n"] == 2
    assert sleeps == [1.0]
    assert chunks[-1].kind == "stop"
    assert chunks[-1].usage["output_tokens"] == 2


async def test_http_400_becomes_error_chunk():
    provider, _ = make_provider(lambda req: httpx.Response(
        400, json={"type": "error", "error": {"type": "invalid_request_error",
                                              "message": "max_tokens too large"}}))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert len(chunks) == 1
    assert chunks[0].kind == "error"
    assert "400" in chunks[0].error and "max_tokens too large" in chunks[0].error
    assert chunks[0].retriable is False


async def test_http_500_exhausted_retriable_error_chunk(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    provider, _ = make_provider(
        lambda req: httpx.Response(500, text="boom"))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert len(chunks) == 1 and chunks[0].kind == "error"
    assert chunks[0].retriable is True
    assert sleeps == [1.0, 2.0, 4.0, 8.0]      # API_RETRY_MAX=5 → 4 次退避


async def test_stream_interrupted_raises():
    head = sse("message_start", {"message": {"id": "m", "usage": {"input_tokens": 1}}}) + \
        sse("content_block_delta", {"delta": {"type": "text_delta", "text": "部分"}})

    async def broken():
        yield head
        raise httpx.ReadError("peer closed")

    provider, _ = make_provider(
        lambda req: httpx.Response(200, content=broken(), headers=SSE_HEADERS))
    got: list[Chunk] = []
    with pytest.raises(StreamInterrupted):
        async for chunk in provider.chat(
                [Message(role="user", content=[TextBlock("hi")])], [], ""):
            got.append(chunk)
    assert any(c.kind == "text_delta" and c.text == "部分" for c in got)


async def test_stream_cut_without_message_stop():
    """连接正常结束但没发 message_stop：已产出内容 → 仍算流中断。"""
    head = sse("content_block_delta", {"delta": {"type": "text_delta", "text": "x"}})
    provider, _ = make_provider(
        lambda req: httpx.Response(200, content=head, headers=SSE_HEADERS))
    with pytest.raises(StreamInterrupted):
        async for _ in provider.chat(
                [Message(role="user", content=[TextBlock("hi")])], [], ""):
            pass


async def test_empty_stream_becomes_retriable_error_chunk(monkeypatch):
    async def no_sleep(_delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)   # 不真等退避
    provider, _ = make_provider(
        lambda req: httpx.Response(200, content=b"", headers=SSE_HEADERS))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert len(chunks) == 1 and chunks[0].kind == "error"
    assert chunks[0].retriable is True         # 未产出任何内容 → 可重试


# ---------------------------------------------------------------- 非流式
async def test_nonstream_full_message():
    payload = {
        "id": "msg_9", "model": "glm-5.3", "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "我用工具查"},
            {"type": "tool_use", "id": "tu_9", "name": "Bash",
             "input": {"command": "pwd"}},
        ],
        "usage": {"input_tokens": 7, "output_tokens": 9,
                  "cache_read_input_tokens": 70, "cache_creation_input_tokens": 0},
    }
    provider, captured = make_provider(
        lambda req: httpx.Response(200, json=payload))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], "", stream=False))

    assert "stream" not in captured["body"]     # 非流式不带 stream 字段
    assert [c.kind for c in chunks] == ["usage", "text_delta",
                                        "input_json_delta", "stop"]
    assert chunks[0].usage == {"input_tokens": 7, "cache_read_input_tokens": 70,
                               "cache_creation_input_tokens": 0}
    assert chunks[1].text == "我用工具查"
    assert chunks[2].tool_use_id == "tu_9"
    assert chunks[2].tool_name == "Bash"
    acc = JsonAccumulator()
    acc.feed(chunks[2].tool_use_id, chunks[2].partial_json)
    assert acc.finish("tu_9") == {"command": "pwd"}    # 与流式同一装配路径
    assert chunks[3].stop_reason == "tool_use"
    assert chunks[3].usage["output_tokens"] == 9


# ---------------------------------------------------------------- per-call model
async def test_chat_model_override_strips_variant():
    """per-call model 覆盖：剥 [1m] 后缀（Z.AI 网关只认裸名）。"""
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    stream = sse("message_start", {"type": "message_start", "message": {
        "id": "msg_m", "model": "glm-5.3-flash",
        "usage": {"input_tokens": 3, "output_tokens": 1}}}) + \
        sse("content_block_start", {"index": 0, "content_block": {
            "type": "text", "text": ""}}) + \
        sse("content_block_delta", {"index": 0, "delta": {
            "type": "text_delta", "text": "ok"}}) + \
        sse("message_stop", {"type": "message_stop"})

    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=stream))
    chunks = await collect(prov.chat([_M(role="user", content=[_TB(text="x")])],
                                     [], "s", model="glm-5.3-flash[1m]"))
    assert captured["body"]["model"] == "glm-5.3-flash"
    assert chunks[-1].kind == "stop"


async def test_chat_no_model_uses_default():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=sse("message_stop", {"type": "message_stop"})))
    chunks = await collect(prov.chat([_M(role="user", content=[_TB(text="x")])],
                                     [], "s"))
    assert chunks[-1].kind == "stop"
    assert captured["body"]["model"] == "glm-5.3"


# ---------------------------------------------------------------- thinking 预算
async def test_thinking_budget_set():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=sse("message_stop", {"type": "message_stop"})))
    prov.extra["thinking_budget"] = 4096
    await collect(prov.chat([_M(role="user", content=[_TB(text="x")])], [], "s"))
    assert captured["body"]["thinking"] == {"type": "enabled",
                                            "budget_tokens": 4096}


async def test_thinking_budget_clamped_to_min():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=sse("message_stop", {"type": "message_stop"})))
    prov.extra["thinking_budget"] = 100        # 低于 1024 → 抬到下限
    await collect(prov.chat([_M(role="user", content=[_TB(text="x")])], [], "s"))
    assert captured["body"]["thinking"]["budget_tokens"] == 1024


async def test_thinking_budget_absent_when_default_or_too_big():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=sse("message_stop", {"type": "message_stop"})))
    await collect(prov.chat([_M(role="user", content=[_TB(text="x")])], [], "s"))
    assert "thinking" not in captured["body"]  # 默认关
    prov2, captured2 = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=sse("message_stop", {"type": "message_stop"})))
    prov2.extra["thinking_budget"] = 10 ** 9    # 与 max_tokens 无余量 → 不设
    await collect(prov2.chat([_M(role="user", content=[_TB(text="x")])], [], "s"))
    assert "thinking" not in captured2["body"]


def test_provider_config_env_thinking_budget(monkeypatch):
    from loadn.providers import provider_config
    monkeypatch.setenv("LOADN_THINKING_BUDGET", "2048")
    cfg = provider_config()
    assert cfg["extra"]["thinking_budget"] == 2048
    monkeypatch.setenv("LOADN_THINKING_BUDGET", "not-a-number")
    cfg2 = provider_config()
    assert "thinking_budget" not in cfg2["extra"]


# ---------------------------------------------------------------- 缓存断点
def _ok_stream():
    return sse("message_start", {"message": {"id": "m", "usage": {"input_tokens": 3}}}) + \
        sse("content_block_start", {"index": 0, "content_block": {"type": "text"}}) + \
        sse("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "ok"}}) + \
        sse("message_stop", {"type": "message_stop"})


async def test_cache_off_no_markers():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    chunks = await collect(prov.chat([_M(role="user", content=[_TB(text="x")])],
                                     [], "s", use_cache=False))
    assert chunks[-1].kind == "stop"
    b = captured["body"]
    assert b["system"] == "s"          # 字符串形态，无块数组
    assert "cache_control" not in json.dumps(b)


async def test_cache_kill_switch():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    cfg = {"provider": "anthropic", "base_url": "http://mock.test",
           "api_key": "k", "model": "glm-5.3",
           "extra": {"disable_prompt_cache": True}}
    captured: dict = {}

    def wrap(request):
        captured["body"] = json.loads(request.read())
        return httpx.Response(200, headers=SSE_HEADERS, content=_ok_stream())

    prov = AnthropicProvider(cfg, transport=httpx.MockTransport(wrap))
    await collect(prov.chat([_M(role="user", content=[_TB(text="x")])], [], "s"))
    assert "cache_control" not in json.dumps(captured["body"])


async def test_cache_empty_guards():
    """空 tools / 空 system / 空末消息 content 不炸。"""
    from loadn.types import Message as _M
    prov, captured = make_provider(lambda r: httpx.Response(
        200, headers=SSE_HEADERS, content=_ok_stream()))
    chunks = await collect(prov.chat([_M(role="user", content=[])], [], ""))
    assert chunks[-1].kind == "stop"
    assert "system" not in captured["body"] and "tools" not in captured["body"]


async def test_cache_downgrade_on_400_named_system():
    """网关 400 点名 system → 降级字符串 system 重发一次成功。"""
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    bodies: list[dict] = []
    state = {"n": 0}

    def wrap(request):
        bodies.append(json.loads(request.read()))
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(400, json={"error": {
                "message": "invalid system format"}})
        return httpx.Response(200, headers=SSE_HEADERS, content=_ok_stream())

    prov = AnthropicProvider({"provider": "anthropic", "base_url": "http://m",
                              "api_key": "k", "model": "glm-5.3", "extra": {}},
                             transport=httpx.MockTransport(wrap))
    chunks = await collect(prov.chat([_M(role="user", content=[_TB(text="x")])],
                                     [], "s"))
    assert chunks[-1].kind == "stop"           # 降级重发成功
    assert isinstance(bodies[0]["system"], list)
    assert bodies[1]["system"] == "s"          # 第二次退回字符串
    assert prov._system_blocks_ok is False


async def test_cache_downgrade_on_400_named_cache_control():
    from loadn.types import Message as _M
    from loadn.types import TextBlock as _TB
    state = {"n": 0}

    def wrap(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(400, json={"error": {
                "message": "cache_control not supported"}})
        return httpx.Response(200, headers=SSE_HEADERS, content=_ok_stream())

    prov = AnthropicProvider({"provider": "anthropic", "base_url": "http://m",
                              "api_key": "k", "model": "glm-5.3", "extra": {}},
                             transport=httpx.MockTransport(wrap))
    chunks = await collect(prov.chat([_M(role="user", content=[_TB(text="x")])],
                                     [], "s"))
    assert chunks[-1].kind == "stop"
    assert prov._cache_enabled is False        # 永久关闭
