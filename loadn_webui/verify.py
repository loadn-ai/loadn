"""凭证资产巡检（P2-3）：结构化注册表 + 验证 URL 回访 + 到期预警。

证书项目的教训：credentials.md 是自由文本（已出错），40+ 验证 URL 只登记
从不回访，10 张证书 2027-09 到期零提醒。本模块把凭证收敛为结构化
artifacts/credentials.json，`wd verify` 巡检三件事：验证页可达且含持有人/
证书号、到期窗口预警、缺失字段报告。挂调度器即可周期跑：
  wd schedule add --sid X --every 7d --max-fires 100 --label 到期巡检 \
      --prompt "跑 python3 '$WORKDADDY_CLI' verify --sid X 并汇报结果"

注册表 schema（artifacts/credentials.json，数组）：
  [{"id": 1, "platform": "google", "title": "GA 基础", "cert_id": "G-123",
    "verify_url": "https://...", "issued": "2026-09-13", "expires": "2027-09-13",
    "account": "google", "holder": "Woldy"}]
account 指向保险库条目（vault），到期日空 = 永久有效。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .config import PATHS
from .util import get_logger

log = get_logger(__name__)

REQUIRED_FIELDS = ("id", "platform", "title", "verify_url")


def load_registry(sid: str) -> tuple[list[dict], list[str]]:
    """读 credentials.json → (条目列表, schema 问题列表)。"""
    p = Path(PATHS["workspace"]) / sid / "artifacts" / "credentials.json"
    if not p.exists():
        return [], [f"未找到 {p}（先按 schema 建注册表）"]
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        return [], [f"credentials.json 不是合法 JSON: {e}"]
    if not isinstance(data, list):
        return [], ["credentials.json 须为条目数组"]
    problems = []
    for i, e in enumerate(data):
        missing = [f for f in REQUIRED_FIELDS if not e.get(f)]
        if missing:
            problems.append(f"条目 #{i} 缺字段: {', '.join(missing)}")
    return data, problems


def _parse_day(s: str) -> date | None:
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def check_expiry(entry: dict, today: date, warn_days: int) -> str:
    """返回到期状态行：''=正常；否则警示文本。"""
    exp = entry.get("expires")
    if not exp:
        return ""                       # 永久有效
    d = _parse_day(exp)
    if d is None:
        return f"expires 无法解析: {exp!r}"
    days = (d - today).days
    if days < 0:
        return f"❌ 已过期 {abs(days)} 天（{exp}）"
    if days <= warn_days:
        return f"⚠️ {days} 天后到期（{exp}）"
    return ""


async def verify_entry(entry: dict, *, proxy: bool = False) -> tuple[bool, str]:
    """回访验证 URL：可达 + 页面含持有人名或证书号 → 通过。"""
    from . import resources
    url = str(entry.get("verify_url") or "")
    holder = str(entry.get("holder") or "").strip()
    cert_id = str(entry.get("cert_id") or "").strip()
    try:
        out = await resources.fetch_page(url, proxy=proxy, ocr="off")
    except Exception as e:  # noqa: BLE001
        return False, f"抓取失败: {str(e)[:120]}"
    text = out.get("text") or ""
    if not text:
        return False, "验证页无正文（可能需登录或页面结构变化）"
    marks = [m for m in (holder, cert_id) if m and m.lower() in text.lower()]
    if not marks:
        return False, (f"页面未见 holder={holder!r} / cert_id={cert_id!r}"
                       "（证书可能被吊销或页面改版）")
    return True, f"验证通过（命中: {', '.join(marks[:2])}）"


async def run(sid: str, *, warn_days: int = 30, proxy: bool = False,
              online: bool = True, out_path: str = "") -> dict:
    """巡检主入口。online=False 只做本地检查（到期/schema，不回访）。"""
    entries, problems = load_registry(sid)
    today = date.today()
    rows = []
    for e in entries:
        row = {"id": e.get("id"), "platform": e.get("platform"),
               "title": e.get("title"), "verify_url": e.get("verify_url"),
               "expiry": check_expiry(e, today, warn_days), "online": None}
        if online:
            ok, msg = await verify_entry(e, proxy=proxy)
            row["online"] = {"ok": ok, "msg": msg}
        rows.append(row)
    report = _render(sid, rows, problems, warn_days, online)
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(report, encoding="utf-8")
    return {"sid": sid, "entries": len(entries), "schema_problems": problems,
            "rows": rows, "report": report}


def _render(sid: str, rows: list[dict], problems: list[str],
            warn_days: int, online: bool) -> str:
    lines = [f"# 凭证巡检报告 · {sid}",
             f"- 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
             f"到期预警窗口 {warn_days} 天 · 线上回访: {'开' if online else '关'}",
             ""]
    if problems:
        lines += ["## 注册表问题", ""]
        lines += [f"- {p}" for p in problems]
        lines.append("")
    if not rows:
        lines.append("（注册表为空）")
        return "\n".join(lines)
    lines += ["| # | 平台/证书 | 到期 | 线上验证 |", "|---|---|---|---|"]
    for r in rows:
        exp = r["expiry"] or "正常"
        onl = "-" if r["online"] is None else \
            ("✅ " + r["online"]["msg"] if r["online"]["ok"] else "❌ " + r["online"]["msg"])
        lines.append(f"| {r['id']} | {r['platform']} / {r['title']} | {exp} | {onl} |")
    return "\n".join(lines)
