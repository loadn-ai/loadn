"""retry.py 单测：退避序列（封顶）、耗尽抛最后异常、已产出不重试、判定函数。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from hahaness.providers import Chunk
from hahaness.providers.retry import (
    StreamInterrupted,
    is_retriable_exc,
    is_retriable_status,
    retry_call,
)


def make_fn(steps: list) -> object:
    """每次 fn() 调用消费 steps 一项：异常则抛出，chunk 列表则产出。"""
    state = {"i": 0}

    async def fn():
        step = state["i"]
        state["i"] += 1
        if step < len(steps) and not isinstance(steps[step], list):
            raise steps[step]
        items = steps[step] if step < len(steps) \
            else [Chunk(kind="text_delta", text="ok")]
        for chunk in items:
            yield chunk

    return fn


@pytest.fixture()
def sleeps(monkeypatch):
    """记录退避时长并把 asyncio.sleep 变即时（不真等）。"""
    recorded: list[float] = []

    async def fake_sleep(delay):
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


async def test_backoff_then_success(sleeps):
    fn = make_fn([httpx.ConnectError("refused"), httpx.ReadTimeout("t"),
                  [Chunk(kind="text_delta", text="ok")]])
    out = [c async for c in retry_call(fn)]
    assert [(c.kind, c.text) for c in out] == [("text_delta", "ok")]
    assert sleeps == [1.0, 2.0]      # base=1 起步，指数翻倍


async def test_backoff_capped(sleeps):
    steps = [httpx.ReadError(f"e{i}") for i in range(4)] + [[Chunk(kind="stop")]]
    out = [c async for c in retry_call(make_fn(steps), base=30.0, cap=40.0)]
    assert out[-1].kind == "stop"
    assert sleeps == [30.0, 40.0, 40.0, 40.0]   # 封顶 cap


async def test_exhausted_raises_last(sleeps):
    err = httpx.ReadError("boom")

    async def fn():
        raise err
        yield  # noqa: B901  # 使 fn 成为 async generator

    with pytest.raises(httpx.ReadError) as ei:
        async for _ in retry_call(fn, max_tries=3):
            pass
    assert ei.value is err                  # 抛的是最后一个异常本身
    assert sleeps == [1.0, 2.0]             # 3 次尝试之间睡 2 次


async def test_non_retriable_raises_immediately(sleeps):
    async def fn():
        raise ValueError("bad request")
        yield  # noqa: B901

    with pytest.raises(ValueError):
        async for _ in retry_call(fn):
            pass
    assert sleeps == []                     # 4xx 类业务错误不退避


async def test_no_retry_after_chunk_produced(sleeps):
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        yield Chunk(kind="text_delta", text="partial")
        raise httpx.ReadError("cut")

    got: list[Chunk] = []
    with pytest.raises(httpx.ReadError):
        async for chunk in retry_call(fn):
            got.append(chunk)
    assert [c.text for c in got] == ["partial"]
    assert calls["n"] == 1                  # 已产出后重试会造成重复消费
    assert sleeps == []


def test_is_retriable_status():
    assert is_retriable_status(429)
    for code in (500, 502, 503, 504, 529):
        assert is_retriable_status(code)
    for code in (200, 400, 401, 403, 404, 422):
        assert not is_retriable_status(code)


def test_is_retriable_exc():
    req = httpx.Request("POST", "http://x.test/v1/messages")
    assert is_retriable_exc(httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(429, request=req)))
    assert is_retriable_exc(httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(529, request=req)))
    assert not is_retriable_exc(httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(400, request=req)))
    assert is_retriable_exc(httpx.ConnectError("refused"))
    assert is_retriable_exc(httpx.ReadError("reset"))
    assert is_retriable_exc(httpx.ReadTimeout("slow"))
    assert is_retriable_exc(ConnectionError("gone"))
    assert is_retriable_exc(TimeoutError("t"))
    assert not is_retriable_exc(StreamInterrupted("cut"))    # RuntimeError 子类
    assert not is_retriable_exc(ValueError("x"))
