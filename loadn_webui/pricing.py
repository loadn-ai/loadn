"""定价：z.ai 官方价目 + GLM Coding Plan 积分乘数（2026-09 调研）。

价目来源：docs.z.ai/guides/overview/pricing（API 按量价）、docs.z.ai/devpack/overview
（Coding Plan 积分乘数）。纯函数无 IO；config.yaml 的 pricing 节可整表覆盖。
claude CLI 报的 cost_usd 是对未知模型名按 Opus 价回落的虚高值（$5/$0.5/$25），
仅用于对比展示，不用它计价。
"""
from __future__ import annotations

# API 按量价 $/1M tokens；cache_write 无溢价（官方未列，按 input 价计）
API_PRICING: dict[str, dict[str, float]] = {
    "glm-5.3": {"input": 1.4, "cache_read": 0.26, "output": 4.4},
    "glm-5.3-flash": {"input": 0.15, "cache_read": 0.03, "output": 0.50},
    "glm-5.2": {"input": 1.4, "cache_read": 0.26, "output": 4.4},
}

# GLM Coding Plan 积分/1M tokens（订阅实际计费口径；glm-5.2/5.1 请求自动路由到 5.3）
PLAN_CREDITS: dict[str, dict[str, float]] = {
    "glm-5.3": {"input": 6.9, "cache_input": 1.7, "output": 24},
    "glm-5.3-flash": {"input": 2.3, "cache_input": 0.56, "output": 8},
    "glm-5.2": {"input": 6.9, "cache_input": 1.7, "output": 24},
}

# MCP 工具（Web Search / Web Reader / Zread）每次调用 ×1.2 输出乘数计积分；
# 非高峰（工作日 14:00-18:00 UTC+8 之外）积分 5 折。两者不做历史回算（不可还原），
# 仅在成本页展示层说明。
PLAN_MCP_TOOL_MULTIPLIER = 1.2
PLAN_OFFPEAK_DISCOUNT = 0.5

DEFAULT_MODEL = "glm-5.3"
USD_CNY = 7.1

# claude CLI 对未知模型名的回落价（Opus 4.8）——CLI cost_usd 虚高的原因
CLI_OPUS_FALLBACK = {"input": 5.0, "cache_read": 0.5, "output": 25.0}


def _tables() -> tuple[dict, dict, float]:
    """内置价目 + config 覆盖（api/plan_credits 整表替换，usd_cny>0 才生效）。"""
    from .config import CONFIG  # 延迟 import：config 不依赖本模块，避免环
    pc = getattr(CONFIG, "pricing", None)
    api = getattr(pc, "api", None) or API_PRICING
    plan = getattr(pc, "plan_credits", None) or PLAN_CREDITS
    usd = getattr(pc, "usd_cny", 0) or USD_CNY
    return api, plan, usd


def _fallback_key(table: dict) -> str:
    """兜底键：优先 DEFAULT_MODEL，不在（整表覆盖）则取表首键。"""
    if DEFAULT_MODEL in table:
        return DEFAULT_MODEL
    return next(iter(table), DEFAULT_MODEL)


def normalize_model(name: str) -> str:
    """模型名归一化：小写、去尾部 "[...]" 变体标记（glm-5.3[1m] → glm-5.3），
    精确匹配 → 前缀匹配 → 兜底键。"""
    n = (name or "").strip().lower()
    if "[" in n:
        n = n.split("[", 1)[0].strip()
    api, _, _ = _tables()
    if n in api:
        return n
    for key in api:
        if n.startswith(key):
            return key
    return _fallback_key(api)


def cost_api_usd(model: str, *, input_t: float = 0, cache_read_t: float = 0,
                 cache_write_t: float = 0, output_t: float = 0) -> float:
    """按 z.ai API 按量价计真实成本（USD）。"""
    api, _, _ = _tables()
    p = api.get(normalize_model(model)) or api[_fallback_key(api)]
    m = 1e6
    return ((input_t + cache_write_t) * p["input"]
            + cache_read_t * p["cache_read"]
            + output_t * p["output"]) / m


def plan_credits(model: str, *, input_t: float = 0, cache_t: float = 0,
                 output_t: float = 0) -> float:
    """按 GLM Coding Plan 积分乘数估算消耗（cache_t = read+write 合并；
    不含 MCP 工具增益与非高峰折减）。"""
    _, plan, _ = _tables()
    p = plan.get(normalize_model(model)) or plan.get(DEFAULT_MODEL) or plan[_fallback_key(plan)]
    return (input_t * p["input"] + cache_t * p["cache_input"]
            + output_t * p["output"]) / 1e6


def pricing_overview() -> dict:
    """成本页单价参考（原样返给前端展示）。"""
    api, plan, usd = _tables()
    return {
        "api": api,
        "plan": plan,
        "usd_cny": usd,
        "default_model": DEFAULT_MODEL,
        "cli_fallback": CLI_OPUS_FALLBACK,
        "mcp_tool_multiplier": PLAN_MCP_TOOL_MULTIPLIER,
        "offpeak_discount": PLAN_OFFPEAK_DISCOUNT,
        "note": ("真实成本按 z.ai API 按量价计；CLI 口径是 claude CLI 对未知模型按 "
                 "Opus 价回落的虚高值，仅作对比。积分为标准时段估算：不含 MCP 工具 "
                 "增益（每次调用 ×1.2 输出乘数），也不含非高峰 5 折折减。"),
    }
