"""OpenAICompatProvider 单测：零 token，MockTransport 喂 chat-completions SSE。

覆盖：三向转换往返（含 tool_call id 对齐多轮 + thinking 丢弃）、
reasoning_content→thinking_delta、tool_calls 增量还原、finish 映射、
usage 映射与 include_usage、role=tool / 降级 user、流内 error 帧、非流式。
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from loadn.providers.anthropic import JsonAccumulator
from loadn.providers.openai_compat import OpenAICompatProvider
from loadn.types import Message, TextBlock, ThinkingBlock, ToolDef, ToolResultBlock, ToolUseBlock

SSE_HEADERS = {"content-type": "text/event-stream"}


def frame(obj: dict) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


DONE = b"data: [DONE]\n\n"


def make_provider(handler, extra=None) -> tuple[OpenAICompatProvider, dict]:
    captured: dict = {}

    def wrap(request):
        captured["request"] = request
        captured["body"] = json.loads(request.read())
        return handler(request)

    cfg = {"provider": "openai", "base_url": "http://mock.test/v1",
           "api_key": "sk-oai", "model": "glm-5.3", "extra": extra or {}}
    return OpenAICompatProvider(cfg, transport=httpx.MockTransport(wrap)), captured


async def collect(agen):
    return [c async for c in agen]


def _weather_stream() -> bytes:
    """reasoning + text + 单 tool_call 两片参数 + finish + usage 的完整流。"""
    return b"".join([
        frame({"choices": [{"delta": {"role": "assistant",
                                      "reasoning_content": "思考中"}}]}),
        frame({"choices": [{"delta": {"content": "我来查查"}}]}),
        frame({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather",
                          "arguments": '{"ci'}}]}}]}),
        frame({"choices": [{"delta": {"tool_calls": [
            {"index": 0,
             "function": {"arguments": 'ty": "Paris"}'}}]}}]}),
        frame({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        frame({"choices": [],
               "usage": {"prompt_tokens": 50, "completion_tokens": 7,
                         "prompt_tokens_details": {"cached_tokens": 3}}}),
        DONE,
    ])


async def test_stream_roundtrip_multiturn():
    tools = [ToolDef(name="get_weather", description="查天气",
                     input_schema={"type": "object",
                                   "properties": {"city": {"type": "string"}}})]
    provider, captured = make_provider(
        lambda req: httpx.Response(200, content=_weather_stream(),
                                   headers=SSE_HEADERS))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock(text="巴黎天气如何")])],
        tools, "你是测试助手"))

    # ---- 第一轮请求侧
    body = captured["body"]
    assert body["model"] == "glm-5.3"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert body["messages"][0] == {"role": "system", "content": "你是测试助手"}
    assert body["messages"][1] == {"role": "user", "content": "巴黎天气如何"}
    assert body["tools"] == [{"type": "function", "function": {
        "name": "get_weather", "description": "查天气",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}}}}}]
    assert captured["request"].headers["authorization"] == "Bearer sk-oai"

    # ---- 第一轮响应侧
    assert [c.text for c in chunks if c.kind == "thinking_delta"] == ["思考中"]
    assert [c.text for c in chunks if c.kind == "text_delta"] == ["我来查查"]
    deltas = [c for c in chunks if c.kind == "input_json_delta"]
    assert deltas[0].tool_use_id == "call_1"
    assert deltas[0].tool_name == "get_weather"      # 首片带名
    assert deltas[0].partial_json == '{"ci'
    assert deltas[1].tool_use_id == "call_1" and deltas[1].tool_name == ""
    acc = JsonAccumulator()
    for c in deltas:
        acc.feed(c.tool_use_id, c.partial_json)
    assert acc.finish("call_1") == {"city": "Paris"}
    stop = chunks[-1]
    assert stop.kind == "stop"
    assert stop.stop_reason == "tool_use"            # tool_calls → tool_use
    assert stop.usage == {"input_tokens": 50, "output_tokens": 7,
                          "cache_read_input_tokens": 3}

    # ---- loop 侧装配 assistant/user 消息（模拟 W2 行为）
    assistant = Message(role="assistant", content=[
        ThinkingBlock(thinking="思考中", signature="sig-should-drop"),
        TextBlock(text="我来查查"),
        ToolUseBlock(id="call_1", name="get_weather", input={"city": "Paris"}),
    ])
    user = Message(role="user", content=[
        ToolResultBlock(tool_use_id="call_1", content="sunny"),
    ])

    # ---- 第二轮：tool_call id 对齐回传
    provider2, captured2 = make_provider(
        lambda req: httpx.Response(200, content=b"".join([
            frame({"choices": [{"delta": {"content": "巴黎是晴天"}}]}),
            frame({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            DONE,
        ]), headers=SSE_HEADERS))
    chunks2 = await collect(provider2.chat(
        [Message(role="user", content=[TextBlock(text="巴黎天气如何")]),
         assistant, user], [], "你是测试助手"))

    msgs = captured2["body"]["messages"]
    assert msgs[0] == {"role": "system", "content": "你是测试助手"}
    assert msgs[1] == {"role": "user", "content": "巴黎天气如何"}
    amsg = msgs[2]
    assert amsg["role"] == "assistant"
    assert amsg["content"] == "我来查查"
    assert "thinking" not in json.dumps(amsg)       # 历史 thinking 丢弃
    assert amsg["tool_calls"] == [{"id": "call_1", "type": "function",
                                   "function": {"name": "get_weather",
                                                "arguments": '{"city": "Paris"}'}}]
    assert msgs[3] == {"role": "tool", "tool_call_id": "call_1",
                       "content": "sunny"}           # id 与响应侧对齐
    assert len(msgs) == 4
    stop2 = chunks2[-1]
    assert stop2.stop_reason == "end_turn"          # stop → end_turn


async def test_thinking_history_knob():
    """extra["thinking_history"]：默认历史 thinking 不回传；开启后按
    reasoning_content 回放（截断 16k），纯 thinking 的 assistant 消息不丢。"""
    msgs = [Message(role="user", content=[TextBlock(text="问")]),
            Message(role="assistant", content=[
                ThinkingBlock(thinking="推" * 30000, signature="sig"),
                TextBlock(text="答"),
                ToolUseBlock(id="c1", name="Bash", input={"command": "ls"})]),
            Message(role="user", content=[
                ToolResultBlock(tool_use_id="c1", content="ok")])]

    # 默认关：无 reasoning_content
    prov_off, cap_off = make_provider(
        lambda r: httpx.Response(200, content=DONE, headers=SSE_HEADERS))
    await collect(prov_off.chat(msgs, [], "s"))
    assert all("reasoning_content" not in m for m in cap_off["body"]["messages"])
    assert "thinking" not in json.dumps(cap_off["body"]["messages"])

    # 开启：回放 + 截断 + 结构不变
    prov_on, cap_on = make_provider(
        lambda r: httpx.Response(200, content=DONE, headers=SSE_HEADERS),
        extra={"thinking_history": True})
    await collect(prov_on.chat(msgs, [], "s"))
    amsg = cap_on["body"]["messages"][2]
    assert len(amsg["reasoning_content"]) == 16000
    assert amsg["reasoning_content"].startswith("推")
    assert amsg["content"] == "答"
    assert amsg["tool_calls"][0]["id"] == "c1"

    # 纯 thinking 无文本/工具的 assistant 消息：开启后不丢，关闭时仍丢
    only = [Message(role="assistant", content=[ThinkingBlock(thinking="只想")])]
    prov_on2, cap_on2 = make_provider(
        lambda r: httpx.Response(200, content=DONE, headers=SSE_HEADERS),
        extra={"thinking_history": True})
    await collect(prov_on2.chat(only, [], ""))
    assert cap_on2["body"]["messages"][0].get("reasoning_content") == "只想"
    prov_off2, cap_off2 = make_provider(
        lambda r: httpx.Response(200, content=DONE, headers=SSE_HEADERS))
    await collect(prov_off2.chat(only, [], ""))
    assert not cap_off2["body"]["messages"]


async def test_tool_role_user_fallback():
    provider, captured = make_provider(lambda req: httpx.Response(
        200, content=b"".join([
            frame({"choices": [{"delta": {"content": "ok"}, "finish_reason":
                                 "stop"}]}), DONE]), headers=SSE_HEADERS),
        extra={"tool_role": "user"})
    await collect(provider.chat([
        Message(role="user", content=[TextBlock(text="跑一下")]),
        Message(role="assistant", content=[
            ToolUseBlock(id="call_9", name="Bash", input={"command": "ls"})]),
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="call_9", content="file1\nfile2",
                            is_error=True)]),
    ], [], ""))
    msgs = captured["body"]["messages"]
    assert not any(m["role"] == "tool" for m in msgs)     # 无 role=tool
    umsg = [m for m in msgs if m["role"] == "user"][-1]
    assert "call_9" in umsg["content"]
    assert "[ERROR]" in umsg["content"]
    assert "file1" in umsg["content"]


async def test_missing_usage_warns_and_empty(caplog):
    provider, _ = make_provider(lambda req: httpx.Response(
        200, content=b"".join([
            frame({"choices": [{"delta": {"content": "hi"},
                                "finish_reason": "stop"}]}),
            DONE]), headers=SSE_HEADERS))
    with caplog.at_level(logging.WARNING,
                         logger="loadn.providers.openai_compat"):
        chunks = await collect(provider.chat(
            [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert chunks[-1].kind == "stop"
    assert chunks[-1].usage == {}               # 无 usage → {} 而不是 None
    assert any("include_usage" in r.message for r in caplog.records)  # warning 一次


async def test_inline_error_frame():
    provider, _ = make_provider(lambda req: httpx.Response(
        200, content=b"".join([
            frame({"choices": [{"delta": {"content": "partial"}}]}),
            frame({"error": {"code": 1112, "message": "Insufficient Balance"}}),
        ]), headers=SSE_HEADERS))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert chunks[-1].kind == "error"
    assert "1112" in chunks[-1].error and "Insufficient Balance" in chunks[-1].error
    assert chunks[-1].retriable is False        # 余额/鉴权类不重试


async def test_429_retry_then_success(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return httpx.Response(200, content=b"".join([
            frame({"choices": [{"delta": {"content": "ok"},
                                "finish_reason": "stop"}]}), DONE]),
            headers=SSE_HEADERS)

    provider, _ = make_provider(handler)
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert state["n"] == 2 and sleeps == [1.0]
    assert chunks[-1].kind == "stop"


async def test_nonstream_full_message():
    payload = {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "跑命令",
        "reasoning_content": "想了一下",
        "tool_calls": [{"id": "call_2", "type": "function",
                        "function": {"name": "Bash",
                                     "arguments": '{"command": "ls"}'}}]}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
    provider, captured = make_provider(
        lambda req: httpx.Response(200, json=payload))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], "", stream=False))
    assert "stream" not in captured["body"]
    assert [c.kind for c in chunks] == ["thinking_delta", "text_delta",
                                        "input_json_delta", "stop"]
    assert chunks[0].text == "想了一下"
    assert chunks[2].tool_use_id == "call_2"
    acc = JsonAccumulator()
    acc.feed("call_2", chunks[2].partial_json)
    assert acc.finish("call_2") == {"command": "ls"}
    assert chunks[3].stop_reason == "tool_use"
    assert chunks[3].usage == {"input_tokens": 20, "output_tokens": 5}


async def test_finish_reason_length_maps_to_max_tokens():
    provider, _ = make_provider(lambda req: httpx.Response(
        200, content=b"".join([
            frame({"choices": [{"delta": {"content": "trunc"},
                                "finish_reason": "length"}]}),
            frame({"choices": [],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
            DONE]), headers=SSE_HEADERS))
    chunks = await collect(provider.chat(
        [Message(role="user", content=[TextBlock("hi")])], [], ""))
    assert chunks[-1].stop_reason == "max_tokens"
