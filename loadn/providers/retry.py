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
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any

import httpx

from loadn.constants import (
    API_BACKOFF_MAX_S,
    API_BACKOFF_MIN_S,
    API_RETRY_MAX,
    FIRST_TOKEN_TIMEOUT_S,
)

if TYPE_CHECKING:
    from loadn.providers import Chunk

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


def backoff_delay(attempt: int, *, base: float = API_BACKOFF_MIN_S,
                  cap: float = API_BACKOFF_MAX_S) -> float:
    """第 attempt 次失败后的退避时长（1-indexed；base→2base→…封顶 cap）。

    retry_call 与 loop 层流中断重试共用同一节律。
    """
    return min(float(base) * (2.0 ** max(0, attempt - 1)), float(cap))


# ---------------------------------------------------------------- 错误→恢复动作
# hermes FailoverReason 的窄口径子集：错误文本 → {reason, retry, compress}
# 决策对象（loop 据此走重试/压缩自救路径，而非一刀切报错）。
_OVERFLOW_PATTERNS = (
    "prompt is too long", "prompt_too_long", "context length",
    "context_length_exceeded", "maximum context", "input length",
    "context window", "too many tokens", "input tokens exceed",
    "exceeds the maximum", "请求过长", "上下文长度",
)


def classify_error(text: str) -> dict:
    """错误文本 → 恢复动作查表（大小写不敏感）。

    返回 {"reason": str, "retry": bool, "compress": bool}：
    - context_overflow → compress=True（loop 强制压缩后重发）
    - 429/5xx 文本兜底 → retry=True（chunk.retriable 已覆盖主路径）
    - 其余 unknown → 两 flag 全 False（如实报错）
    """
    t = (text or "").lower()
    if any(p in t for p in _OVERFLOW_PATTERNS):
        return {"reason": "context_overflow", "retry": False, "compress": True}
    if "rate limit" in t or "overloaded" in t or "too many requests" in t:
        return {"reason": "rate_or_server", "retry": True, "compress": False}
    return {"reason": "unknown", "retry": False, "compress": False}


async def _first_or_timeout(anext_coro: Coroutine[Any, Any, Chunk],
                           timeout: float) -> Chunk:
    """取首 chunk，超时抛干净的 TimeoutError。

    不用 asyncio.wait_for：3.10 下超时取消 async generator 的 __anext__
    时，CancelledError（BaseException，非 Exception 子类）会穿透调用方
    的 except Exception（单测实证）。asyncio.wait + 手动清理保证：
    - 超时 → 我们主动 cancel 任务并吞掉其取消痕迹，抛 TimeoutError；
    - 外部取消（用户 stop）→ asyncio.wait 自身被取消，CancelledError
      正常向外传播（不会被误判成超时）。
    """
    task = asyncio.ensure_future(anext_coro)
    done, pending = await asyncio.wait({task}, timeout=timeout)
    if pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # 清理痕迹（吞取消/异常，保主异常语义）
            pass
        # 内建 TimeoutError（OSError 系）而非 asyncio.TimeoutError：3.10 里
        # 两者是不同类，重试白名单 is_retriable_exc 认的是内建——抛错了类
        # 会被判不可重试直接上抛（首测实证：断开重试日志打了却没重试）
        raise TimeoutError(f"首响超时（>{timeout:.0f}s，网关挂死兜底）")
    return task.result()


async def retry_call(
    fn: Callable[[], AsyncIterator[Chunk]],
    *,
    max_tries: int = API_RETRY_MAX,
    base: float = API_BACKOFF_MIN_S,
    cap: float = API_BACKOFF_MAX_S,
    first_token_timeout: float = FIRST_TOKEN_TIMEOUT_S,
) -> AsyncIterator[Chunk]:
    """包裹一次 provider 调用：首 chunk 前的可重试失败按指数退避重试。

    fn() 每次调用返回全新的 AsyncIterator（重新建连）；一旦本尝试已对外
    yield 过任何 chunk，后续异常直接上抛（重试会造成重复消费）。

    首响限时（2026-11-02 生产实证：GLM 网关偶发挂死 500-1230s，httpx
    read=None 无限等）：首个 chunk 前等待超过 first_token_timeout 判挂死，
    抛 TimeoutError（在可重试白名单内）走既有退避重试；首个 chunk 之后
    不再限时——GLM 思考型流中段长时间无下行属正常，误杀会造成重复消费。
    first_token_timeout<=0 关闭该兜底。
    """
    tries = max(1, int(max_tries))
    for attempt in range(1, tries + 1):
        produced = False
        it: AsyncIterator[Chunk] | None = None
        try:
            it = fn().__aiter__()
            while True:
                if not produced and first_token_timeout and first_token_timeout > 0:
                    nxt = await _first_or_timeout(
                        it.__anext__(), first_token_timeout)
                else:
                    nxt = await it.__anext__()
                produced = True
                yield nxt
        except StopAsyncIteration:
            return
        except Exception as exc:
            # 挂死超时/失败尝试：释放底层连接后再走重试判定
            if it is not None:
                aclose = getattr(it, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # 清理失败不掩盖主异常
                        pass
            if produced:
                raise
            if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                logger.warning(
                    "首响超时（>%.0fs，网关挂死兜底，第 %d/%d 次尝试）——断开重试",
                    first_token_timeout, attempt, tries)
            if not is_retriable_exc(exc) or attempt == tries:
                raise  # 不可重试，或已耗尽——抛最后一个异常
            delay = backoff_delay(attempt, base=base, cap=cap)
            logger.warning(
                "provider 调用失败（第 %d/%d 次，%.1fs 后重试）：%r",
                attempt, tries, delay, exc,
            )
            await asyncio.sleep(delay)
