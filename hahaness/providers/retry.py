"""Provider 内部重试薄层：仅覆盖「首 chunk 前」的可重试失败，指数退避。

设计要点：
- 只有尚未对外产出任何 chunk 时才重试——重放已消费的流会导致 chunk 重复，
  中途失败一律上抛（流中断由 provider 抛 StreamInterrupted，loop 层走续传
  路径，不在这里补救）。
- 可重试判定窄口径：HTTP 429/529/5xx（httpx.HTTPStatusError 按状态码）与
  网络传输类异常（httpx.TransportError 含连接拒绝/读失败/各类超时）；4xx
  业务错误不重试，StreamInterrupted（RuntimeError）天然不可重试。
- 退避 base→2base→4base…封顶 cap（默认 1s→60s），共 max_tries 次尝试
  （API_RETRY_MAX=5）；耗尽后原样抛出最后一个异常，由 provider.chat 统一
  转 error chunk（retriable 标记交给 loop 决策）。
- 刻意不做 jitter/熔断/装饰器框架——唯一调用方是 provider.chat，保持薄。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

import httpx

from hahaness.constants import API_BACKOFF_MAX_S, API_BACKOFF_MIN_S, API_RETRY_MAX

if TYPE_CHECKING:
    from hahaness.providers import Chunk

logger = logging.getLogger(__name__)


class StreamInterrupted(RuntimeError):
    """流中断：已产出至少一个内容 chunk 后连接断开/流未正常终结。

    不可重试（重放会重复消费）——loop 捕获后走续传/报错路径。
    定义在 retry.py 供 anthropic/openai_compat 共用，且保证 is_retriable_exc
    无需感知具体 provider 模块（RuntimeError 不在可重试白名单）。
    """


def is_retriable_status(code: int) -> bool:
    """429 与全部 5xx（含 Anthropic 网关 529）可重试，其余不可。"""
    return code == 429 or 500 <= code < 600


def is_retriable_exc(exc: BaseException) -> bool:
    """异常级可重试判定：HTTP 状态类按码，传输/连接/超时类一律可重试。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return is_retriable_status(exc.response.status_code)
    return isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError))


async def retry_call(
    fn: Callable[[], AsyncIterator[Chunk]],
    *,
    max_tries: int = API_RETRY_MAX,
    base: float = API_BACKOFF_MIN_S,
    cap: float = API_BACKOFF_MAX_S,
) -> AsyncIterator[Chunk]:
    """包裹一次 provider 调用：首 chunk 前的可重试失败按指数退避重试。

    fn() 每次调用返回全新的 AsyncIterator（重新建连）；一旦本尝试已对外
    yield 过任何 chunk，后续异常直接上抛（重试会造成重复消费）。
    """
    tries = max(1, int(max_tries))
    delay = float(base)
    for attempt in range(1, tries + 1):
        produced = False
        try:
            async for chunk in fn():
                produced = True
                yield chunk
            return
        except Exception as exc:
            if produced:
                raise
            if not is_retriable_exc(exc) or attempt == tries:
                raise  # 不可重试，或已耗尽——抛最后一个异常
            logger.warning(
                "provider 调用失败（第 %d/%d 次，%.1fs 后重试）：%r",
                attempt, tries, delay, exc,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, float(cap))
