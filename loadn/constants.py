"""全部数值常量（经验默认值，集中可调）——工程详设 §6 速查表的落位。"""
from __future__ import annotations

# ---------------------------------------------------------------- 工具纪律
BASH_OUTPUT_MAX = 30_000          # Bash 输出超限截中间保首尾（字符）
BASH_TIMEOUT_DEFAULT_S = 120      # Bash 默认超时（工具级可覆盖）
MCP_CALL_TIMEOUT_S = 30           # MCP 工具调用总超时（慢 server 部署可调）
MCP_HTTP_TIMEOUT_S = 30.0         # streamable-HTTP 客户端超时
WEBSEARCH_TIMEOUT_S = 20.0        # 搜索 API 超时
WEBFETCH_TIMEOUT_S = 30.0         # 网页抓取超时
BASH_AUTO_BG_S = 60               # 前台超此线自动转后台（Terminal-Bench 死法①）
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

# InteractiveShell（pty 会话；Terminal-Bench 死法②——交互轮次爆炸）
SHELL_SESSIONS_MAX = 4            # 并发会话上限（超限要求先 kill）
SHELL_STEPS_MAX = 40              # 单次调用 steps 上限
SHELL_STEP_TIMEOUT_S = 10.0       # 有 expect 的 step 默认超时
SHELL_STEP_WAIT_S = 1.5           # 无 expect 的 step 固定排空等待
SHELL_READ_POLL_S = 0.05          # master fd 轮询周期（取消安全的读法）
SHELL_BUFFER_MAX = 65_536         # 会话滚动 buffer 上限（保尾）
SHELL_STEP_RECV_CLIP = 4000       # 单步 transcript 接收裁剪
SHELL_TOTAL_BUDGET_S = 900.0      # 单次调用累计预算（先于 loop 层上限返回）

# ---------------------------------------------------------------- 循环/压缩
MAX_TURNS_DEFAULT = 200           # None/0 = 不限
LOOP_REPEAT_LIMIT = 3             # 同指纹调用连续 N 次且结果相同 → 硬打断
LOOP_REMIND_AT = 2                # 连续 N 次先轻提醒（dsh repeat-tool-reminder）
GRIND_MAX_NUDGES = 8              # 完工自检关卡最多续战次数（TB 实测 3 次太早放行：coq 15 分钟假交付）
GRIND_MIN_TURNS = 6               # 前 N 轮纯文本直接放行（聊天/简单问答不受关卡影响）
REFLECT_EVERY_TURNS = 25          # 反思检查点：每 N 轮注入进展/死角总结
COMPACT_THRESHOLD = 0.92          # 用量 ≥92% 窗口触发压缩（last-call 口径）
COMPACT_KEEP_TURNS = 20           # 保留轮数上限兜底（防预算内塞几百轮）
COMPACT_KEEP_TOKENS = 20_000      # 保留窗口 token 预算（chars/1.6 粗估）
PRUNE_KEEP_CHARS = 2000           # 摘要渲染时旧 tool_result 骨架化阈值
CONTEXT_BUDGET_TOKENS = 12_000    # ContextAssembler 静态注入预算（约）
ENV_TREE_MAX_ENTRIES = 100        # 环境块目录树条目上限
ENV_TREE_DEPTH = 2
SUBAGENT_CONCURRENCY = 4          # Task 工具信号量
PLAN_MAX_SUBTASKS = 3             # planner 拆分子任务上限（信号量留余量防限流）

# ---------------------------------------------------------------- 宪法/skills/agents
MEMORY_FILE_MAX_CHARS = 60_000    # 单份 CLAUDE.md/AGENT.md clip
MEMORY_TOTAL_MAX_CHARS = 120_000  # 宪法链总量（超则截最远端）
MEMORY_IMPORT_DEPTH = 3           # @import 递归深度上限
MEMORY_IMPORT_MAX_CHARS = 10_000  # 单个 @import 目标 clip
SKILL_BODY_MAX_CHARS = 16_000     # Skill 工具注入正文 clip
AGENT_DEF_MAX_CHARS = 20_000      # 自定义 agent 定义正文 clip

# ---------------------------------------------------------------- Provider
API_RETRY_MAX = 5                 # 429/529/5xx 重试次数
API_BACKOFF_MIN_S = 1.0
API_BACKOFF_MAX_S = 60.0
STREAM_RETRY_MAX = 2              # loop 层流中断/retriable error chunk 重试次数
STREAM_LINE_MAX = 64 * 1024 * 1024   # 读缓冲单行上限（base64 图片免疫）
# 单响应输出上限：16384 会被长 thinking 吃满（Terminal-Bench 实测：竞赛数学题
# 16k 全耗在 thinking、text 零产出空转一轮）——提到 32k + loop 层截断续轮兜底
MODEL_MAX_OUTPUT_TOKENS = 32_768

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
