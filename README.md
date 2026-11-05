# loadn

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <img src="docs/assets/logo.svg" alt="loadn" width="330">
</picture>

[![CI](https://img.shields.io/github/actions/workflow/status/loadn-ai/loadn/ci.yml?branch=main&label=CI)](https://github.com/loadn-ai/loadn/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/loadn-ai/loadn)](https://github.com/loadn-ai/loadn/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![Code Style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-1837%20%C2%B7%200%20API%20calls-green)](tests/TEST-PLAN.md)

English | **[简体中文](README.zh-CN.md)**

> As heaven moves with vigor — OldMan; as earth lies receptive — LoadN.

**A drop-in, self-hostable coding-agent platform.** `loadn` is a pure-Python
agent engine whose CLI speaks the Claude Code `stream-json` dialect — any
harness built for `claude -p` can drive it unchanged — grown into a full
platform: multi-session web workbench, digital employees, a resource hub,
plan-before-execute approval, scheduling, cost accounting, and a
seven-layer execution-security stack. One `pip install`, any
Anthropic-form or OpenAI-compatible endpoint, zero external services.

![loadn-web workbench tour: session stream with thinking cards, tool calls, subagent dispatch, then the artifacts and workspace panels](docs/assets/demo-workbench.gif)

## Why loadn

| | |
|---|---|
| 🔌 **Drop-in engine** | argv contract and event stream isomorphic to Claude Code headless mode. Swap `claude -p` for `loadn -p` in any supervisor — sessions, resume, locks, heartbeats, `usage`/`modelUsage` accounting all match. |
| 🏠 **Self-hosted platform** | `loadn-web` gives you sessions, projects, scheduling, wake-up timers, cost dashboards and audit — your models, your machine, your data. |
| 📋 **Plan before execute** | Plan Mode (v0.7.74): complex tasks produce a structured plan card first; nothing touches disk until you approve. Revise by just chatting — v2, v3… |
| 🔐 **Seven-layer security** | Vault (AES-GCM) → bash-AST policy → egress allowlist proxy → filesystem sandbox → irreversible-action approval codes → canary + hash-chained audit → supply-chain gate. Deterministic, fail-closed, no LLM on the enforcement path. |
| 🧩 **Anything-shaped model layer** | Anthropic-form SSE (GLM, Anthropic) or OpenAI-compatible (DeepSeek, vLLM, LiteLLM…), one adapter. Engine runtime ships a single dependency: `httpx`. |
| 🧪 **Tested like infrastructure** | 1837 tests, zero API calls, ≥83% coverage; 4055 injected bugs across 47 core files with a 78% mutation-kill rate, gated weekly in CI. |

### Product forms in one repo

| Path | Contents | How to start |
|---|---|---|
| `loadn/` | The agent engine (main subject of this document) | `pip install loadn` |
| `loadn_webui/` | Web platform: sessions, organization, resource hub, security, scheduling, cost accounting | `pip install -e ".[webui]"` → `loadn-web init` → `loadn-web serve` |
| `desktop/` | Desktop distribution: Tauri shell with a Debian rootfs; the whole execution domain runs inside a Linux VM on macOS and Windows | `desktop/image/` build pipeline |
| `ui/` | React frontend for the administration center (security, resource, and organization panels) | `cd ui && npm run build` |

**Status.** v0.7.x (beta), actively maintained. The CLI argv contract, the
`stream-json` dialect, and configuration keys follow Semantic Versioning;
breaking changes are announced in [CHANGELOG.md](CHANGELOG.md) with every
minor release.

## Plan Mode — read-only planning, human adjudication

![Plan Mode: the agent finished a read-only exploration and presents a structured plan card — goal, steps, files, risks, open questions — awaiting approve / abort](docs/assets/plan-mode.png)

Complex or risky tasks benefit from an alignment step before any edit. Turn
on the 📋 **plan** switch in the session header and the next task runs as a
*plan turn*: the agent explores the workspace **read-only** (no writes, no
edits, no deletions) and finishes with a structured plan card — goal,
numbered steps, files/resources involved, risks and mitigations, and open
questions back to you.

- **✓ Approve** — plan steps become the session todo list and an execution
  turn starts, carrying the task text plus the full approved plan. Todos in
  place keep the execution turn from being re-intercepted into planning.
- **Revise by chatting** — there is no "edit" button on purpose: send your
  corrections as a normal message and the session automatically re-enters a
  plan turn (plan v2, v3…). In headless environments the interactive
  confirm of Claude Code's plan mode becomes an async card — the session
  simply waits.
- **Abort** — discard the plan; nothing was touched anyway.
- If a model fails to emit the structured plan, a fallback card shows the
  raw planning text and still offers the same two decisions.

## Feature tour

| Screenshot | What you are looking at |
|---|---|
| [![workbench](docs/assets/chat-workspace.png)](docs/assets/chat-workspace.png) | **Workbench** — live agent stream with thinking cards, tool-call cards, subagent dispatch, steering notes; right panel groups deliverables per subtask and agent. |
| [![artifacts](docs/assets/artifacts-panel.png)](docs/assets/artifacts-panel.png) | **Artifacts panel** — every deliverable the session produced, with preview / download / share / export-to-html-docx. |
| [![preview](docs/assets/artifact-preview.png)](docs/assets/artifact-preview.png) | **In-place artifact preview** — read a deliverable without leaving the session. |
| [![workspace](docs/assets/workspace-panel.png)](docs/assets/workspace-panel.png) | **Workspace browser** — the task's whole working tree, filtered per subtask, one-click zip export. |
| [![org](docs/assets/org-page.png)](docs/assets/org-page.png) | **Digital employees** — departments and named employees, each an agent definition (persona, skills, duty) hot-injected into the subagent registry; dispatch by name with `Task(subagent_type="xiaoming")`. |
| [![skills](docs/assets/skills-page.png)](docs/assets/skills-page.png) | **Skills library** — local and marketplace skills (agentskills.io `SKILL.md`), lifecycle states, and a suggestion loop that turns your corrections into reviewable skills. |
| [![resources](docs/assets/resource-hub.png)](docs/assets/resource-hub.png) | **Resource hub** — every resource is a `Resource.md`; credentials bind from the encrypted vault **by reference** and inject into the task environment as `LOADN_SVC_*` variables. |
| [![drawer](docs/assets/resource-drawer.png)](docs/assets/resource-drawer.png) | **Resource drawer** — structured form ↔ markdown source, two-way live sync, with a preview of exactly what the agent will see. |
| [![security](docs/assets/security-center.png)](docs/assets/security-center.png) | **Security center** — sandbox, approvals, egress, vault, audit, canary and target-policy posture on one screen. |
| [![cost](docs/assets/cost-panel.png)](docs/assets/cost-panel.png) | **Cost accounting** — three-way cost (CLI nominal / API metered / plan credits) across 7/30/90-day windows. |
| [![schedules](docs/assets/schedule-panel.png)](docs/assets/schedule-panel.png) | **Scheduling** — wake-up timers and scheduled tasks per session, with run history. |

![admin center tour: organization console → security posture → resource hub → cost accounting](docs/assets/demo-admin-center.gif)

### Agent runtime

- Agent loop with a permission engine, hooks, and a two-tier loop guard
  that interrupts repeated identical tool calls (soft reminder, then hard
  break).
- Context compaction at a 92% window that preserves tool-call pairing;
  stream interruptions and retriable provider errors are retried inside the
  loop, so a turn never ends with a bogus success.
- Session persistence: append-only JSONL transcripts with a SQLite index;
  replay resumes from the last compaction point. Reloaded sessions replay
  byte-identically with the live stream.
- `loadn daemon` keeps AgentCore alive across disconnects (UDS attach), so
  cache warmth and context survive UI reconnects.
- Long-running work: foreground commands exceeding 60 s are adopted into a
  background task table automatically; interactive programs (REPLs, install
  wizards) get a pty-backed `InteractiveShell` tool that scripts multi-round
  send/expect exchanges in a single call.

### Tools and extensibility

- Built-in toolset: file editing (Read / Write / Edit / MultiEdit), search
  (Grep / Glob), shell (Bash, InteractiveShell), delegation (Task),
  on-demand knowledge (Skill), web (WebFetch / WebSearch), and task
  tracking (TodoWrite).
- MCP servers declared in `.mcp.json` (stdio and streamable HTTP with
  OAuth); their tools appear as `mcp__<server>__<tool>`.
- Skills follow the agentskills.io `SKILL.md` standard; third-party
  installs pass a supply-chain lock, and any skill exports as a compatible
  zip.
- Custom subagents defined as markdown files with
  `name/description/tools/model` frontmatter, usable through
  `Task(subagent_type=...)`.

### Provider-agnostic model layer

Any Anthropic-form SSE endpoint (Z.AI GLM, Anthropic proper) and any
OpenAI-compatible endpoint (DeepSeek, vLLM, LiteLLM, and others).
Thinking/reasoning content and tool-call syntax are translated in a single
adapter layer.

### Claude Code `stream-json` compatibility

- The argv contract and event stream are deliberately isomorphic to Claude
  Code's headless mode (`-p --verbose --output-format stream-json`,
  prompt as `argv[-1]`, `--session-id` / `--resume`, and
  `system/assistant/user/result` events).
- The final `result` event carries snake_case `usage` plus camelCase
  per-model `modelUsage`, ready for cost dashboards.
- During long tool runs the CLI emits a `system/heartbeat` line every 30 s,
  so parent-process stall detectors do not kill healthy work.
- The live session lock reproduces the `Session ID already in use` error
  (stderr, exit 1) that supervisors match on.

### Context engineering

The system prompt assembles from a CLAUDE.md / AGENTS.md ancestor chain
(git-root bounded, nearest last, `@import` support), a skills index,
long-term memory with boundary-driven background extraction, and a
repository map — mirroring Claude Code's layered memory.

### Cost discipline

Three prompt-cache breakpoints (tools tail / system / last message) with a
no-cache lane for auxiliary calls; a handoff-style compactor that prunes
before summarizing (file ledger and UPDATE mode); a completion gate for
grind mode (verbal-delivery interception plus artifact existence checks);
last-call usage accounting; and context-overflow self-rescue via forced
compaction.

### Execution security

Security is treated as four multiplied terms: sandbox, control points,
vault, and audit. No LLM sits on the enforcement path; every layer is
deterministic and fail-closed.

```mermaid
flowchart LR
    CALL["agent tool call"] --> L1["L1<br/>credential<br/>vault"] --> L2["L2<br/>bash-AST<br/>policy"] --> L3["L3<br/>egress allowlist<br/>proxy"] --> L4["L4<br/>filesystem<br/>sandbox"] --> L5["L5<br/>irreversible-action<br/>approval gate"] --> L6["L6<br/>canary +<br/>audit chain"] --> L7["L7<br/>supply-chain<br/>trust gate"] --> EXEC["exec"]
    style CALL fill:#F8FAFC,stroke:#94A3B8
    style EXEC fill:#FEF3C7,stroke:#F59E0B
```

| Layer | Term | Mechanism | Source |
|---|---|---|---|
| L1 | Vault | AES-GCM encryption; plaintext never enters the agent environment | `loadn_webui/security/vault.py` |
| L2 | Control point | Bash AST policy: red lines plus token-coverage rules, out-of-process PreToolUse | `loadn_webui/security/policy.py` |
| L3 | Control point | Egress allowlist proxy; CONNECT allowlist, DNS-rebinding and private-net rejection | `loadn_webui/security/egress_proxy.py` |
| L4 | Sandbox | bwrap same-path binds; network namespace with the proxy socket as sole egress | `loadn_webui/security/sandbox.py` |
| L5 | Control point | Irreversible-action approval gate: one-time codes bound to parameter hashes | `loadn_webui/security/approve.py` |
| L6 | Audit | Canary trips plus hash-chained ledger with daily anchors | `loadn_webui/security/canary.py` · `audit.py` |
| L7 | Control point | Supply-chain trust gate: skill sha256 locks, eight-class static scans | `loadn_webui/security/skill_scan.py` |

### Quality

- 1837 tests with zero API calls (fake-provider driven) at ≥83% line
  coverage.
- Mutation testing: 4055 injected bugs across 47 core files with a 78% kill
  rate, plus a weekly mutation gate in CI. See
  [tests/TEST-PLAN.md](tests/TEST-PLAN.md) §7.

## Architecture

```mermaid
flowchart TB
    subgraph HOSTS["CLI · library · daemon"]
        HEAD["headless -p<br/>(stream-json NDJSON)"]
        REPL["REPL"]
        DAEMON["daemon (UDS attach)"]
        LIB["python library<br/>build_agent()"]
    end
    subgraph CORE["AgentCore"]
        ASM["ContextAssembler"] --> LOOP["agent loop"]
        LOOP --> PERM["PermissionEngine"]
        LOOP --> HOOKS["Hooks"]
        LOOP --> GUARD["LoopGuard"]
        LOOP --> COMP["Compactor (92% window)"]
        LOOP --> SUB["SubagentManager"]
        LOOP --> EVT["stream-event synth<br/>(--verbose deltas)"]
    end
    subgraph TOOLS["Tools — built-in set + MCP dynamic"]
        BASH["Bash / Read / Write<br/>Edit / MultiEdit / Grep / Glob"]
        SHELL["InteractiveShell (pty) · Task · Skill<br/>WebFetch · WebSearch · TodoWrite"]
        MCP["mcp__server__tool"]
    end
    subgraph PROV["Providers"]
        ANT["Anthropic-form SSE"]
        OAI["OpenAI-compatible"]
    end
    PERSIST[("transcript JSONL (append-only)<br/>+ SQLite index")]

    HOSTS --> CORE
    CORE <-- "tool calls" --> TOOLS
    CORE <-- "chat chunks" --> PROV
    CORE --> PERSIST
```

Full planes, data layout, and test system: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

**1 · Install** (Python ≥ 3.10; on Debian 12+/Ubuntu 23.04+ system Python,
install into a venv):

```bash
python3 -m venv ~/.venvs/loadn
source ~/.venvs/loadn/bin/activate
pip install loadn          # or: pip install git+https://github.com/loadn-ai/loadn
```

**2 · Configure an endpoint** — no config file required; endpoints resolve
in this order:

1. `$LOADN_HOME/config.json` — user overrides (`LOADN_HOME` defaults to `~/.loadn`)
2. Environment variables: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`,
   `LOADN_MODEL`, `LOADN_PROVIDER` (`anthropic` | `openai` | `fake`)
3. The `env` block of `~/.claude/settings.json`, if present — existing
   Claude CLI gateway setups are reused as-is

On first run without a configured endpoint, an interactive terminal starts
an onboarding wizard (preset endpoint menu, masked key entry) and writes
`$LOADN_HOME/config.json`. Headless (`-p`) and non-tty stdin never enter
the wizard — the CLI fails fast (exit 2) with hints.

**3 · First run:**

```bash
# one-shot, human-readable output
loadn -p --dangerously-skip-permissions "read ./note.txt and summarize it"

# headless event stream (Claude Code stream-json dialect)
loadn -p --verbose --output-format stream-json \
  --session-id 11111111-2222-3333-4444-555555555555 "fix the failing test"

# continue a session / structured result only / interactive REPL
loadn -p --resume 11111111-2222-3333-4444-555555555555 "continue"
loadn -p --output-format json "list the todos"
loadn                       # REPL: /help /resume /fork /compact /todos
```

**Want the whole platform?**

```bash
pip install "loadn[webui]"
loadn-web init            # data root + config.yaml template + access token
loadn-web serve           # http://127.0.0.1:8792 — interactive API docs at /docs
```

From source with the full web UI: `git clone
https://github.com/loadn-ai/loadn && cd loadn && pip install -e ".[webui]"`
then `(cd ui && npm ci && npm run build)`.

<details>
<summary><b>Optional extras</b></summary>

| Extra | Provides |
|---|---|
| `ast` | Bash AST parsing for permission policies (`bashlex`) |
| `repomap` | Symbol extraction for repository maps (tree-sitter) |
| `tui` | Full-screen terminal UI (textual); the plain REPL works without it |
| `dev` | Test and lint toolchain (pytest, ruff, playwright) |
| `webui` | `loadn-web` platform dependencies (FastAPI, cryptography, …) |

> TUI note: verified on Python 3.10/3.11. On Python 3.12 under a
> pseudo-terminal (pexpect, `script(1)`, some CI runners), textual 8.x may
> not deliver key events to input fields — the plain REPL
> (`LOADN_NO_TUI=1`) is unaffected.
</details>

## Usage examples

### Event stream shape

NDJSON on stdout, one JSON object per line:

```json
{"type":"system","subtype":"init","session_id":"…","model":"…","tools":[…]}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Let me "}},"parent_tool_use_id":null}
{"type":"assistant","message":{"id":"msg_…","role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"Bash","input":{"command":"ls"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":"…","is_error":false}]}}
{"type":"result","subtype":"success","usage":{"input_tokens":…,"output_tokens":…,"cache_read_input_tokens":…,"cache_creation_input_tokens":…},"modelUsage":{…},"num_turns":2,"duration_ms":…}
```

`stream_event` lines (Anthropic SSE-shaped deltas, only with `--verbose`)
are best-effort transport for live rendering; the assembled `assistant`
event is always the source of truth. Exit codes: `0` for success, `1` for
any `error_*` outcome.

### As a Python library

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

`AgentCore` is also constructible piecewise (custom tool registry,
permission mode, hooks, compactor off, …) — see `loadn/core/build.py`.

### Embedding into an existing harness

Any supervisor that spawns `claude -p --output-format stream-json` can
spawn `loadn` instead:

- fresh sessions: `--session-id <uuid>`; continuations: `--resume <uuid>`;
  `--fork` copies a resumed session to a new id
- concurrent reuse of a live session is rejected with the same wording
  (`Session ID already in use`, stderr, exit 1)
- accounting: the final `result` event carries `usage` and per-model
  `modelUsage` for cost dashboards
- per-session transcripts live under `$LOADN_HOME/sessions/<sid>/`
  (append-only JSONL; replay resumes from the last compaction point)
- long-lived hosting: `loadn daemon` keeps AgentCore alive across
  disconnects (UDS attach)

## Configuration

| Item | Location |
|---|---|
| Engine thresholds, budgets, and limits | `loadn/constants.py` |
| Permission rules | `.loadn/settings.json` in the project (deny/allow, `Bash:git push*` patterns; the legacy `.agent/` path is still honored); modes: `default` / `acceptEdits` / `plan` / `bypassPermissions` |
| Plan Mode | Session-level 📋 switch (off by default) — see [Plan Mode](#plan-mode--read-only-planning-human-adjudication) |
| Hooks (Pre/PostToolUse, Stop, Session*) | `hooks` key of `.loadn/settings.json` — external commands, stdin JSON, exit code 2 blocks |
| MCP servers | `.mcp.json` in the project (stdio and streamable HTTP with OAuth) |
| Skills | `.agents/skills/` (agentskills.io standard, highest priority) / `.claude/skills/` / `.loadn/skills/` / `$LOADN_HOME/skills` — indexed in the system prompt, body loaded on demand via the `Skill` tool |
| Custom subagents | `.claude/agents/*.md` / `$LOADN_HOME/agents` — frontmatter `name/description/tools/model`; platform employees are hot-injected here |
| Constitution chain | CLAUDE.md / AGENTS.md from the git root down to cwd, nearest last; `@./file.md` line imports; user-level `$LOADN_HOME/AGENT.md` on top |
| Platform configuration (50+ keys) | `config.yaml` at the data root, generated by `loadn-web init` (template: `config.yaml.example`); every key documented in [docs/CONFIG.md](docs/CONFIG.md) |
| Thinking budget | `LOADN_THINKING_BUDGET` env or `extra.thinking_budget` (Anthropic-form `thinking.budget_tokens`; off by default) |
| Prompt caching | On by default (three breakpoints); `extra.disable_prompt_cache` disables it |

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Planes, data layout, test system |
| [docs/CONFIG.md](docs/CONFIG.md) | Every configuration key |
| [docs/EXTENDING.md](docs/EXTENDING.md) | Customization seams: engines, providers, skills, MCP, hooks |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | Engine event-dialect contract |
| [docs/RELEASE.md](docs/RELEASE.md) | Deployment, upgrade, rollback |
| [docs/ATTACK_SURFACE.md](docs/ATTACK_SURFACE.md) | Attack surface and AI-BOM |
| [docs/compat.md](docs/compat.md) | Compatibility notes |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [ROADMAP.md](ROADMAP.md) | Roadmap and pending milestones |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Repository layout, one-file tool/provider recipes, assertion-strength rules |
| [SECURITY.md](SECURITY.md) | Security policy |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Code of conduct |
| [tests/TEST-PLAN.md](tests/TEST-PLAN.md) | Test plan, including the mutation-testing methodology |
| [CITATION.cff](CITATION.cff) | Citation metadata |

## FAQ

**Which models and providers are supported?**
The model layer is provider-agnostic: any Anthropic-form SSE endpoint (for
example Z.AI GLM or Anthropic) and any OpenAI-compatible endpoint
(DeepSeek, vLLM, LiteLLM, and others). Select with `LOADN_PROVIDER` and
point `ANTHROPIC_BASE_URL` at the gateway.

**Does the CLI require configuration before first use?**
No. Endpoint resolution follows the order listed under
[Quick start](#quick-start); an unconfigured first run enters the
interactive onboarding wizard and writes `$LOADN_HOME/config.json`. The
`fake` provider never triggers it.

**Where are sessions stored?**
Under `$LOADN_HOME/sessions/<session-id>/` (default `~/.loadn`):
append-only JSONL transcripts plus a SQLite index. Replay resumes from the
last compaction point.

**Can loadn replace `claude -p` in an existing supervisor?**
Yes. The argv contract and event stream are deliberately isomorphic, and
the live session lock reproduces the `Session ID already in use` error
(stderr, exit 1) that supervisors match on.

**How do I disable permission prompts in CI?**
Use `--dangerously-skip-permissions` (alias `--yolo`) or
`--permission-mode bypassPermissions`. For interactive use, prefer scoped
deny/allow rules in `.loadn/settings.json`.

**What do the exit codes mean?**
`0` for success; `1` for any `error_*` result.

**Do the tests call paid APIs?**
No. All 1837 tests run against the fake provider with zero API calls.

**How are the engine, platform, and desktop related?**
One monorepo, three product forms: the `loadn` engine, the `loadn-web`
platform (CLI entry point `loadn-web`; `wd` is a deprecated alias), and
the desktop distribution built from `desktop/`.

**How do I add a tool, a provider, or a skill?**
See [docs/EXTENDING.md](docs/EXTENDING.md) and
[CONTRIBUTING.md](CONTRIBUTING.md); most extensions are a single file.

**Is there a desktop application?**
Yes, `desktop/` contains the Tauri-based distribution (the execution
domain runs inside a Linux VM on macOS and Windows); real-device
validation is in progress.

## License

Apache-2.0 — see [LICENSE](LICENSE).
