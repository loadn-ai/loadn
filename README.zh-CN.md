# loadn（老登，读 **load-n**）

简体中文 | **[English](README.md)**

**A self-contained coding-agent engine in pure Python.** One `pip install`, any
Anthropic-form or OpenAI-compatible endpoint, and you get a headless agent with
tools, MCP, subagents, session persistence, and context compaction — speaking
the Claude Code `stream-json` dialect, so existing harnesses can drive it as a
drop-in subprocess engine.

> 🚧 **抢先版（预发布）**——本仓库当前公开的是 `v0.6.16` 抢先版快照。
> **正式版承诺于 2026-10-10 前发布**；在此之前 API、配置键与文档仍可能
> 调整。欢迎提 issue 反馈。

> 命名：loadn 是 loadn-ai 平台的引擎包（org: **loadn-ai**）。吉祥物「老登」，
> 昵称老 bike，仅作文案。平台产品形态：loadn webui / loadn desktop。

## 配套研究（Companion research）

loadn 是一组预印本（2026，即将公开）的**部署态参考系统**，三篇论文的
主张均锚定到本代码库：

- **Defense-in-Depth for Agentic Execution: A Reference Architecture
  Evaluated by Deterministic Replay of an Online Codebase**——以本仓库
  平台的冻结快照为被测对象：框架原生七层执行安全栈 × 预注册 96 场景
  注入套件 × 16 配置消融 × 56 种白盒逃逸变换。loadn 即论文评估的
  部署态架构；快照清单随正式版一并发布。
- **The Standing Layer: Agent = Model + Harness + Standing**——立场
  文章：agent 公式的第三项是外部裁决的 standing（账号/信誉/出网/资金/
  通道/预算）。平台的资源控制台即 standing 层的部署形态，其审计账本
  是论文的实证数据源。
- **Toolbelt Richness: A Concept and Measurement Framework for Agent
  Environments**——定义 toolbelt richness（可内省 API 面+版本匹配文档+
  可跑样例；供给侧质量而非工具数量），并测量三种供给臂（skills+MCP /
  本地 pinned toolbelt / 混合）。loadn 的本地优先工具供给即富供给臂的
  设计哲学。

```
┌────────────────────────────────────────────────────────────┐
│  CLI:  loadn -p / REPL / python -m loadn             │
├────────────────────────────────────────────────────────────┤
│  AgentCore   loop · LoopGuard · Compactor (92% window)     │
│              stream-event synth (--verbose deltas)         │
│              ContextAssembler · PermissionEngine · Hooks   │
│              SubagentManager (Task tool + .claude/agents)  │
├────────────────────────────────────────────────────────────┤
│  Tools: Bash(+cwd/env, 60s auto-bg) Read Write Edit        │
│         MultiEdit NotebookEdit Grep Glob Skill              │
│         InteractiveShell(pty) WebFetch WebSearch TodoWrite  │
│         (+ MCP dynamic)                                     │
├────────────────────────────────────────────────────────────┤
│  Providers: Anthropic-native SSE │ OpenAI-compatible       │
│             retry/backoff │ usage accounting               │
├────────────────────────────────────────────────────────────┤
│  Persistence: transcript JSONL (append-only, resume,       │
│               compact-aware replay) + SQLite index         │
└────────────────────────────────────────────────────────────┘
```

## Monorepo：引擎 + 平台

本仓库不止是引擎——一个可以完整自托管的 **多会话 agent 平台**：

| 组件 | 是什么 | 起步 |
|---|---|---|
| `loadn/` | 引擎（本 README 主体） | `pip install loadn` |
| `loadn_webui/` | Web 平台：会话管理/调度/成本/分享，内置框架原生**七层执行安全栈**（凭证保险库·bash AST 策略·出口白名单代理·bwrap 沙箱·不可逆动作审批门·蜜罐+审计链·供应链信任门） | `pip install -e ".[webui]"` → `loadn-web init` → `loadn-web serve` |
| `ui/` | React 前端（管理中心含安全中心/流量/成本面板） | `cd ui && npm run build` |

文档索引：[ARCHITECTURE](docs/ARCHITECTURE.md)（三平面/数据布局）·
[EXTENDING](docs/EXTENDING.md)（定制缝地图：引擎/provider/skill/MCP/hooks）·
[PROTOCOL](docs/PROTOCOL.md)（引擎方言契约）·
[RELEASE](docs/RELEASE.md)（发布/升级/回滚）·
[ATTACK_SURFACE](docs/ATTACK_SURFACE.md)（攻击面+AI-BOM）。

