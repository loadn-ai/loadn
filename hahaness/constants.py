"""全部数值常量（经验默认值，集中可调）——工程详设 §6 速查表的落位。"""
from __future__ import annotations

# ---------------------------------------------------------------- 工具纪律
BASH_OUTPUT_MAX = 30_000          # Bash 输出超限截中间保首尾（字符）
BASH_TIMEOUT_DEFAULT_S = 120      # Bash 默认超时（工具级可覆盖）
BASH_KILL_GRACE_S = 3.0           # SIGTERM → SIGKILL 宽限
READ_LINES_DEFAULT = 2000         # Read 默认行数
READ_LINE_CHARS_MAX = 2000        # Read 单行超限截断
READ_FILE_MAX_BYTES = 25 * 1024 * 1024   # >25MB 拒读
EDIT_DIFF_MAX_LINES = 500         # 单次 diff 上限
WRITE_MAX_BYTES = 1024 * 1024     # Write 上限 1MB
GREP_MAX_HITS = 200               # 命中上限，超出提示收窄
GLOB_MAX_HITS = 100
TASK_OUTPUT_MAX_CHARS = 30_000    # 子代理 final text 回传上限
WEBFETCH_MAX_CHARS = 50_000       # 正文提取上限
WEBSEARCH_TOP_K = 10

# ---------------------------------------------------------------- 循环/压缩
MAX_TURNS_DEFAULT = 200           # None/0 = 不限
LOOP_REPEAT_LIMIT = 3             # 同指纹调用连续 N 次且结果相同 → 注入打断
COMPACT_THRESHOLD = 0.92          # 用量 ≥92% 窗口触发压缩
COMPACT_KEEP_TURNS = 20           # 压缩保留最近 K 轮
CONTEXT_BUDGET_TOKENS = 12_000    # ContextAssembler 静态注入预算（约）
ENV_TREE_MAX_ENTRIES = 100        # 环境块目录树条目上限
ENV_TREE_DEPTH = 2
SUBAGENT_CONCURRENCY = 4          # Task 工具信号量

# ---------------------------------------------------------------- Provider
API_RETRY_MAX = 5                 # 429/529/5xx 重试次数
API_BACKOFF_MIN_S = 1.0
API_BACKOFF_MAX_S = 60.0
STREAM_LINE_MAX = 64 * 1024 * 1024   # 读缓冲单行上限（base64 图片免疫）

# ---------------------------------------------------------------- 进程/心跳
HEARTBEAT_INTERVAL_S = 30         # 工具长执行时 system/heartbeat 心跳间隔
HOOK_TIMEOUT_S = 10
STALL_TIMEOUT_DEFAULT_S = 1800    # 子代理独立跑时的判死兜底

# ---------------------------------------------------------------- 截断展示
TOOL_RESULT_INLINE_MAX = 2000     # 落 transcript 前的单值内联截断

# ---------------------------------------------------------------- 记账价表
# z.ai API 按量价 $/1M tokens（docs.z.ai 官方价目，2026-09）；仅用于
# result 事件的 total_cost_usd/costUSD 对照值——精确计价留给宿主
PRICE_PER_MTOKEN = {
    "glm-5.3": {"input": 1.4, "cache_read": 0.26, "output": 4.4},
    "glm-5.3-flash": {"input": 0.15, "cache_read": 0.03, "output": 0.50},
    "glm-5.2": {"input": 1.4, "cache_read": 0.26, "output": 4.4},
}
PRICE_FALLBACK_MODEL = "glm-5.3"


def price_key(model: str) -> str:
    """模型名归一化到价表键（glm-5.3[1m] → glm-5.3；未知回落兜底键）。"""
    n = (model or "").strip().lower()
    if "[" in n:
        n = n.split("[", 1)[0].strip()
    if n in PRICE_PER_MTOKEN:
        return n
    for k in PRICE_PER_MTOKEN:
        if n.startswith(k):
            return k
    return PRICE_FALLBACK_MODEL


def cost_usd(model: str, *, input_t: float = 0, cache_read_t: float = 0,
             cache_write_t: float = 0, output_t: float = 0) -> float:
    p = PRICE_PER_MTOKEN[price_key(model)]
    m = 1e6
    return ((input_t + cache_write_t) * p["input"] + cache_read_t * p["cache_read"]
            + output_t * p["output"]) / m
