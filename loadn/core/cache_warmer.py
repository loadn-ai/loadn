"""Prompt-cache 保活（P1-6，pi cache-warmer 同构）。

人审间隙不掉 Anthropic 5min TTL 缓存：miss 全价重写 vs 0.1x 读。空闲期
按 90%TTL 用 **max_tokens=1 单 token 重放**刷新断点（无副作用）。

- TTL/价格从 P1-5 目录读（models.json）；价格 null（GLM 订阅制无边际价）
  → 经济学不可算 → **不发**（保温无收益，pi 同款 stop 语义）
- 决策公式（pi evaluate 同构）：期望节省 = 续用概率×miss 代价 − warm
  代价 ≥ $0.05（env LOADN_CACHE_WARM_MIN_SAVINGS 可调）才发；空闲续用
  概率 0.15
- 不可重放判定：thinking budget 派生自 max_tokens（budget 模型缓存键含
  budget）→ 重放键变 → 不保温（pi isReplayable 同构）
- 刷新点：90% TTL 且保底 10s 余量；60min 硬上限
- 取消（epoch bump）：新 turn 开始 / 压缩改写 messages（旧断点已失效）
- 开关：LOADN_CACHE_WARM=0 关（默认开）；**只惠及长命进程**（REPL/
  P2-1 daemon）——webui 每 turn 一进程，turn 结束进程退、保温随之中止
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from loadn.core.models import cache_ttl, lookup
from loadn.util import get_logger

log = get_logger(__name__)

IDLE_CONTINUATION_PROBABILITY = 0.15
MIN_EXPECTED_SAVINGS_USD = float(
    os.environ.get("LOADN_CACHE_WARM_MIN_SAVINGS", "0.05"))
CACHE_READ_MULT = 0.1          # Anthropic 牌价：cache read = 0.1×input
CACHE_WRITE_MULT = 1.25        # cache write = 1.25×input
MAX_WARM_WINDOW_S = 3600       # 单次空闲期保温硬上限（pi 60min）


def warm_enabled() -> bool:
    """总开关（默认开；env LOADN_CACHE_WARM=0 关）。"""
    return os.environ.get("LOADN_CACHE_WARM", "1") != "0"


def warming_delay_s(ttl_s: float) -> float | None:
    """90% TTL 刷新 + ≥10s 余量；TTL≤10s 无保温意义（pi L29-32 同构）。"""
    if ttl_s <= 10:
        return None
    return max(0.001, min(ttl_s * 0.9, ttl_s - 10))


@dataclass
class WarmDecision:
    action: str                    # "warm" | "stop"
    prompt_tokens: int = 0
    warm_cost_usd: float = 0.0
    miss_cost_usd: float = 0.0
    expected_savings_usd: float = 0.0
    reason: str = ""


def evaluate(model: str, prompt_tokens: int, *, phase: str = "idle") -> WarmDecision:
    """经济学判定（pi evaluate 同构）。价格未知 → stop（不臆造）。"""
    spec = lookup(model)
    if spec.input_price_per_m is None or spec.output_price_per_m is None:
        return WarmDecision("stop", prompt_tokens,
                            reason="cache economics unavailable（目录无价格）")
    if prompt_tokens <= 0:
        return WarmDecision("stop", reason="无 prompt 用量记录")
    pin, pout = spec.input_price_per_m, spec.output_price_per_m
    hit = pin * CACHE_READ_MULT * prompt_tokens / 1e6
    miss = pin * CACHE_WRITE_MULT * prompt_tokens / 1e6
    warm = hit + pout * 1 / 1e6                    # 单 token 输出
    miss_delta = max(0.0, miss - hit)
    prob = IDLE_CONTINUATION_PROBABILITY if phase == "idle" else 1.0
    expected = prob * miss_delta - warm
    if expected >= MIN_EXPECTED_SAVINGS_USD:
        return WarmDecision("warm", prompt_tokens, warm, miss_delta, expected)
    return WarmDecision("stop", prompt_tokens, warm, miss_delta, expected,
                        reason=f"期望节省 ${expected:.4f} < "
                                f"${MIN_EXPECTED_SAVINGS_USD:.2f}")


class CacheWarmer:
    """空闲期后台保温任务（AgentCore 生命周期内）。

    messages_fn 取「下一 turn 会发的同一消息序列」——断点必须逐字节一致
    才命中。epoch 由宿主 bump（新 turn / 压缩后），bump 后旧循环自废。
    """

    def __init__(self, *, replay_fn: Callable[[], AsyncIterator],
                 model: str,
                 record_usage: Callable[[dict], None],
                 replayable: bool = True,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.replay_fn = replay_fn            # 宿主捕获上一轮精确请求参数
        self.model = model                    # （messages/tools/system 逐字节
        self.record_usage = record_usage      # 一致才命中断点——loop 侧闭包）
        self.replayable = replayable          # thinking-budget 模型=False
        self._sleep = sleep
        self._clock = clock
        self.epoch = 0

    def bump(self) -> None:
        """失效当前保温循环（新 turn / 压缩）。"""
        self.epoch += 1

    async def _fire_once(self) -> dict | None:
        """单 token 重放（replay_fn 已含 max_output_override=1）；失败 None
        不重试不炸（网关不容忍重放形态 → 放弃保温）。"""
        usage: dict = {}
        try:
            async for chunk in self.replay_fn():
                # 与 ChunkAssembler 同款：usage 与 stop 两类 chunk 都可能带量
                if chunk.kind in ("usage", "stop") and chunk.usage:
                    usage.update(chunk.usage)
        except Exception:                                  # noqa: BLE001
            return None
        return usage or None

    async def run_idle(self, prompt_tokens: int) -> None:
        """空闲保温主循环：判定→delay→重放→记账，直到 epoch 变或超窗。"""
        if not (warm_enabled() and self.replayable):
            return
        d = evaluate(self.model, prompt_tokens)
        if d.action != "warm":
            log.info("cache-warm 不发：%s", d.reason)
            return
        ttl = cache_ttl(self.model)
        delay = warming_delay_s(ttl)
        if delay is None:
            return
        start = self._clock()
        my_epoch = self.epoch
        while True:
            await self._sleep(delay)
            if self.epoch != my_epoch:
                return                      # 新 turn/压缩：旧断点已无意义
            if self._clock() - start > MAX_WARM_WINDOW_S:
                return                      # 60min 硬上限
            usage = await self._fire_once()
            if self.epoch != my_epoch:
                return
            if usage is None:
                return                      # 网关不容忍重放形态：放弃保温
            self.record_usage({"cache_warm": True, **usage})
