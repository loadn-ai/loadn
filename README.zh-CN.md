# loadn（老登，读 **load-n**）

简体中文 | **[English](README.md)**

**A self-contained coding-agent engine in pure Python.** One `pip install`, any
Anthropic-form or OpenAI-compatible endpoint, and you get a headless agent with
tools, MCP, subagents, session persistence, and context compaction — speaking
the Claude Code `stream-json` dialect, so existing harnesses can drive it as a
drop-in subprocess engine.

> 命名：loadn 是 loadn-ai 平台的引擎包（org: **loadn-ai**）。吉祥物「老登」，
> 昵称老 bike，仅作文案。平台产品形态：loadn webui / loadn desktop。

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
| `loadn_webui/` | Web 平台：会话管理/调度/成本/分享，内置安全栈（bwrap 沙箱·物理断网·凭证库·审计账本·审批门） | `pip install -e ".[webui]"` → `loadn-web init` → `loadn-web serve` |
| `ui/` | React 前端（管理中心含安全中心/流量/成本面板） | `cd ui && npm run build` |

文档索引：[ARCHITECTURE](docs/ARCHITECTURE.md)（三平面/数据布局）·
[EXTENDING](docs/EXTENDING.md)（定制缝地图：引擎/provider/skill/MCP/hooks）·
[PROTOCOL](docs/PROTOCOL.md)（引擎方言契约）·
[RELEASE](docs/RELEASE.md)（发布/升级/回滚）·
[ATTACK_SURFACE](docs/ATTACK_SURFACE.md)（攻击面+AI-BOM）。

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
- **Zero-token test suite.** 1344 tests drive every loop branch through a
  scripted fake provider — CI needs no API keys. Beyond coverage (83.1%):
  a homegrown mutation runner injected **2589 bugs into 41 core files** with
  a **76% kill rate** — assertions are proven to catch regressions, not just
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
| Skills | `.claude/skills/` / `.agent/skills/` / `$LOADN_HOME/skills` SKILL.md — name+description indexed in the system prompt; body loads on demand via the `Skill` tool |
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
pytest            # 1344 tests, zero API calls
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

See [CHANGELOG.md](CHANGELOG.md). Contributions welcome —
[CONTRIBUTING.md](CONTRIBUTING.md) describes the layout and how to add a tool
or provider in one file.

## License

Apache-2.0 — see [LICENSE](LICENSE).