## 本部署承载的三个概念

**1 · 沿不可逆边界的纵深防御。** 平台内置框架原生的执行安全栈——
七层、**enforcement path 上无 LLM**、默认 fail-closed，按动作可逆性
分层（可逆工作圈进沙箱，不可逆动作停在审批门——承重墙是沙箱）：

| # | 层 | 机制 | 源码 |
|---|---|---|---|
| L1 | 凭证保险库 | AES-GCM；明文永不进 agent 环境 | `loadn_webui/security/vault.py` |
| L2 | bash AST 策略 | L0 红线+令牌覆盖规则，进程外 PreToolUse | `loadn_webui/security/policy.py` |
| L3 | 出口白名单代理 | CONNECT 白名单、DNS 重绑定与私网拒绝 | `loadn_webui/security/egress_proxy.py` |
| L4 | 文件系统沙箱 | bwrap 同路径挂载；网络命名空间内唯一出口=代理 socket | `loadn_webui/security/sandbox.py` |
| L5 | 不可逆动作审批门 | 一次性确认码绑定参数哈希 | `loadn_webui/security/approve.py` |
| L6 | 蜜罐熔断+审计链 | 会话内埋诱饵凭证；哈希链账本+每日锚点 | `loadn_webui/security/canary.py` · `audit.py` |
| L7 | 供应链信任门 | skill sha256 锁+八类静态扫描+未信任降级 | `loadn_webui/security/skill_scan.py` |

**2 · Standing 层：agent = model + harness + standing。** 算力之外，
任务完成度还取决于世界肯放行什么——账号、出网、资金、通道、速率
预算。平台的资源控制台管理的正是这个准入子面：凭证入保险库、
域名级出口策略+会话级临时授权、不可逆动作过审批门、按角色收敛度
预算、自定义服务以 `LOADN_SVC_<NAME>_URL` 端点注入。运营者扮演
resource landlord 角色，每次裁决都落审计账本。

**3 · 本地优先 toolbelt，而非 skill/MCP 重栈。** 引擎的工具就是可
内省的纯 Python（`dir()`/`help()`/直接读源码）；平台通过挂载 pinned
本地运行时与只读数据根（resource bridges）供给能力，而不是把一切
包成提示词包或 RPC 接口。skills 与 MCP 都支持——作为薄包装与动态
扩展，而非主供给通道。

## Why another agent engine

- **Embeddable & hackable.** ~7k lines of typed Python 3.10+, one runtime
  dependency (`httpx`). The core loop (`AgentCore.run_turn`) is a library
  first; the CLI is a thin shell around it.
- **Speaks `stream-json` natively.** The CLI's argv contract and event stream
  are deliberately isomorphic to Claude Code's headless mode
  (`-p --verbose --output-format stream-json`, prompt as `argv[-1]`,
  `--session-id`/`--resume`, `system/assistant/user/result` events with
  `usage` + per-model `modelUsage`, plus per-delta `stream_event` lines under
  `--verbose` for typewriter rendering). Harnesses built for the Claude CLI
  can spawn loadn instead — supervision, accounting, and UI keep working.
- **Provider-agnostic.** Works with Anthropic-form gateways (Z.AI GLM,
  Anthropic proper) and any OpenAI-compatible endpoint (DeepSeek, vLLM,
  LiteLLM, ...), with thinking/reasoning and tool-call translation handled in
  one adapter layer.
- **Discipline built in.** Output truncation budgets, read-before-write file
  guards, a loop-guard that interrupts repeated identical calls, dual-signal
  stall detection for subprocesses, and compaction that keeps tool-call
  pairing intact. Stream interruptions and retriable provider errors are
  retried inside the loop — a turn never ends with a bogus success.
- **Cheap by default.** Three prompt-cache breakpoints (tools tail /
  system / last message) with a no-cache lane for auxiliary calls, a
  handoff-style compactor that prunes before summarizing (with a file
  ledger and UPDATE mode), last-call usage accounting, and context-overflow
  self-rescue via forced compaction.
