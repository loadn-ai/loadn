# hahaness

**A self-contained coding-agent engine in pure Python.** One `pip install`, any
Anthropic-form or OpenAI-compatible endpoint, and you get a headless agent with
tools, MCP, subagents, session persistence, and context compaction — speaking
the Claude Code `stream-json` dialect, so existing harnesses can drive it as a
drop-in subprocess engine.

```
┌────────────────────────────────────────────────────────────┐
│  CLI:  hahaness -p / REPL / python -m hahaness             │
├────────────────────────────────────────────────────────────┤
│  AgentCore   loop · LoopGuard · Compactor (92% window)     │
│              ContextAssembler · PermissionEngine · Hooks   │
│              SubagentManager (Task tool, sem=4)            │
├────────────────────────────────────────────────────────────┤
│  Tools: Bash Read Write Edit Grep Glob                     │
│         WebFetch WebSearch TodoWrite (+ MCP dynamic)       │
├────────────────────────────────────────────────────────────┤
│  Providers: Anthropic-native SSE │ OpenAI-compatible       │
│             retry/backoff │ usage accounting               │
├────────────────────────────────────────────────────────────┤
│  Persistence: transcript JSONL (append-only, resume,       │
│               compact-aware replay) + SQLite index         │
└────────────────────────────────────────────────────────────┘
```

## Why another agent engine

- **Embeddable & hackable.** ~7k lines of typed Python 3.10+, one runtime
  dependency (`httpx`). The core loop (`AgentCore.run_turn`) is a library
  first; the CLI is a thin shell around it.
- **Speaks `stream-json` natively.** The CLI's argv contract and event stream
  are deliberately isomorphic to Claude Code's headless mode
  (`-p --verbose --output-format stream-json`, prompt as `argv[-1]`,
  `--session-id`/`--resume`, `system/assistant/user/result` events with
  `usage` + per-model `modelUsage`). Harnesses built for the Claude CLI can
  spawn hahaness instead — supervision, accounting, and UI keep working.
- **Provider-agnostic.** Works with Anthropic-form gateways (Z.AI GLM,
  Anthropic proper) and any OpenAI-compatible endpoint (DeepSeek, vLLM,
  LiteLLM, ...), with thinking/reasoning and tool-call translation handled in
  one adapter layer.
- **Discipline built in.** Output truncation budgets, read-before-write file
  guards, a loop-guard that interrupts repeated identical calls, dual-signal
  stall detection for subprocesses, and compaction that keeps tool-call
  pairing intact.
- **Zero-token test suite.** 150+ tests drive every loop branch through a
  scripted fake provider — CI needs no API keys.

## Install

```bash
pip install hahaness          # or: pip install git+https://github.com/<you>/hahaness
```

No config required — endpoint resolution order:

1. `$HAHANESS_HOME/config.json` (your overrides)
2. environment: `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `HAHANESS_MODEL`
   / `HAHANESS_PROVIDER` (`anthropic` | `openai` | `fake`)
3. `~/.claude/settings.json` env block (if you already run the Claude CLI
   against a gateway, hahaness reuses it as-is)

## Quickstart

```bash
# one-shot, human-readable
hahaness -p --dangerously-skip-permissions "read ./note.txt and summarize it"

# headless event stream (Claude Code stream-json dialect)
hahaness -p --verbose --output-format stream-json \
  --session-id 11111111-2222-3333-4444-555555555555 "fix the failing test"

# continue a session / structured result only / interactive REPL
hahaness -p --resume 11111111-2222-3333-4444-555555555555 "continue"
hahaness -p --output-format json "list the todos"
hahaness                       # REPL: /help /resume /fork /compact /todos
```

Event stream shape (NDJSON on stdout, one JSON object per line):

```json
{"type":"system","subtype":"init","session_id":"…","model":"…","tools":[…]}
{"type":"assistant","message":{"id":"msg_…","role":"assistant","content":[{"type":"tool_use","id":"tu_1","name":"Bash","input":{"command":"ls"}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"tu_1","content":"…","is_error":false}]}}
{"type":"result","subtype":"success","usage":{"input_tokens":…,"output_tokens":…,"cache_read_input_tokens":…,"cache_creation_input_tokens":…},"modelUsage":{…},"num_turns":2,"duration_ms":…}
```

Exit codes: `0` success, `1` any `error_*` outcome (error text goes to stderr —
same position the Claude CLI puts it). During long tool runs the CLI emits a
`system/heartbeat` line every 30s so parent-process stall detectors don't
kill healthy work.

## Use as a library

```python
import asyncio
from pathlib import Path
from hahaness.core.build import build_agent

async def main():
    bundle = await build_agent(Path.cwd())          # provider/tools/session wired
    summary = await bundle.core.run_turn("find TODOs and write them to notes/")
    print(summary.text, summary.usage, summary.model_usage)

asyncio.run(main())
```

`AgentCore` is also constructible piecewise (custom tool registry, permission
mode, hooks, compactor off, ...) — see `hahaness/core/build.py`.

## Embedding into a harness

Because the CLI mirrors Claude Code's headless contract, any supervisor that
spawns `claude -p --output-format stream-json` can spawn `hahaness` instead:

- fresh sessions: `--session-id <uuid>`; continuations: `--resume <uuid>`
- a live session lock rejects concurrent use with the same
  `Session ID already in use` wording (stderr, exit 1)
- accounting: the final `result` event carries snake_case `usage` and
  camelCase per-model `modelUsage` for cost dashboards
- per-session transcripts live under `$HAHANESS_HOME/sessions/<sid>/`
  (append-only JSONL; replay resumes from the last compaction point)

## Configuration & extension points

| Thing | Where |
|---|---|
| Engine knobs (thresholds, budgets, limits) | `hahaness/constants.py` |
| Permission rules | `.agent/settings.json` in the project (deny/allow, `Bash:git push*` patterns); modes `default/acceptEdits/plan/bypassPermissions` |
| Hooks (Pre/PostToolUse, Stop, Session*) | `.agent/settings.json` `hooks` — external commands, stdin JSON, exit 2 blocks |
| MCP servers | `.mcp.json` in the project (stdio; tools appear as `mcp__<server>__<tool>`) |
| Skills index | `.claude/skills/` / `.agent/skills/` SKILL.md frontmatter — name+description only in system prompt |
| Web search backend | `HAHANESS_SEARCH_PROVIDER` (`bocha`\|`zhipu`) + `HAHANESS_SEARCH_KEY` |
| Model variant suffixes | `glm-5.3[1m]` — `[...]` is treated as a client-side window hint, stripped for API calls |

## Development

```bash
pip install -e ".[dev]"
pytest            # 150+ tests, zero API calls
ruff check .
```

The fake provider (`HAHANESS_PROVIDER=fake`) replays control files from
`$HAHANESS_FAKE_DIR` (`reply`/`tools`/`todos`/`fail`/`fastfail`/`bigusage`/
`hang`/`giantline`) — the same protocol used by the end-to-end CLI tests.

## Status & roadmap

- [x] v0.1.0 — W1–W5 complete: providers, tools, loop, permissions, hooks,
      compaction, subagents, MCP, persistence, headless CLI, REPL
- [ ] Terminal-Bench baseline numbers
- [ ] Ollama provider, DeepSeek native
- [ ] TUI (textual)
- [ ] MCP server mode (hahaness as an MCP server)

See [CHANGELOG.md](CHANGELOG.md). Contributions welcome —
[CONTRIBUTING.md](CONTRIBUTING.md) describes the layout and how to add a tool
or provider in one file.

## License

Apache-2.0 — see [LICENSE](LICENSE).
