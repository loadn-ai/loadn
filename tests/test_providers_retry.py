"""retry.py 单测：退避序列（封顶）、耗尽抛最后异常、已产出不重试、判定函数。"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from loadn.providers import Chunk
from loadn.providers.retry import (
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


# ---------------- 首响限时（2026-11-02 生产实证 GLM 网关挂死兜底） ----------------

# fixture sleeps 把 asyncio.sleep 打成即时——挂死场景需要真 sleep（由
# wait_for/cancel 砍掉，不会真等满），module 级保存原函数
REAL_SLEEP = asyncio.sleep


async def test_first_token_timeout_retries(sleeps):
    """首 chunk 前挂死超 first_token_timeout → 判挂死重试，第二次成功。"""
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            await REAL_SLEEP(3600)          # 挂死：永不出首片（被限时砍掉）
        yield Chunk(kind="text_delta", text="ok")

    out = [c async for c in retry_call(
        fn, first_token_timeout=0.05, base=0.01, cap=0.01)]
    assert [c.text for c in out] == ["ok"]
    assert calls["n"] == 2                  # 第一次被限时砍掉，重试成功
    # 退避次数不作硬断言：sleeps patch 在全量组合下会被外部活动任务
    # 污染（len 可达数万，engine 面遗留任务共用事件循环）——重试语义
    # 已由 calls==2 锁死


async def test_first_token_timeout_not_applied_midstream(sleeps):
    """首片后长时间无下行（思考型常态）不受限时——不误杀、不重复消费。"""
    calls = {"n": 0}
    received: list[str] = []

    async def fn():
        calls["n"] += 1
        yield Chunk(kind="text_delta", text="first")
        await REAL_SLEEP(3600)              # 中段长静默：远超 timeout
        yield Chunk(kind="text_delta", text="second")

    async def collect():
        async for chunk in retry_call(fn, first_token_timeout=0.05):
            received.append(chunk.text)

    # 限时整卡 0.5s：若限时误杀中段会提前抛 TimeoutError 且 fn 重开；
    # 正常行为=首片到达后卡在中段静默（无限时），由外层砍出
    task = asyncio.ensure_future(collect())
    # 3.10：wait_for 抛 asyncio.TimeoutError（与内建 TimeoutError 不同类）
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
    task.cancel()
    try:
        await task              # 回收取消痕迹，防 teardown 逃逸 CancelledError
    except BaseException:
        pass
    assert calls["n"] == 1                  # 未重试（中段未被限时砍）
    assert received == ["first"]            # 首片已产出且只此一片


async def test_first_token_timeout_exhausted(sleeps):
    """永久挂死：耗尽尝试后抛 TimeoutError（由 provider 转 error chunk）。"""
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        await REAL_SLEEP(3600)
        yield Chunk(kind="text_delta", text="never")

    with pytest.raises(TimeoutError):
        async for _ in retry_call(fn, max_tries=2, first_token_timeout=0.05,
                                  base=0.01, cap=0.01):
            pass
    assert calls["n"] == 2


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