- **Won't die waiting on a shell.** Foreground commands that exceed 60s are
  automatically adopted into the background task table (the agent keeps
  working and `tail`s the log later); interactive programs (REPLs, terminal
  games, install wizards) get a pty-backed `InteractiveShell` tool that
  scripts multi-round send/expect exchanges in a single call; compiles are
  prompted to run with `-j$(nproc)`.
- **Context engineering.** The system prompt assembles from a CLAUDE.md /
  AGENTS.md ancestor chain (git-root bounded, nearest last, `@import` support),
  a user-level `AGENT.md`, a skills index, and long-term memory — mirroring
  Claude Code's layered memory. Skills load on demand via the `Skill` tool;
  custom subagent types come from `.claude/agents/*.md` frontmatter.
- **Zero-token test suite.** 1373 tests drive every loop branch through a
  scripted fake provider — CI needs no API keys. Beyond coverage (83.1%):
  a homegrown mutation runner injected **4055 bugs into 47 core files** with
  a **78% kill rate** — assertions are proven to catch regressions, not just
  execute paths (tests/TEST-PLAN.md §7).

## Install

```bash
pip install loadn          # 或：pip install git+https://github.com/loadn-ai/loadn
```

No config required — endpoint resolution order:

1. `$LOADN_HOME/config.json` (your overrides)
2. environment: `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `LOADN_MODEL`
   / `LOADN_PROVIDER` (`anthropic` | `openai` | `fake`)
3. `~/.claude/settings.json` env block (if you already run the Claude CLI
   against a gateway, loadn reuses it as-is)

## Quickstart

```bash
# one-shot, human-readable
loadn -p --dangerously-skip-permissions "read ./note.txt and summarize it"

# headless event stream (Claude Code stream-json dialect)
loadn -p --verbose --output-format stream-json \
  --session-id 11111111-2222-3333-4444-555555555555 "fix the failing test"

# continue a session / structured result only / interactive REPL
loadn -p --resume 11111111-2222-3333-4444-555555555555 "continue"
loadn -p --output-format json "list the todos"
loadn                       # REPL: /help /resume /fork /compact /todos
```

Event stream shape (NDJSON on stdout, one JSON object per line):

```json
{"type":"system","subtype":"init","session_id":"…","model":"…","tools":[…]}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me "}},"parent_tool_use_id":null}
{"type":"assistant","message":{"id":"msg_…","role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"Bash","input":{"command":"ls"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":"…","is_error":false}]}}
{"type":"result","subtype":"success","usage":{"input_tokens":…,"output_tokens":…,"cache_read_input_tokens":…,"cache_creation_input_tokens":…},"modelUsage":{…},"num_turns":2,"duration_ms":…}
```

`stream_event` lines (Anthropic SSE-shaped deltas, only with `--verbose`) are
best-effort transport for live rendering — the assembled `assistant` event is
always the source of truth. Interrupted streams are retried in the loop with a
fresh message id (deltas already seen may replay; consumers should reconcile on
the `assistant` block).

Exit codes: `0` success, `1` any `error_*` outcome (error text goes to stderr —
same position the Claude CLI puts it). During long tool runs the CLI emits a
`system/heartbeat` line every 30s so parent-process stall detectors don't
kill healthy work.

## Use as a library

```python
import asyncio
from pathlib import Path
from loadn.core.build import build_agent

async def main():
    bundle = await build_agent(Path.cwd())          # provider/tools/session wired
    summary = await bundle.core.run_turn("find TODOs and write them to notes/")
    print(summary.text, summary.usage, summary.model_usage)

