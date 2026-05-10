# 🧀 skillz-that-grillz 🧀

[![CI](https://img.shields.io/github/actions/workflow/status/paulnsorensen/skillz-that-grillz/validate.yml?branch=main&label=CI&style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/paulnsorensen/skillz-that-grillz?style=flat-square)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/paulnsorensen/skillz-that-grillz?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/releases/latest)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?style=flat-square)](https://www.conventionalcommits.org)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-spec-blueviolet?style=flat-square)](https://agentskills.io/specification)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/pulls)

> _Tight little toolbelt of git, GitHub, project-runner, docs-research, and shell-craft skills._

A focused, skills-only repository of [Agent Skills](https://agentskills.io/specification)
for the everyday plumbing around a project: making a clean commit, working a
GitHub PR, stacking branches with Graphite, scaffolding a justfile, grounding
plans in current library docs, wiring up prek pre-commit hooks, and writing
concise idiomatic Bash. No agents, no
orchestration, no MCP requirements — just self-contained `SKILL.md` files that
any spec-compliant harness can load.

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
| `skills/bash-shortening/SKILL.md` | `/bash-shortening` | Rewrite verbose Bash into idiomatic forms — parameter expansion, brace expansion, process substitution, arithmetic contexts, heredocs, associative arrays, and 45 other techniques. Knows when shortening hurts readability and refuses cryptic one-liners. Methodology + a deterministic rewriter (`scripts/bash-shorten.py`) that requires `ast-grep`; without it, fall back to invoking the skill directly in Claude. |
| `skills/commit/SKILL.md` | `/commit` | Stage and commit changes with conventional-commits messages, no `git add -A`, no `--no-verify`, no amends to published commits. Hand off to `/gh` for push / PR. |
| `skills/doc-grounder/SKILL.md` | `/doc-grounder` | Ground a planning phase in current library docs with Context7 library lookup plus Tavily search/extract of official docs, API references, changelogs, examples, and best practices. Produces a cited docs brief; does not implement. |
| `skills/chezmoi/SKILL.md` | `/chezmoi` | Manage dotfiles with [chezmoi](https://chezmoi.io/) — file-naming attribute table (`dot_`, `private_`, `encrypted_`, `run_once_`), safe-apply ritual (`status` → `diff` → `dry-run` → `apply`), secrets decision tree (1Password / Bitwarden / age / gpg / SOPS), `.chezmoi.toml.tmpl` bootstrap recipe, and the canonical pitfall list. |
| `skills/gh/SKILL.md` | `/gh` | All GitHub plumbing — PRs, issues, CI checks, releases, code search — via the GitHub MCP plugin, with `gh` CLI as the fallback for operations MCP doesn't cover. |
| `skills/gh-bootstrap/SKILL.md` | `/gh-bootstrap` | One-time configuration of a single GitHub repo via `gh` CLI: enable the merge queue on `main`, lock to squash-only merging with PR-title commits, wire required CI checks, scaffold `.github/release.yml` for auto-generated release notes, and optionally add a tag-driven release workflow. Idempotent. |
| `skills/gt/SKILL.md` | `/gt` | 🚧 **Reserved slot — not yet implemented.** Will cover Graphite (`gt`) stacked-PR workflows. Frontmatter and directory shape are in place so future work lands without a rename; invoking it today announces the banner and falls back to the `gt` CLI directly. |
| `skills/justfile/SKILL.md` | `/justfile` | Generate or migrate to a justfile, detect the project ecosystem (Rust / Python / TypeScript / Go / Ruby), and write idiomatic recipes with token-optimized output for LLM-driven builds. |
| `skills/oss-hygiene/SKILL.md` | `/oss-hygiene` | Bring a public repo up to the GitHub Community Standards baseline and the OpenSSF Scorecard supply-chain baseline: scaffold `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, issue + PR templates, `dependabot.yml`, the dependency-review / Scorecard / CodeQL workflows; toggle Dependabot alerts and secret scanning; audit existing workflows for `Token-Permissions` and `Dangerous-Workflow`. Idempotent. |
| `skills/prek/SKILL.md` | `/prek` | Onboard [prek](https://prek.j178.dev/) and pick language-appropriate pre-commit hooks. Migrates `.pre-commit-config.yaml` → `prek.toml` when asked. |
| `skills/safe-settings/SKILL.md` | `/safe-settings` | Onboard [`github/safe-settings`](https://github.com/github/safe-settings) for declarative, org-wide repo policy as code. Scaffolds the admin-repo layout (`settings.yml` + `suborgs/` + `repos/`), the GitHub App install steps, and a scheduled `full-sync` GitHub Actions workflow. |

## Scope

Most skills wrap a single CLI you probably already use. `bash-shortening`
can be used as pure methodology (invoke `/bash-shortening` in Claude
Code); the bundled `bash-shorten.py` rewriter additionally requires
**ast-grep**.

| Skill | Wraps | Required | Optional |
| --- | --- | --- | --- |
| `bash-shortening` | methodology + `bash-shorten.py` rewriter | bash 4+ in target scripts, **ast-grep** (when running the rewriter) | shellcheck (post-validation), sd / ripgrep / fd (`--include modernize`) |
| `commit` | `git` | git | — |
| `doc-grounder` | Context7 + Tavily MCP-assisted docs research | — | Context7 MCP, Tavily MCP, WebSearch fallback |
| `chezmoi` | `chezmoi` CLI | chezmoi | `op` / `bw` / `age` / `gpg` (one of, when using encrypted dotfiles); Context7 MCP (latest template-function docs) |
| `gh` | `gh` CLI | gh | GitHub MCP plugin (preferred) |
| `gh-bootstrap` | `gh` CLI (`gh api`) | gh | — |
| `gt` | `gt` (Graphite) | gt (when implemented) | — |
| `justfile` | `just` | just | — |
| `oss-hygiene` | `gh` CLI (`gh api`) + scaffolded GitHub Actions (Dependabot, Scorecard, dependency review, CodeQL) | gh | — |
| `prek` | `prek` | prek | Context7 MCP (for current hook revisions) |
| `safe-settings` | `gh` CLI + [`github/safe-settings`](https://github.com/github/safe-settings) GitHub App | gh, Node 20+ on the runner that executes the GHA `full-sync` workflow | — |

What that means in practice:

- **No orchestration, no intent classification.** Each skill is a single
  focused step the user (or another skill) explicitly invokes.
- **No required MCP servers.** The `gh` skill prefers the GitHub MCP plugin
  but degrades cleanly to the `gh` CLI; `doc-grounder` uses MCPs when present
  and falls back to narrower web research; `prek` falls back to documented hook
  revisions when Context7 is missing.
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
    ├── /prek              ──►  scaffold pre-commit hooks
    ├── /gh-bootstrap      ──►  configure merge queue + squash-only + release notes on a single repo
    └── /oss-hygiene       ──►  community files + supply-chain workflows + Scorecard / OSSF Badge

org-wide policy as code
    └── /safe-settings     ──►  scaffold admin repo + GitHub App for declarative settings across many repos
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
for s in bash-shortening commit doc-grounder gh gh-bootstrap gt justfile oss-hygiene prek safe-settings; do
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

### Context7 + Tavily (optional, used by `/doc-grounder`)

The `doc-grounder` skill combines Context7 with Tavily to produce current,
cited docs briefs before planning library-dependent work. Context7 supplies
structured library docs; Tavily search/extract verifies official docs, release
notes, API references, examples, and best-practice pages.

Install Context7 as below, then add Tavily using your harness's MCP
configuration with a `TAVILY_API_KEY` from Tavily. If either MCP is missing,
`doc-grounder` should say what is unavailable and fall back to narrower web
research where possible.

### GitHub MCP plugin (preferred for `/gh`)

The `gh` skill prefers the GitHub MCP plugin (`mcp__plugin_github_github__*`)
over the `gh` CLI because it bypasses sandbox / TLS issues. Install via your
harness's plugin manager — for Claude Code:

```sh
/plugin install github
```

If MCP isn't available, the skill falls back to the `gh` CLI for every
operation that has a CLI equivalent.

### Context7 (optional, used by `/doc-grounder` and `/prek`)

[Context7](https://github.com/upstash/context7) fetches up-to-date library
docs into the session. The `doc-grounder` skill uses it as the structured docs
source for planning briefs; the `prek` skill uses it to pin current revisions
of community hook repos (ruff-pre-commit, shellcheck-py, etc.).

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

If Context7 is missing, `doc-grounder` falls back to Tavily/WebSearch with a
freshness warning, and `prek` falls back to the hook revisions documented inline.

### Tavily (optional, used by `/doc-grounder`)

[Tavily](https://www.tavily.com/) provides web search and `tavily-extract` for
official docs pages. `doc-grounder` uses it to verify Context7 results against
release notes, changelogs, API references, examples, and best-practice guides.

Configure Tavily in your harness's MCP settings and provide `TAVILY_API_KEY` as
an environment variable or secret. Prefer extracting selected official URLs over
crawling an entire docs site unless you explicitly need a local docs corpus.

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
