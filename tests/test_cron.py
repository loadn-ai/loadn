"""cron 模块：解析合法性 / next 边界（严格大于、月末、闰年、dom-dow OR）/ describe。"""
from __future__ import annotations

from datetime import datetime

import pytest

from loadn_webui.cron import describe, next_run, next_run_iso, parse_cron


def _dt(s: str) -> datetime:
    """'2026-09-18 19:59' 本地时区 aware（与 next_run 内部口径一致）。"""
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.astimezone()
    return d


# ---------------------------------------------------------------- 解析
def test_parse_valid_forms():
    c = parse_cron("*/15 * * * *")
    assert c.minute == frozenset({0, 15, 30, 45})
    c = parse_cron("30 20 * * *")
    assert c.minute == {30} and c.hour == {20}
    c = parse_cron("0 20 * * 1-5")
    assert c.dow == {1, 2, 3, 4, 5}
    c = parse_cron("*/10 8-18 * * *")
    assert c.hour == frozenset(range(8, 19))
    c = parse_cron("5,35 20 13 * 5")
    assert c.minute == {5, 35}
    # dow 7 归一为 0（周日别名）
    assert parse_cron("* * * * 7").dow == {0}


@pytest.mark.parametrize("bad", [
    "* * * *",            # 4 段
    "* * * * * *",        # 6 段
    "60 * * * *",         # 分越界
    "* * 0 * *",          # 日 0
    "* * * 13 *",         # 月越界
    "61 * * * *",
    "",                   # 空
    "a * * * *",          # 非整数
    "*/0 * * * *",        # 步长 0
    "1-60 * * * *",       # 区间越界
])
def test_parse_invalid(bad):
    with pytest.raises(ValueError):
        parse_cron(bad)


# ---------------------------------------------------------------- next 边界
def test_next_daily_strictly_after():
    """从 19:59 → 当日 20:00；从 20:00:00 整点 → 次日 20:00（严格大于）。"""
    assert next_run("30 20 * * *", _dt("2026-09-18 19:59")) == _dt("2026-09-18 20:30")
    n = next_run("30 20 * * *", _dt("2026-09-18 20:30:00"))
    assert n == _dt("2026-09-19 20:30")


def test_next_month_end_jump():
    """31 号从 9 月（30 天）跳到 10 月 31。"""
    n = next_run("0 20 31 * *", _dt("2026-09-18 20:00"))
    assert (n.month, n.day) == (10, 31)


def test_next_leap_year():
    """2/29 跨 2027（平年）找到 2028-02-29。"""
    n = next_run("0 20 29 2 *", _dt("2026-09-18 20:00"))
    assert (n.year, n.month, n.day) == (2028, 2, 29)


def test_dom_dow_or_semantics():
    """`0 20 13 * 5`：13 号或周五（OR）；13 号本身是周五只算一次。"""
    # 2026-09-18 是周五：即使不是 13 号也命中
    n = next_run("0 20 13 * 5", _dt("2026-09-18 08:00"))
    assert n == _dt("2026-09-18 20:00")
    # 下一个 13 号（2026-10-13 周二）也命中
    n = next_run("0 20 13 * 5", _dt("2026-10-10 08:00"))
    assert n == _dt("2026-10-13 20:00")


def test_dow_only():
    """每周一 08:00：2026-09-18 是周五 → 下周一 2026-09-21。"""
    n = next_run("0 8 * * 1", _dt("2026-09-18 12:00"))
    assert n == _dt("2026-09-21 08:00")


def test_unreachable_raises():
    """2 月 30 日不存在 → 4 年上界 ValueError。"""
    with pytest.raises(ValueError):
        next_run("0 0 30 2 *", _dt("2026-09-18 00:00"))


def test_next_run_iso_utc():
    """iso 输出为 UTC（+00:00 后缀）且可反解。"""
    s = next_run_iso("30 20 * * *", _dt("2026-09-18 19:59"))
    assert s.endswith("+00:00")
    back = datetime.fromisoformat(s)
    assert back.astimezone() == _dt("2026-09-18 20:30")


def test_step_within_day():
    """8-18 点每 10 分钟：从 08:05 → 08:10；18 点后 → 次日 08:00。"""
    assert next_run("*/10 8-18 * * *", _dt("2026-09-18 08:05")) == _dt("2026-09-18 08:10")
    assert next_run("*/10 8-18 * * *", _dt("2026-09-18 18:50")) == _dt("2026-09-19 08:00")


# ---------------------------------------------------------------- describe
def test_describe_patterns():
    assert describe("30 20 * * *") == "每天 20:30"
    assert describe("0 20 * * 5") == "每周五 20:00"
    assert describe("0 9 * * 1-5") == "工作日 09:00"
    assert describe("*/15 * * * *") == "每 15 分钟"
    assert describe("0 */6 * * *") == "每 6 小时"
    assert describe("* * * * *") == "每分钟"
    assert describe("5,35 20 13 * 5") == "5,35 20 13 * 5"   # 无预设原样
    assert describe("bad expr") == "bad expr"                # 非法原样
