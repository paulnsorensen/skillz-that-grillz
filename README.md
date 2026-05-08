# 🧀 skillz-that-grillz 🧀

[![CI](https://img.shields.io/github/actions/workflow/status/paulnsorensen/skillz-that-grillz/validate.yml?branch=main&label=CI&style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/paulnsorensen/skillz-that-grillz?style=flat-square)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/paulnsorensen/skillz-that-grillz?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/releases/latest)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?style=flat-square)](https://www.conventionalcommits.org)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-spec-blueviolet?style=flat-square)](https://agentskills.io/specification)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/pulls)

> _Tight little toolbelt of git, GitHub, and project-runner skills._

A focused, skills-only repository of [Agent Skills](https://agentskills.io/specification)
for the everyday plumbing around a project: making a clean commit, working a
GitHub PR, stacking branches with Graphite, scaffolding a justfile, and wiring
up prek pre-commit hooks. No agents, no orchestration, no MCP requirements —
just self-contained `SKILL.md` files that any spec-compliant harness can load.

The companion repo [easy-cheese](https://github.com/paulnsorensen/easy-cheese)
covers the design / implement / review workflow (mold, cook, press, age, cure).
This repo covers the surrounding mechanics.

## Skill layout

This repo follows the [Agent Skills spec](https://agentskills.io/specification):

```text
skills/
└── <skill-name>/
    ├── SKILL.md          # required: name + description + body
    ├── references/       # optional: detail pulled in on demand
    ├── scripts/          # optional: executable helpers
    └── assets/           # optional: templates / static resources
```

Each `SKILL.md` is self-contained markdown with YAML frontmatter. There are no
nested sub-skills; deeper material lives in `references/<topic>.md` so the
harness can load it progressively.

## Skills

| Skill path | Command | Purpose |
| --- | --- | --- |
| `skills/commit/SKILL.md` | `/commit` | Stage and commit changes with conventional-commits messages, no `git add -A`, no `--no-verify`, no amends to published commits. Hand off to `/gh` for push / PR. |
| `skills/gh/SKILL.md` | `/gh` | All GitHub plumbing — PRs, issues, CI checks, releases, code search — via the GitHub MCP plugin, with `gh` CLI as the fallback for operations MCP doesn't cover. |
| `skills/gt/SKILL.md` | `/gt` | 🚧 **Reserved slot — not yet implemented.** Will cover Graphite (`gt`) stacked-PR workflows. Frontmatter and directory shape are in place so future work lands without a rename; invoking it today announces the banner and falls back to the `gt` CLI directly. |
| `skills/justfile/SKILL.md` | `/justfile` | Generate or migrate to a justfile, detect the project ecosystem (Rust / Python / TypeScript / Go / Ruby), and write idiomatic recipes with token-optimized output for LLM-driven builds. |
| `skills/prek/SKILL.md` | `/prek` | Onboard [prek](https://prek.j178.dev/) and pick language-appropriate pre-commit hooks. Migrates `.pre-commit-config.yaml` → `prek.toml` when asked. |
| `skills/ralphify-spec/SKILL.md` | `/ralphify-spec` | Generate a ralphify-approved ralph directory (RALPH.md + scripts) from a plain-English description of repetitive or iterative work. Ships an iteration-cap-enforcing runner wrapper, a `<promise>COMPLETE</promise>` stop sentinel, and a burn-down-todos template. |

## Scope

Each skill wraps a single CLI you probably already use:

| Skill | Wraps | Required | Optional |
| --- | --- | --- | --- |
| `commit` | `git` | git | — |
| `gh` | `gh` CLI | gh | GitHub MCP plugin (preferred) |
| `gt` | `gt` (Graphite) | gt (when implemented) | — |
| `justfile` | `just` | just | — |
| `prek` | `prek` | prek | Context7 MCP (for current hook revisions) |
| `ralphify-spec` | [`ralphify`](https://github.com/ghuntley/ralphify) | ralphify (`uv tool install ralphify`), Python 3.10+ | — |

What that means in practice:

- **No orchestration, no intent classification.** Each skill is a single
  focused step the user (or another skill) explicitly invokes.
- **No required MCP servers.** The `gh` skill prefers the GitHub MCP plugin
  but degrades cleanly to the `gh` CLI; `prek` falls back to documented
  hook revisions when Context7 is missing.
- **Composes freely with any other skill set** — install just these, install
  alongside something larger, or pick individual skills.

## Suggested flow

```text
work on a branch
    ├── /commit            ──►  stage + commit (conventional commits)
    ├── /gt                ──►  manage stacked branches (reserved slot — not yet implemented)
    └── /gh                ──►  push + create PR + watch checks

new project setup
    ├── /justfile          ──►  scaffold task runner
    └── /prek              ──►  scaffold pre-commit hooks
```

`/commit`, `/gt`, and `/gh` form a loose pipeline for everyday change flow:
commit locally → arrange in a stack → push and review on GitHub. `/justfile`
and `/prek` are one-shot scaffolding skills you run when bootstrapping a repo.

## Install

### gh skill (recommended)

Requires [GitHub CLI](https://cli.github.com) v2.90.0 or later with the
`gh skill` command.

Install all skills interactively:

```sh
gh skill install paulnsorensen/skillz-that-grillz
```

Install every skill in one shot:

```sh
for s in commit gh gt justfile prek ralphify-spec; do
  gh skill install paulnsorensen/skillz-that-grillz "$s"
done
```

Install a specific skill:

```sh
gh skill install paulnsorensen/skillz-that-grillz commit
```

Pin to a release tag or commit SHA:

```sh
gh skill install paulnsorensen/skillz-that-grillz commit@v1.0.0
gh skill install paulnsorensen/skillz-that-grillz commit@abc123def
```

Pick the agent and scope:

```sh
# User-wide (recommended for personal toolkits)
gh skill install paulnsorensen/skillz-that-grillz --agent claude-code --scope user

# Committed into the current project repo
gh skill install paulnsorensen/skillz-that-grillz --agent claude-code --scope project
```

### Claude Code (manual)

Copy the skills you want into your skills directory:

```sh
# Per-user
mkdir -p ~/.claude/skills
cp -r skills/commit ~/.claude/skills/

# Per-project
mkdir -p .claude/skills
cp -r skills/justfile .claude/skills/
```

### Other harnesses

Copy `skills/<name>/` into wherever the harness loads Agent Skills from. The
format follows the [agentskills.io spec](https://agentskills.io/specification)
and works in any compliant client.

## One-shot installer (macOS)

`scripts/install.sh` does the whole setup in one shot:

1. Installs the CLI tools the skills wrap (`gh`, `just`, `prek`, `graphite`)
   via Homebrew.
2. Auto-detects installed Claude Code, Cursor, and Codex CLIs, then installs
   every skill into each detected harness at user scope.
3. Optionally registers `context7` (used by the prek skill for up-to-date
   hook revisions) with the detected harness.

Currently macOS only — it relies on Homebrew. Requires `gh` to be authenticated
(`gh auth login`) before running.

Pipe straight from GitHub:

```sh
curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh | bash
```

Or grab the script first:

```sh
curl -fsSL -o /tmp/skillz-install.sh https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh
bash /tmp/skillz-install.sh --help
bash /tmp/skillz-install.sh --dry-run
```

Common flags:

```sh
# Just the gh + just CLIs, no MCP registration
curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh \
  | bash -s -- --tools gh,just --skip-mcp

# Register MCP servers only (skills + tools already in place)
curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh \
  | bash -s -- --skip-tools --mcp context7

# Pick a specific harness for skill + MCP registration
curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh \
  | bash -s -- --harness cursor
```

The script is idempotent — it skips any tool already on `PATH` — and accepts
`--dry-run` so you can preview what it would do.

> **Heads-up:** `curl | bash` runs whatever the URL serves at the moment of the
> request. If you want to audit before running, use the two-step form above.

## CLI tools

The skills wrap these CLIs. Install whichever ones you actually use; the
others can wait until you invoke the matching skill.

### GitHub CLI (`gh`)

Used by the `gh` skill, the `commit → gh` handoff, and `gh skill install`.

```sh
brew install gh           # macOS/Linux via Homebrew
winget install GitHub.cli # Windows
# or see https://cli.github.com for other methods
gh auth login
```

Minimum version for `gh skill`: **v2.90.0**.

### `just`

Project task runner. Used by the `justfile` skill.

```sh
brew install just              # macOS/Linux
cargo install just             # Rust/Cargo
winget install Casey.Just      # Windows
```

### `prek`

Rust-powered pre-commit replacement. Used by the `prek` skill.

```sh
cargo install prek             # Rust/Cargo
brew install prek              # macOS/Linux (when the formula is available)
```

See [prek.j178.dev](https://prek.j178.dev/) for the latest install
instructions.

### `gt` (Graphite CLI)

Stacked-PR workflow tool. Used by the `gt` reserved-slot skill.

```sh
brew install withgraphite/tap/graphite   # macOS/Linux via Homebrew tap
npm install -g @withgraphite/graphite-cli # Node.js
```

See [graphite.dev/docs](https://graphite.dev/docs/graphite-cli) for the
latest install instructions and `gt auth` to log in.

## Optional MCP servers

### GitHub MCP plugin (preferred for `/gh`)

The `gh` skill prefers the GitHub MCP plugin (`mcp__plugin_github_github__*`)
over the `gh` CLI because it bypasses sandbox / TLS issues. Install via your
harness's plugin manager — for Claude Code:

```sh
/plugin install github
```

If MCP isn't available, the skill falls back to the `gh` CLI for every
operation that has a CLI equivalent.

### Context7 (optional, used by `/prek`)

[Context7](https://github.com/upstash/context7) fetches up-to-date library
docs into the session. The `prek` skill uses it to pin current revisions of
community hook repos (ruff-pre-commit, shellcheck-py, etc.).

**Claude Code:**

```sh
claude mcp add context7 -- npx -y @upstash/context7-mcp@latest
```

**Other harnesses** — add to your MCP config file:

```json
{
  "mcpServers": {
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp@latest"]
    }
  }
}
```

For higher rate limits, get a free API key at
[context7.com](https://context7.com) and append `--api-key YOUR_API_KEY` to
the `args` array. Requires Node.js v18+.

If Context7 is missing, the `prek` skill falls back to the hook revisions
documented inline.

## Validate

The reference validator from
[`agentskills/agentskills`](https://github.com/agentskills/agentskills) checks
frontmatter and naming:

```sh
skills-ref validate ./skills/commit
```

Each `SKILL.md` must have YAML frontmatter with at least `name` and
`description`, and `name` must match the parent directory name.

The CI pipeline also runs the in-repo validator:

```sh
python3 .github/scripts/validate_skills.py
```

## License

MIT — see [LICENSE](LICENSE).
