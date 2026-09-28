# loadn

**[简体中文](README.zh-CN.md)** | English

**A self-contained coding-agent engine in pure Python.** One `pip install`, any
Anthropic-form or OpenAI-compatible endpoint, and you get a headless agent with
tools, MCP, subagents, session persistence, and context compaction — speaking
the Claude Code `stream-json` dialect, so existing harnesses can drive it as a
drop-in subprocess engine.

> 🚧 **Early access (preview)** — this repository publishes `v0.6.16` as an
> early-access snapshot. The official release is scheduled **before
> 2026-10-10**; until then APIs, config keys, and docs may still change.
> Bug reports and issue feedback are welcome.

> Naming: loadn is the engine package of the loadn-ai platform (org:
> **loadn-ai**). Product forms: `loadn` engine / `loadn-web` platform /
> `loadn desktop`.

## Companion research

loadn is the deployed reference system for a family of preprints (2026, to
appear). Each paper's claims are anchored to this codebase:

- **Defense-in-Depth for Agentic Execution: A Reference Architecture
  Evaluated by Deterministic Replay of an Online Codebase** — evaluates a
  frozen snapshot of this repo's platform: a framework-native seven-layer
  execution-security stack, pre-registered 96-scenario injection suite ×
  16-configuration ablation, 56 white-box evasion transforms. loadn is the
  deployed architecture under test; the snapshot manifest ships with the
  official release.
- **The Standing Layer: Agent = Model + Harness + Standing** — position
  paper: the third term of the agent formula is externally adjudicated
  standing (accounts, reputation, egress, funds, channels, budgets). The
  platform's resource console is the standing layer in deployed form, and
  its audit ledger is the paper's field-evidence source.
- **Toolbelt Richness: A Concept and Measurement Framework for Agent
  Environments** — defines toolbelt richness (introspectable API surface +
  version-matched docs + runnable examples; supply quality, not tool count)
  and measures three supply arms (skills+MCP vs pinned local toolbelt vs
  hybrid). loadn's local-first tool supply is the rich arm's design
  philosophy.

```
┌────────────────────────────────────────────────────────────┐
│  CLI:  loadn -p / REPL / python -m loadn                   │
├────────────────────────────────────────────────────────────┤
│  AgentCore   loop · LoopGuard · Compactor (92% window)     │
│              stream-event synth (--verbose deltas)         │
│              ContextAssembler · PermissionEngine · Hooks   │
│              SubagentManager (Task tool + .claude/agents)  │
├────────────────────────────────────────────────────────────┤
│  Tools: Bash(+cwd/env, 60s auto-bg) Read Write Edit        │
│         MultiEdit NotebookEdit Grep Glob Skill             │
│         InteractiveShell(pty) WebFetch WebSearch TodoWrite │
│         (+ MCP dynamic)                                    │
├────────────────────────────────────────────────────────────┤
│  Providers: Anthropic-native SSE │ OpenAI-compatible       │
│             retry/backoff │ usage accounting               │
├────────────────────────────────────────────────────────────┤
│  Persistence: transcript JSONL (append-only, resume,       │
│               compact-aware replay) + SQLite index         │
└────────────────────────────────────────────────────────────┘
```

## Monorepo: engine + platform

This repo is more than the engine — a fully self-hostable **multi-session
agent platform**:

| Component | What it is | Start |
|---|---|---|
| `loadn/` | The engine (main subject of this README) | `pip install loadn` |
| `loadn_webui/` | Web platform: session management / scheduling / cost / sharing, with a framework-native **seven-layer execution-security stack** (credential vault · bash-AST policy · egress allowlist proxy · bwrap sandbox · irreversible-action approval gate · canary+audit chain · supply-chain trust gate) | `pip install -e ".[webui]"` → `loadn-web init` → `loadn-web serve` |
| `desktop/` | Desktop form (Tauri shell + Debian rootfs: the whole execution domain runs inside a Linux VM on mac/win) | `desktop/image/` build pipeline |
| `ui/` | React frontend (admin center with security / traffic / cost panels) | `cd ui && npm run build` |