asyncio.run(main())
```

`AgentCore` is also constructible piecewise (custom tool registry, permission
mode, hooks, compactor off, ...) — see `loadn/core/build.py`.

## Embedding into a harness

Because the CLI mirrors Claude Code's headless contract, any supervisor that
spawns `claude -p --output-format stream-json` can spawn `loadn` instead:

- fresh sessions: `--session-id <uuid>`; continuations: `--resume <uuid>`
- a live session lock rejects concurrent use with the same
  `Session ID already in use` wording (stderr, exit 1)
- accounting: the final `result` event carries snake_case `usage` and
  camelCase per-model `modelUsage` for cost dashboards
- per-session transcripts live under `$LOADN_HOME/sessions/<sid>/`
  (append-only JSONL; replay resumes from the last compaction point)

## Configuration & extension points

| Thing | Where |
|---|---|
| Engine knobs (thresholds, budgets, limits) | `loadn/constants.py` |
| Permission rules | `.agent/settings.json` in the project (deny/allow, `Bash:git push*` patterns); modes `default/acceptEdits/plan/bypassPermissions` |
| Hooks (Pre/PostToolUse, Stop, Session*) | `.agent/settings.json` `hooks` — external commands, stdin JSON, exit 2 blocks |
| MCP servers | `.mcp.json` in the project (stdio; tools appear as `mcp__<server>__<tool>`) |
| Skills | `.agents/skills/`（agentskills.io 标准目录，最高优先）/ `.claude/skills/` / `.agent/skills/` / `$LOADN_HOME/skills` SKILL.md — name+description indexed in the system prompt; body loads on demand via the `Skill` tool（第三方安装过供应链锁，装完即 pin；任意 skill 可导出 agentskills.io 兼容 zip） |
| Custom subagents | `.claude/agents/*.md` / `.agent/agents/*.md` / `$LOADN_HOME/agents` — frontmatter `name/description/tools/model`, body becomes the type's system addendum; usable as `Task(subagent_type=…)` |
| Constitution chain | CLAUDE.md/AGENTS.md from the git root (or `$HOME`/cwd boundary) down to cwd, nearest last; `@./file.md` line imports (depth 3); user-level `$LOADN_HOME/AGENT.md` on top |
| Small model for summaries | `small_model` key in `$LOADN_HOME/config.json` (per-call model override for compaction) |
| Web search backend | `LOADN_SEARCH_PROVIDER` (`bocha`\|`zhipu`) + `LOADN_SEARCH_KEY` |
| Model variant suffixes | `glm-5.3[1m]` — `[...]` is treated as a client-side window hint, stripped for API calls |
| Thinking budget | `LOADN_THINKING_BUDGET` env or `extra.thinking_budget` in config.json (Anthropic-form `thinking.budget_tokens`, clamped; off by default) |
| Prompt caching | on by default (3 breakpoints); `extra.disable_prompt_cache` kills it; auxiliary calls (summaries/planner/grace) bypass the cache lane |
| Compaction knobs | `COMPACT_KEEP_TOKENS` (20k keep window), `PRUNE_KEEP_CHARS` (2000 skeletonize threshold), `LOOP_REMIND_AT` (soft-remind tier) in `loadn/constants.py` |

## Development

```bash
pip install -e ".[dev]"
pytest            # 1373 tests, zero API calls
ruff check .
```

The fake provider (`LOADN_PROVIDER=fake`) replays control files from
`$LOADN_FAKE_DIR` (`reply`/`tools`/`todos`/`fail`/`fastfail`/`bigusage`/
`hang`/`giantline`) — the same protocol used by the end-to-end CLI tests.

## Status & roadmap

- [x] v0.1.0 — W1–W5 complete: providers, tools, loop, permissions, hooks,
      compaction, subagents, MCP, persistence, headless CLI, REPL
- [x] v0.2.x — parallel task planner, background command discipline
- [x] v0.3.0 — per-delta `stream_event`, MultiEdit/NotebookEdit/Bash
      cwd+env/Skill tool, custom subagents, CLAUDE.md chain + `@import`,
      small-model summaries, stream-interruption retry
- [x] v0.4.0 — timeout-death hardening: Bash 60s auto-background,
      pty `InteractiveShell`, `-j$(nproc)` discipline, thinking-budget knob
- [x] v0.5.0 — harness-lore integration: prompt-cache breakpoints,
      truncation-with-actions, handoff compactor (prune + ledger + UPDATE),
      grace call, two-tier loop-guard, truncated-toolCall refusal,
      context-overflow self-rescue, normalized-fuzzy Edit
- [ ] Terminal-Bench baseline numbers
- [ ] Ollama provider, DeepSeek native
- [ ] TUI (textual)
- [ ] MCP server mode (loadn as an MCP server)

变更与方向见 [CHANGELOG.md](CHANGELOG.md) 与 [ROADMAP.md](ROADMAP.md)。
欢迎贡献——[CONTRIBUTING.md](CONTRIBUTING.md) 说明仓库结构、一文件加
工具/provider 的路径，以及断言强度纪律（新守卫配否定路径测试；改动
文件跑突变窄集）。

## License

Apache-2.0 — see [LICENSE](LICENSE).
