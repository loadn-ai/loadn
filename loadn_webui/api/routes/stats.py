"""统计与健康：用量/成本/health。"""
from __future__ import annotations

import json

from fastapi import APIRouter

from ... import db as db_mod
from ...config import PATHS
from ...util import iso

router = APIRouter(prefix="/api")

@router.get("/stats/usage")
def stats_usage(days: int = 7):
    with db_mod.conn() as c:
        daily = db_mod.daily_usage(c, days)
        total = db_mod.usage_totals(c)
        by_profile = {}
        for r in c.execute(
                """SELECT s.profile p, COUNT(*) n, SUM(COALESCE(t.cost_usd,0)) cost
                   FROM turns t JOIN sessions s ON s.id=t.session_id
                   GROUP BY s.profile"""):
            by_profile[r["p"]] = {"turns": r["n"], "cost_usd": round(r["cost"] or 0, 4)}
    return {"daily": daily, "total": total, "by_profile": by_profile}
@router.get("/stats/cost")
def stats_cost(days: int = 14):
    """成本分析页数据：三口径成本（CLI 假价 / z.ai API 真价 / Coding Plan 积分）
    + 按模型 / 每日 / 角色 / Top 会话 / 工具接口调用 聚合。Python 全表聚合
    （千行级 <10ms），不建物化层。"""
    from ...integrations import pricing as pricing_mod

    def _load(text: str | None) -> dict:
        try:
            return json.loads(text) if text else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    tok = dict.fromkeys(("input", "output", "cache_read", "cache_write"), 0)
    cost_cli = 0.0
    turns_n = turns_no_model = 0
    sids: set[str] = set()
    models: dict[str, dict] = {}          # 展示名（原始模型名 or "(未记录)"）→ 聚合
    daily: dict[str, dict] = {}
    profiles: dict[str, dict] = {}
    sess_agg: dict[str, dict] = {}

    def _m_row(name: str, assumed: bool) -> dict:
        return models.setdefault(name, {
            "model": name, "norm": "" if assumed else pricing_mod.normalize_model(name),
            "assumed": assumed, "turns": 0, "input": 0, "output": 0,
            "cache_read": 0, "cache_write": 0, "web_search_requests": 0,
            "cost_api_usd": 0.0, "plan_credits": 0.0})

    with db_mod.conn() as c:
        rows = c.execute(
            """SELECT t.id, t.session_id, t.cost_usd, t.usage_json, t.models_json,
                      t.started_at, s.profile, s.title
               FROM turns t LEFT JOIN sessions s ON s.id=t.session_id""").fetchall()
        for r in rows:
            turns_n += 1
            sids.add(r["session_id"])
            u = _load(r["usage_json"])
            mu = _load(r["models_json"]) if r["models_json"] else None
            turn_cli = r["cost_usd"] or 0
            cost_cli += turn_cli
            turn_api = 0.0
            tu = sum(u.get(k) or 0 for k in db_mod.NUMERIC_USAGE_KEYS)

            # ---- 模型维度（有 models_json 按模型拆；没有则整 turn 归"(未记录)"，
            #      按默认模型价计、assumed 标记）
            if mu:
                for name, m in mu.items():
                    if not isinstance(m, dict):
                        continue
                    it, ot = m.get("inputTokens") or 0, m.get("outputTokens") or 0
                    cr, cw = (m.get("cacheReadInputTokens") or 0,
                              m.get("cacheCreationInputTokens") or 0)
                    row = _m_row(name, assumed=False)
                    row["turns"] += 1
                    row["input"] += it; row["output"] += ot
                    row["cache_read"] += cr; row["cache_write"] += cw
                    row["web_search_requests"] += m.get("webSearchRequests") or 0
                    m_api = pricing_mod.cost_api_usd(
                        name, input_t=it, cache_read_t=cr, cache_write_t=cw, output_t=ot)
                    row["cost_api_usd"] += m_api
                    row["plan_credits"] += pricing_mod.plan_credits(
                        name, input_t=it, cache_t=cr + cw, output_t=ot)
                    tok["input"] += it; tok["output"] += ot
                    tok["cache_read"] += cr; tok["cache_write"] += cw
                    turn_api += m_api
            else:
                turns_no_model += 1
                it, ot = u.get("input_tokens") or 0, u.get("output_tokens") or 0
                cr, cw = (u.get("cache_read_input_tokens") or 0,
                          u.get("cache_creation_input_tokens") or 0)
                row = _m_row("(未记录)", assumed=True)
                row["turns"] += 1
                row["input"] += it; row["output"] += ot
                row["cache_read"] += cr; row["cache_write"] += cw
                m_api = pricing_mod.cost_api_usd(
                    pricing_mod.DEFAULT_MODEL, input_t=it, cache_read_t=cr,
                    cache_write_t=cw, output_t=ot)
                row["cost_api_usd"] += m_api
                row["plan_credits"] += pricing_mod.plan_credits(
                    pricing_mod.DEFAULT_MODEL, input_t=it, cache_t=cr + cw, output_t=ot)
                turn_api += m_api

            # ---- 每日 / 角色 / 会话桶（CLI 假价 + 真实价 + 四类 token 合计）
            day = (r["started_at"] or "")[:10]
            d = daily.setdefault(day, {"turns": 0, "cost_cli_usd": 0.0,
                                       "cost_api_usd": 0.0, "tokens": 0})
            d["turns"] += 1; d["cost_cli_usd"] += turn_cli
            d["cost_api_usd"] += turn_api; d["tokens"] += tu
            p = profiles.setdefault(r["profile"] or "?", {"turns": 0, "cost_cli_usd": 0.0,
                                                          "cost_api_usd": 0.0, "tokens": 0})
            p["turns"] += 1; p["cost_cli_usd"] += turn_cli
            p["cost_api_usd"] += turn_api; p["tokens"] += tu
            sa = sess_agg.setdefault(r["session_id"], {
                "sid": r["session_id"], "title": r["title"] or r["session_id"],
                "profile": r["profile"] or "?", "turns": 0,
                "cost_cli_usd": 0.0, "cost_api_usd": 0.0, "tokens": 0})
            sa["turns"] += 1; sa["cost_cli_usd"] += turn_cli
            sa["cost_api_usd"] += turn_api; sa["tokens"] += tu

        # ---- 工具/接口调用统计（messages.blocks_json 的 [{name,brief,is_error}]）
        tools = [{"name": r["name"], "calls": r["calls"], "errors": r["errors"] or 0}
                 for r in c.execute(
                     """SELECT json_extract(b.value,'$.name') name, COUNT(*) calls,
                               SUM(json_extract(b.value,'$.is_error')) errors
                        FROM messages m, json_each(m.blocks_json) b
                        WHERE m.blocks_json IS NOT NULL
                          AND json_extract(b.value,'$.name') IS NOT NULL
                        GROUP BY name ORDER BY calls DESC LIMIT 30""")]

    def _r2(x: float) -> float:
        return round(x, 4)

    return {
        "totals": {
            "tokens": {**tok, "total_all": sum(tok.values())},
            "cost_cli_usd": round(cost_cli, 2),
            "cost_api_usd": _r2(sum(m["cost_api_usd"] for m in models.values())),
            "plan_credits": round(sum(m["plan_credits"] for m in models.values()), 4),
            "turns": turns_n, "sessions": len(sids),
            "turns_without_model": turns_no_model,
        },
        "by_model": sorted(models.values(), key=lambda m: -m["cost_api_usd"]),
        "daily": [{"day": k, "turns": v["turns"], "tokens": v["tokens"],
                   "cost_cli_usd": _r2(v["cost_cli_usd"]),
                   "cost_api_usd": _r2(v["cost_api_usd"])}
                  for k, v in sorted(daily.items())][-days:],
        "by_profile": [{"profile": k, "turns": v["turns"], "tokens": v["tokens"],
                        "cost_cli_usd": _r2(v["cost_cli_usd"]),
                        "cost_api_usd": _r2(v["cost_api_usd"])}
                       for k, v in sorted(profiles.items(),
                                          key=lambda kv: -kv[1]["cost_api_usd"])],
        "top_sessions": sorted(sess_agg.values(), key=lambda s: -s["cost_api_usd"])[:10],
        "tools": tools,
        "pricing": pricing_mod.pricing_overview(),
    }
@router.get("/health")
def health():
    # R7：release 字段（读 RELEASE.json——让 healthcheck 能验证新版本真在跑）
    release = {}
    try:
        import json as _j

        from ...config import CODE_ROOT as _CR
        release = _j.loads((_CR / "RELEASE.json").read_text())
    except (OSError, ValueError, KeyError):
        pass
    from ... import engines as engines_mod

    engines = {name: spec.health() for name, spec in engines_mod.ENGINES.items()
               if name not in engines_mod.ALIASES}
    disk_free_gb = -1.0
    try:
        import shutil as _sh
        _, _, free = _sh.disk_usage(str(PATHS["root"]))
        disk_free_gb = round(free / 1024 ** 3, 1)
    except OSError:
        pass
    # claude_bin/claude_version 顶层字段保留（前端兼容）；engines 为全引擎状态
    from ...security import sandbox as sandbox_mod
    return {"release": release,"claude_bin": engines.get("claude", {}).get("bin"),
            "claude_version": engines.get("claude", {}).get("version"),
            "engines": engines, "default_engine": engines_mod.default_engine(),
            "sandbox": sandbox_mod.tier_status(),
            "disk_free_gb": disk_free_gb, "time": iso()}