Docs index: [ARCHITECTURE](docs/ARCHITECTURE.md) (planes / data layout /
test system) · [CONFIG](docs/CONFIG.md) (every config key) ·
[EXTENDING](docs/EXTENDING.md) (customization seams: engines / providers /
skills / MCP / hooks) · [PROTOCOL](docs/PROTOCOL.md) (engine dialect
contract) · [RELEASE](docs/RELEASE.md) (deploy / upgrade / rollback) ·
[ATTACK_SURFACE](docs/ATTACK_SURFACE.md) (attack surface + AI-BOM).

## Three ideas this deployment embodies

**1 · Defense-in-depth along an irreversibility boundary.** The platform
ships a framework-native execution-security stack — seven layers, **no LLM
on the enforcement path**, fail-closed by default, tiered by action
reversibility (reversible work is fenced into the sandbox; irreversible
actions stop at the approval gate — the sandbox is the load-bearing wall):

| # | Layer | Mechanism | Source |
|---|---|---|---|
| L1 | Credential vault | AES-GCM; plaintext never enters the agent env | `loadn_webui/security/vault.py` |
| L2 | Bash AST policy | L0 red lines + token-coverage rules, out-of-process PreToolUse | `loadn_webui/security/policy.py` |
| L3 | Egress allowlist proxy | CONNECT allowlist, DNS-rebinding & private-net rejection | `loadn_webui/security/egress_proxy.py` |
| L4 | Filesystem sandbox | bwrap same-path binds; netns with proxy socket as sole egress | `loadn_webui/security/sandbox.py` |
| L5 | Irreversible-action approval gate | one-time codes bound to parameter hashes | `loadn_webui/security/approve.py` |
| L6 | Canary trip + audit chain | planted decoy credentials; hash-chained ledger with daily anchors | `loadn_webui/security/canary.py` · `audit.py` |
| L7 | Supply-chain trust gate | skill sha256 locks, eight-class static scans, untrusted degradation | `loadn_webui/security/skill_scan.py` |

**2 · The standing layer: agent = model + harness + standing.** Beyond
compute, task completion is conditioned on what the world will admit —
accounts, egress, funds, channels, rate budgets. The platform's resource
console manages exactly this admission sub-plane: credentials in the vault,
domain-level egress policy with session-scoped grants, approval-gated
irreversible actions, per-role convergence budgets, and custom services
injected as `LOADN_SVC_<NAME>_URL` endpoints. The operator holds the
resource-landlord role, and every adjudication lands in the audit ledger.

**3 · Local-first toolbelt over skill/MCP-heavy stacks.** The engine's tools
are plain Python you can introspect (`dir()` / `help()` / read the source);
the platform mounts pinned local runtimes and read-only data roots (resource
bridges) instead of wrapping every capability in prompt packages or RPC
interfaces. Skills and MCP are supported — as thin wrappers and dynamic
extensions, not the primary supply channel.

## Why another agent engine

- **Embeddable & hackable.** Typed Python 3.10+, one runtime dependency
  (`httpx`). The core loop (`AgentCore.run_turn`) is a library first; the CLI
  is a thin shell around it.
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
  guards, a two-tier loop-guard that interrupts repeated identical calls
  (soft remind → hard break), dual-signal stall detection for subprocesses,
  and compaction that keeps tool-call pairing intact. Stream interruptions
  and retriable provider errors are retried inside the loop — a turn never
  ends with a bogus success.
- **Cheap by default.** Three prompt-cache breakpoints (tools tail / system /
  last message) with a no-cache lane for auxiliary calls, a handoff-style
  compactor that prunes before summarizing (with a file ledger and UPDATE
  mode), a completion gate for grind mode (verbal-delivery interception +
  artifact existence checks), last-call usage accounting, and
  context-overflow self-rescue via forced compaction.
- **Won't die waiting on a shell.** Foreground commands that exceed 60s are
  automatically adopted into the background task table (the agent keeps
  working and `tail`s the log later); interactive programs (REPLs, terminal
  games, install wizards) get a pty-backed `InteractiveShell` tool that
  scripts multi-round send/expect exchanges in a single call.
- **Context engineering.** The system prompt assembles from a CLAUDE.md /
  AGENTS.md ancestor chain (git-root bounded, nearest last, `@import`
  support), a skills index, long-term memory with boundary-driven background
  extraction, and a repo map — mirroring Claude Code's layered memory.
