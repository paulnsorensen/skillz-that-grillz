---
status: draft
owner: paulnsorensen
last_verified: 2026-09-23
confidence: medium
sources:
  - ../research/agent-friendly-cli-design-libraries/agent-friendly-cli-design-libraries.md
---
# Agent-friendly CLI design

Sources agree that a CLI for agents needs machine-readable output and strict input checks with actionable errors. Sources disagree on JSON input. No source examined recommends silent auto-correction of arguments. The full cited research is in the corpus at `docs/research/agent-friendly-cli-design-libraries/`.[^1]

## Output

- Give `--output json` (or `--json`) on data commands. Poehnelt calls this "table stakes".
- Send results to stdout and diagnostics to stderr. Use exit code 0 for success and non-zero for failure.
- Remove color, spinners, and prompts when stdout is not a TTY.
- TOON output (wevm `incur` default) uses fewer tokens than JSON, but independent benchmarks show lower model accuracy than JSON. See [TOON versus JSON for LLMs](toon-versus-json-for-llms.md).

## Input

- Disagreement: Poehnelt recommends raw `--json` payloads instead of bespoke flags. A Microsoft experiment found that args-only input was never less correct than `--json`-only input and always cost less.
- Treat agent input as adversarial. Reject control characters, path traversal, and embedded query parameters.
- Make every input available as a flag. An interactive prompt blocks an agent.

## Typos and errors

- Suggest, do not auto-correct. Cobra, clap, and Typer suggest close command names by default. Click adds option suggestions to its error. Python 3.14 argparse has opt-in `suggest_on_error`.
- A good error names the problem, shows the correct invocation, suggests valid values, and gives an example. The agent can then self-correct in one retry.

## Libraries

| Library | Language | Agent features |
| --- | --- | --- |
| wevm `incur` | TypeScript | TOON default, `--llms`, `--schema`, `--mcp`, token limits |
| `gws` (Google Workspace CLI) | Rust | JSON output, 40+ agent skills (a product, not a framework) |
| oclif | TypeScript | opt-in `enableJsonFlag` |
| Cobra, clap | Go, Rust | typo suggestions by default |
| cyclopts | Python | "Did you mean" for commands, options, choices, and misplaced options (`difflib`, cutoff 0.6); no JSON, MCP, or TOON mode found |
| Typer, Click, argparse | Python | typo suggestions only |
| `click-to-mcp` | Python | wraps a Click or Typer CLI as an MCP server (7 stars) |
| FastMCP `generate-cli` | Python | creates a CLI from an MCP server (reverse direction) |
| `toon-python` | Python | TOON encoder and decoder |

No Python framework examined has a built-in `--llms`, `--mcp`, or TOON output mode.

[^1]: `docs/research/agent-friendly-cli-design-libraries/agent-friendly-cli-design-libraries.md` (researched 2026-09-23).

_Source: /briesearch run 2026-09-23 · Updated: 2026-09-23 · Supersedes: "No independent measurement was found" for TOON (independent TOON benchmarks, 2026-09-23)_
