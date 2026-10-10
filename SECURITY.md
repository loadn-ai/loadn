# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.7.x | ✅ |
| ≤ 0.6.x | ❌ end of life |

## Reporting a Vulnerability

**Do not open a public issue for security problems.** Report via GitHub
private security advisories instead: **Security → Report a vulnerability**
on this repository — it reaches the maintainers directly and keeps the
details private until a fix ships.

- We acknowledge reports within **7 days**.
- We aim for a fix or mitigation within **90 days**, coordinated with the
  reporter on disclosure timing.

Out of scope for private advisories (file a regular issue instead):

- attacks that require `--yolo` / `--dangerously-skip-permissions`
  (see design boundaries below)
- misconfiguration of your own deployment (egress allowlist too broad,
  sandbox disabled, secrets committed to your fork)

## Design boundaries relevant to security

- loadn runs under the permission mode **you** declare.
  `--dangerously-skip-permissions` / `--yolo` admits every tool call that is
  not on the deny list — assess the risk yourself before running headless
  workloads outside a container/sandbox.
- Credentials enter only via environment variables or
  `$LOADN_HOME/config.json`; they are never written to transcripts.
- Subprocess governance only manages process groups the engine itself
  registered — it never scans the global process table.

For the platform's full seven-layer execution-security stack (credential
vault, bash-AST policy, egress allowlist proxy, filesystem sandbox,
irreversible-action approval gate, canary + audit chain, supply-chain trust
gate), see [docs/ATTACK_SURFACE.md](docs/ATTACK_SURFACE.md).