- **Mutation-tested.** 1373 tests (zero token, fake-provider driven) at 83.1%
  line coverage — and beyond coverage, a homegrown mutation-testing runner
  has injected **4055 bugs into 47 core files with a 78% kill rate**,
  proving the assertions actually catch regressions, not just execute code
  paths. See [tests/TEST-PLAN.md](tests/TEST-PLAN.md) §7.

## Install

```bash
pip install loadn          # or: pip install git+https://github.com/loadn-ai/loadn
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
always the source of truth. Exit codes: `0` success, `1` any `error_*`
outcome. During long tool runs the CLI emits a `system/heartbeat` line every
30s so parent-process stall detectors don't kill healthy work.

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
- long-lived hosting: `loadn daemon` keeps AgentCore alive across
  disconnects (UDS attach), so cache warmth and context survive UI reconnects

## Configuration & extension points

| Thing | Where |
|---|---|
| Engine knobs (thresholds, budgets, limits) | `loadn/constants.py` |
| Permission rules | `.loadn/settings.json` in the project (deny/allow, `Bash:git push*` patterns; `.agent/` legacy path still honored); modes `default/acceptEdits/plan/bypassPermissions` |
| Hooks (Pre/PostToolUse, Stop, Session*) | `.loadn/settings.json` `hooks` — external commands, stdin JSON, exit 2 blocks |
| MCP servers | `.mcp.json` in the project (stdio + streamable-HTTP with OAuth); tools appear as `mcp__<server>__<tool>` |
| Skills | `.claude/skills/` / `.loadn/skills/` / `$LOADN_HOME/skills` SKILL.md — indexed in the system prompt; body loads on demand via the `Skill` tool (third-party installs go through a supply-chain lock) |
| Custom subagents | `.claude/agents/*.md` / `$LOADN_HOME/agents` — frontmatter `name/description/tools/model`; usable as `Task(subagent_type=…)` |
| Constitution chain | CLAUDE.md/AGENTS.md from the git root down to cwd, nearest last; `@./file.md` line imports; user-level `$LOADN_HOME/AGENT.md` on top |
| Platform config (50+ keys) | [docs/CONFIG.md](docs/CONFIG.md) — sandbox tiers, egress allowlist/three-state policy, approval TTLs, resource bridges |
| Thinking budget | `LOADN_THINKING_BUDGET` env or `extra.thinking_budget` (Anthropic-form `thinking.budget_tokens`; off by default) |
| Prompt caching | on by default (3 breakpoints); `extra.disable_prompt_cache` kills it |

## Testing your changes

```bash
pip install -e ".[dev]"
pytest            # 1373 tests, zero API calls
ruff check .
```

The fake provider (`LOADN_PROVIDER=fake`) replays control files from
`$LOADN_FAKE_DIR` — the same protocol used by end-to-end CLI tests. Beyond
pytest: 10 eval scenarios gate releases nightly, and
`scripts/mutate.py` measures whether your tests actually kill injected bugs
(contribution rule of thumb in [CLAUDE.md](CLAUDE.md) §测试同步纪律:
new guard logic needs a negative-path test; new modules need an entry in the
mutation TARGET_TESTS map).

## Status & roadmap

- [x] v0.4.x — open-source readiness: portable defaults, engine plugin
      entry point, community files, zero lint debt
- [x] v0.6.0 — per-task isolated workspaces, rotation anchors, crash-recovery
      bookkeeping, provider thinking continuation
- [x] v0.6.3 — interactive egress control: off/warn/enforce + ask-to-approve
      cards, session-level overrides, per-session attribution
- [x] v0.6.5 — every security mechanism explicitly configurable; host
      resource bridges
- [x] post-0.6.5 — test-quality campaign: 83.1% coverage, 2589-mutant
      verification across 41 files (76% kill rate), weekly mutation gate in CI
- [ ] PyPI publication
- [ ] desktop real-device validation (mac/win)
- [ ] Terminal-Bench baseline numbers
- [ ] Ollama provider, DeepSeek native; TUI (textual)

See [CHANGELOG.md](CHANGELOG.md) and [ROADMAP.md](ROADMAP.md).
Contributions welcome — [CONTRIBUTING.md](CONTRIBUTING.md) describes the
layout, how to add a tool or provider in one file, and the assertion-strength
rules (negative-path tests for new guards; mutation narrow-set for touched
files).

## License

Apache-2.0 — see [LICENSE](LICENSE).
