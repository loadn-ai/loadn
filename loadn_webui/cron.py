"""标准 5 段 cron：解析 + 下次触发计算（纯 stdlib，本地时区）。

scheduled_jobs 的 cron 串是触发的唯一真源；due_at 是算出的下次值缓存
（fire 推进 / 创建 / PATCH 三处都要经 next_run_iso 重算写回）。

语义对齐 Vixie cron 的常用子集：分 时 日 月 周，字段支持 `*` `,` `-` `/`
（含 `a-b/n`、`*/n`）；周 7 归一为 0（周日）。日/周都非 `*` 时取 **OR**
（如「13 号或周五」），单边 `*` 以非 `*` 边为准——标准 cron 语义，最易错点。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# 逐级跳进的搜索上界：4 年覆盖闰年周期（2/29 最多隔 3 年）
_MAX_SCAN_DAYS = 1461

_CN_DOW = "日一二三四五六"      # 索引 = cron dow（0=周日）
_ALL_MIN = frozenset(range(60))
_ALL_HR = frozenset(range(24))
_ALL_DOM = frozenset(range(1, 32))
_ALL_MON = frozenset(range(1, 13))
_ALL_DOW = frozenset(range(7))

# 字段名 →（下界, 上界），顺序即 cron 五段
_FIELDS = (("minute", 0, 59), ("hour", 0, 23),
           ("dom", 1, 31), ("month", 1, 12), ("dow", 0, 7))


@dataclass(frozen=True)
class Cron:
    minute: frozenset[int]
    hour: frozenset[int]
    dom: frozenset[int]          # 1-31
    month: frozenset[int]        # 1-12
    dow: frozenset[int]          # 0-6，0=周日
    expr: str


def parse_cron(expr: str) -> Cron:
    """5 段 cron → Cron；段数/字符/越界 ValueError。"""
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError(f"cron 需 5 段（分 时 日 月 周）: {expr!r}")
    sets: dict[str, frozenset[int]] = {}
    for (name, lo, hi), raw in zip(_FIELDS, parts):
        vals = _parse_field(raw, lo, hi, name)
        if name == "dow" and 7 in vals:
            vals = (vals - {7}) | {0}     # 7 = 周日别名
        sets[name] = frozenset(vals)
    return Cron(minute=sets["minute"], hour=sets["hour"], dom=sets["dom"],
                month=sets["month"], dow=sets["dow"], expr=expr)


def _parse_field(raw: str, lo: int, hi: int, name: str) -> set[int]:
    """单段：`*` `,` `-` `/` 组合；越界/空段/坏字符 ValueError。"""
    out: set[int] = set()
    for piece in raw.split(","):
        if not piece:
            raise ValueError(f"cron {name} 段有空项: {raw!r}")
        step = 1
        body = piece
        if "/" in piece:
            body, _, step_s = piece.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                raise ValueError(f"cron {name} 步长非整数: {piece!r}") from None
            if step < 1:
                raise ValueError(f"cron {name} 步长须 ≥1: {piece!r}")
        if body == "*":
            a, b = lo, hi
        elif "-" in body:
            a_s, _, b_s = body.partition("-")
            try:
                a, b = int(a_s), int(b_s)
            except ValueError:
                raise ValueError(f"cron {name} 区间非整数: {piece!r}") from None
        else:
            try:
                a = b = int(body)
            except ValueError:
                raise ValueError(f"cron {name} 值非整数: {piece!r}") from None
        if a < lo or b > hi or a > b:
            raise ValueError(f"cron {name} 越界（{lo}-{hi}）: {piece!r}")
        out.update(range(a, b + 1, step))
    if not out:
        raise ValueError(f"cron {name} 段为空: {raw!r}")
    return out


def next_run(cron: Cron | str, after: datetime | None = None) -> datetime:
    """严格晚于 after 的下次触发（本地时区 aware，秒/微秒归零）。

    逐级跳进（月→日→当日时分），最坏 4 年内找不到（如 2 月 30 日）抛
    ValueError——不逐分钟暴力。
    """
    c = parse_cron(cron) if isinstance(cron, str) else cron
    tz = datetime.now().astimezone().tzinfo
    cur = (after or datetime.now(tz)).astimezone(tz)
    cand = cur.replace(second=0, microsecond=0) + timedelta(minutes=1)
    dom_any = c.dom == _ALL_DOM
    dow_any = c.dow == _ALL_DOW

    for _ in range(_MAX_SCAN_DAYS + 100):
        if cand.month not in c.month:
            cand = _next_month_first(cand, c.month)
            continue
        if not _day_ok(cand, c, dom_any, dow_any):
            cand = (cand + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        # 日已定：当天找晚于 cand 的首个（时,分）组合；无则次日再判
        for h in sorted(c.hour):
            if h > cand.hour:
                return cand.replace(hour=h, minute=min(c.minute))
            if h == cand.hour:
                later = [m for m in sorted(c.minute) if m >= cand.minute]
                if later:
                    return cand.replace(minute=later[0])
        cand = (cand + timedelta(days=1)).replace(hour=0, minute=0)
    raise ValueError(f"cron {_MAX_SCAN_DAYS} 天内无触发点: {c.expr!r}")


def _next_month_first(cand: datetime, months: frozenset[int]) -> datetime:
    y, m = cand.year, cand.month
    for _ in range(48):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        if m in months:
            return cand.replace(year=y, month=m, day=1, hour=0, minute=0)
    raise ValueError(f"cron 月份集合无未来可达值: {sorted(months)}")


def _day_ok(cand: datetime, c: Cron, dom_any: bool, dow_any: bool) -> bool:
    """cron 日匹配：dom/dow 双 `*` 恒真；单边看非 `*` 边；双限制取 OR。"""
    if dom_any and dow_any:
        return True
    dow_hit = ((cand.weekday() + 1) % 7) in c.dow   # 周一=0 → cron 周一=1
    if dom_any:
        return dow_hit
    if dow_any:
        return cand.day in c.dom
    return cand.day in c.dom or dow_hit


def next_run_iso(cron: Cron | str, after: datetime | None = None) -> str:
    """下次触发的 UTC iso（与 scheduler.parse_when / util.iso 同口径）。"""
    return next_run(cron, after).astimezone(timezone.utc).isoformat(timespec="seconds")


def describe(expr: str) -> str:
    """命中常见模式给中文短语，否则原样返回（前端/CLI 直显）。"""
    try:
        c = parse_cron(expr)
    except ValueError:
        return expr
    if c.month != _ALL_MON or c.dom != _ALL_DOM:
        return expr
    hhmm = f"{min(c.hour):02d}:{min(c.minute):02d}"
    single_time = len(c.hour) == 1 and len(c.minute) == 1
    if c.dow == _ALL_DOW:
        if c.minute == _ALL_MIN and c.hour == _ALL_HR:
            return "每分钟"
        if single_time:
            return f"每天 {hhmm}"
        if c.minute == {0} and _is_step(sorted(c.hour)):
            return f"每 {sorted(c.hour)[1]} 小时"
        if c.hour == _ALL_HR and _is_step(sorted(c.minute)):
            return f"每 {sorted(c.minute)[1]} 分钟"
        return expr
    if single_time:
        if len(c.dow) == 1:
            return f"每周{_CN_DOW[min(c.dow)]} {hhmm}"
        if c.dow == {1, 2, 3, 4, 5}:
            return f"工作日 {hhmm}"
    return expr


def _is_step(vals: list[int]) -> bool:
    """vals 是从 0 起的等差（*/n 展开形）；单元素不算。"""
    return len(vals) > 1 and vals[0] == 0 \
        and all(vals[i + 1] - vals[i] == vals[1] for i in range(len(vals) - 1))


__all__ = ["Cron", "describe", "next_run", "next_run_iso", "parse_cron"]
