"""P1-6 Prompt-cache 保活验收（fake provider 计时场景）。

- 刷新点公式：TTL≤10s 无意义；90%TTL 与 TTL−10s 取小
- 经济学：大 prompt+有价格 → warm；小 prompt/价格未知（GLM）→ 不发；
  busy 相位概率=1
- 计时主链：空闲超 90%TTL 触发一次 warm——重放 max_output_override=1
  且 use_cache=True；usage 记账带 cache_warm 分类
- 取消：epoch bump（新 turn/压缩语义）后不再发；60min 硬上限
- 不可重放（thinking budget）→ 不发
"""
from __future__ import annotations

from loadn.core import cache_warmer as cw
from loadn.core.cache_warmer import CacheWarmer, evaluate, warming_delay_s
from loadn.providers.fake import FakeProvider


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeSleep:
    """记录 sleep 并立即推进假钟（不真等）。"""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.slept: list[float] = []

    async def __call__(self, s: float):
        import asyncio as _aio
        self.slept.append(s)
        self.clock.t += s
        await _aio.sleep(0)      # 真让出一次：宿主才有机会在等待期 bump


def _fake_replay_provider():
    """每轮：一个 text chunk + 带 usage 的 stop chunk（重放成功形态）。"""
    from loadn.providers import Chunk
    from loadn.providers.fake import _DEFAULT_USAGE, _fake_model, _stop_chunk
    fp = FakeProvider([[Chunk(kind="text_delta", text="ok"),
                        _stop_chunk(_DEFAULT_USAGE, "end_turn", _fake_model())]])

    def replay_fn():
        return fp.chat([], [], "", max_output_override=1)
    return fp, replay_fn


# ---------------------------------------------------------------- 公式
def test_warming_delay_formula():
    assert warming_delay_s(5) is None            # TTL≤10s：保温无意义
    assert warming_delay_s(10) is None
    assert warming_delay_s(300) == 270           # 90% TTL
    assert warming_delay_s(100) == 90
    assert warming_delay_s(11) == 1              # 保底 10s 余量（11-10）


# ---------------------------------------------------------------- 经济学
def test_evaluate_economics():
    # claude-sonnet-4-6（$3/M）大 prompt：0.15×miss−warm ≥ $0.05 → warm
    d = evaluate("claude-sonnet-4-6", 1_000_000)
    assert d.action == "warm", d
    assert d.expected_savings_usd >= 0.05
    # 小 prompt：期望节省低于阈值 → stop
    d2 = evaluate("claude-sonnet-4-6", 5_000)
    assert d2.action == "stop" and d2.reason
    # 价格未知（GLM 订阅制）→ economics unavailable → 不发
    d3 = evaluate("glm-5.3", 1_000_000)
    assert d3.action == "stop" and "unavailable" in d3.reason
    # busy 相位（概率 1）更容易触发
    d4 = evaluate("claude-opus-4-8", 200_000, phase="busy")
    assert d4.action == "warm"
    assert d4.expected_savings_usd > evaluate(
        "claude-opus-4-8", 200_000).expected_savings_usd


# ---------------------------------------------------------------- 计时主链
async def test_idle_warms_once_with_single_token_replay():
    fp, replay = _fake_replay_provider()
    recorded: list[dict] = []
    clock = FakeClock()
    warmer = CacheWarmer(replay_fn=replay, model="claude-sonnet-4-6",
                         record_usage=recorded.append,
                         sleep=FakeSleep(clock), clock=clock)
    # ttl=300 → delay=270；跑三拍（270s×3 < 3600 窗口）
    task = __import__("asyncio").get_event_loop().create_task(
        warmer.run_idle(1_000_000))
    for _ in range(30):
        await __import__("asyncio").sleep(0)
        if fp.calls >= 3:
            break
    warmer.bump()
    await task
    assert fp.calls == 3                          # 每拍恰好一次单 token 重放
    assert fp.last_max_output_override == 1
    assert fp.last_use_cache is True
    assert len(recorded) == 3
    assert all(u.get("cache_warm") for u in recorded)


async def test_epoch_bump_cancels_pending():
    fp, replay = _fake_replay_provider()
    clock = FakeClock()
    sleeper = FakeSleep(clock)
    warmer = CacheWarmer(replay_fn=replay, model="claude-sonnet-4-6",
                         record_usage=lambda u: None,
                         sleep=sleeper, clock=clock)
    task = __import__("asyncio").create_task(warmer.run_idle(1_000_000))
    await __import__("asyncio").sleep(0)          # 进入首个 sleep
    warmer.bump()                                 # 等待期被新 turn 打断
    await __import__("asyncio").wait_for(task, timeout=1)
    assert fp.calls == 0                          # 未到刷新点：不发


async def test_below_threshold_never_fires():
    fp, replay = _fake_replay_provider()
    clock = FakeClock()
    warmer = CacheWarmer(replay_fn=replay, model="claude-sonnet-4-6",
                         record_usage=lambda u: None,
                         sleep=FakeSleep(clock), clock=clock)
    await warmer.run_idle(1_000)                  # 小 prompt：stop，直接返回
    assert fp.calls == 0


async def test_hard_cap_60min(monkeypatch):
    fp, replay = _fake_replay_provider()
    clock = FakeClock()
    sleeper = FakeSleep(clock)
    monkeypatch.setattr(cw, "MAX_WARM_WINDOW_S", 300)   # 缩短窗口测硬上限
    warmer = CacheWarmer(replay_fn=replay, model="claude-sonnet-4-6",
                         record_usage=lambda u: None,
                         sleep=sleeper, clock=clock)
    # ttl 默认 300 → delay 270；窗口 300 → 第二拍前超窗退出
    task = __import__("asyncio").create_task(warmer.run_idle(1_000_000))
    for _ in range(10):
        await __import__("asyncio").sleep(0)
    await __import__("asyncio").wait_for(task, timeout=1)
    assert fp.calls == 1                          # 仅第一拍，随后超窗终止


async def test_not_replayable_never_fires():
    fp, replay = _fake_replay_provider()
    clock = FakeClock()
    warmer = CacheWarmer(replay_fn=replay, model="claude-sonnet-4-6",
                         record_usage=lambda u: None, replayable=False,
                         sleep=FakeSleep(clock), clock=clock)
    await warmer.run_idle(1_000_000)
    assert fp.calls == 0


async def test_disabled_by_env(monkeypatch):
    fp, replay = _fake_replay_provider()
    monkeypatch.setenv("LOADN_CACHE_WARM", "0")
    clock = FakeClock()
    warmer = CacheWarmer(replay_fn=replay, model="claude-sonnet-4-6",
                         record_usage=lambda u: None,
                         sleep=FakeSleep(clock), clock=clock)
    await warmer.run_idle(1_000_000)
    assert fp.calls == 0
