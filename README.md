# 🧀 skillz-that-grillz 🧀

[![CI](https://img.shields.io/github/actions/workflow/status/paulnsorensen/skillz-that-grillz/validate.yml?branch=main&label=CI&style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/paulnsorensen/skillz-that-grillz?style=flat-square)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/paulnsorensen/skillz-that-grillz?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/releases/latest)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?style=flat-square)](https://www.conventionalcommits.org)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-spec-blueviolet?style=flat-square)](https://agentskills.io/specification)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/pulls)

> _Tight little toolbelt of git, GitHub, project-runner, and shell-craft skills._

A focused, skills-only repository of [Agent Skills](https://agentskills.io/specification)
for the everyday plumbing around a project: making a clean commit, working a
GitHub PR, stacking branches with Graphite or `gh stack`, scaffolding a
justfile, wiring up prek pre-commit hooks, and writing concise idiomatic Bash.
No agents, no orchestration, no MCP requirements — just self-contained
`SKILL.md` files that any spec-compliant harness can load.

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
| `skills/chezmoi/SKILL.md` | `/chezmoi` | Manage dotfiles with [chezmoi](https://chezmoi.io/) — file-naming attribute table (`dot_`, `private_`, `encrypted_`, `run_once_`), safe-apply ritual (`status` → `diff` → `dry-run` → `apply`), secrets decision tree (1Password / Bitwarden / age / gpg / SOPS), `.chezmoi.toml.tmpl` bootstrap recipe, and the canonical pitfall list. |
| `skills/gh/SKILL.md` | `/gh` | All GitHub plumbing — PRs, issues, CI checks, releases, code search — via the GitHub MCP plugin, with `gh` CLI as the fallback for operations MCP doesn't cover. |
| `skills/gh-bootstrap/SKILL.md` | `/gh-bootstrap` | One-time configuration of a single GitHub repo via `gh` CLI: enable the merge queue on `main`, lock to squash-only merging with PR-title commits, wire required CI checks, scaffold `.github/release.yml` for auto-generated release notes, and optionally add a tag-driven release workflow. Idempotent. |
| `skills/github-copilot-personal-instructions/SKILL.md` | `/github-copilot-personal-instructions` | Configure or audit per-user GitHub Copilot instructions on github.com (response language, tone, default example language). Doc-faithful walkthrough of the github.com Chat-only surface, precedence vs repo/org instructions, and verification. |
| `skills/github-copilot-repo-instructions/SKILL.md` | `/github-copilot-repo-instructions` | Add or audit `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` so Copilot Chat, code review, and the coding agent pick up project-wide guidance. Covers `applyTo`/`excludeAgent` frontmatter, `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` alternates, surfaces, verification, **and the full `copilot_code_review` ruleset knob inventory** (see `references/code-review-knobs.md`). |
| `skills/pr-stack/SKILL.md` | `/pr-stack` | Stacked-PR workflows across whichever tool is installed: Graphite (`gt`) or GitHub's native `gh stack` extension. Auto-detects which is available, dispatches to the matching per-tool reference, and refuses to fake stacking with plain `git push` chains when neither is present. |
| `skills/justfile/SKILL.md` | `/justfile` | Generate or migrate to a justfile, detect the project ecosystem (Rust / Python / TypeScript / Go / Ruby), and write idiomatic recipes with token-optimized output for LLM-driven builds. |
| `skills/oss-hygiene/SKILL.md` | `/oss-hygiene` | Bring a public repo up to the GitHub Community Standards baseline and the OpenSSF Scorecard supply-chain baseline: scaffold `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, issue + PR templates, `dependabot.yml`, the dependency-review / Scorecard / CodeQL workflows; toggle Dependabot alerts and secret scanning; audit existing workflows for `Token-Permissions` and `Dangerous-Workflow`. Idempotent. |
| `skills/prek/SKILL.md` | `/prek` | Onboard [prek](https://prek.j178.dev/) and pick language-appropriate pre-commit hooks. Migrates `.pre-commit-config.yaml` → `prek.toml` when asked. |
| `skills/ralphify-spec/SKILL.md` | `/ralphify-spec` | Generate a ralphify-approved ralph directory (RALPH.md + scripts) from a plain-English description of repetitive or iterative work. Ships an iteration-cap-enforcing runner wrapper, a `<promise>COMPLETE</promise>` stop sentinel, and a burn-down-todos template. |
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
| `chezmoi` | `chezmoi` CLI | chezmoi | `op` / `bw` / `age` / `gpg` (one of, when using encrypted dotfiles); Context7 MCP (latest template-function docs) |
| `gh` | `gh` CLI | gh | GitHub MCP plugin (preferred) |
| `gh-bootstrap` | `gh` CLI (`gh api`) | gh | — |
| `github-copilot-personal-instructions` | github.com Copilot UI | — | — |
| `github-copilot-repo-instructions` | repo files + `gh api repos/.../rulesets` | — | — |
| `pr-stack` | `gt` (Graphite) **or** `gh stack` (GitHub extension) | one of: `gt`, or `gh` v2.0+ with `gh extension install github/gh-stack` | — |
| `justfile` | `just` | just | — |
| `oss-hygiene` | `gh` CLI (`gh api`) + scaffolded GitHub Actions (Dependabot, Scorecard, dependency review, CodeQL) | gh | — |
| `prek` | `prek` | prek | Context7 MCP (for current hook revisions) |
| `ralphify-spec` | [`ralphify`](https://github.com/ghuntley/ralphify) | ralphify (`uv tool install ralphify`), Python 3.10+ | — |
| `safe-settings` | `gh` CLI + [`github/safe-settings`](https://github.com/github/safe-settings) GitHub App | gh, Node 20+ on the runner that executes the GHA `full-sync` workflow | — |

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
    ├── /pr-stack          ──►  manage stacked branches (gt or gh stack — auto-detects)
    └── /gh                ──►  push + create PR + watch checks

new project setup
    ├── /justfile          ──►  scaffold task runner
    ├── /prek              ──►  scaffold pre-commit hooks
    ├── /gh-bootstrap      ──►  configure merge queue + squash-only + release notes on a single repo
    └── /oss-hygiene       ──►  community files + supply-chain workflows + Scorecard / OSSF Badge

org-wide policy as code
    └── /safe-settings     ──►  scaffold admin repo + GitHub App for declarative settings across many repos
```

`/commit`, `/pr-stack`, and `/gh` form a loose pipeline for everyday change
flow: commit locally → arrange in a stack → push and review on GitHub.
`/justfile` and `/prek` are one-shot scaffolding skills you run when
bootstrapping a repo.

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
for s in bash-shortening commit gh gh-bootstrap github-copilot-personal-instructions github-copilot-repo-instructions justfile oss-hygiene pr-stack prek ralphify-spec safe-settings; do
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

### Stacked-PR tooling (one of)

Used by the `pr-stack` skill. Install **one** — the skill auto-detects which
is available and dispatches to the matching reference.

**Graphite (`gt`)** — third-party, generally available:

```sh
brew install withgraphite/tap/graphite   # macOS/Linux via Homebrew tap
npm install -g @withgraphite/graphite-cli # Node.js (incl. Windows)
gt auth --token <token>                  # from https://app.graphite.com/activate
gt init                                  # once per repo
```

See [graphite.dev/docs](https://graphite.dev/docs/graphite-cli).

**GitHub `gh stack`** — first-party, private preview as of 2026-05:

```sh
gh extension install github/gh-stack     # requires gh v2.0+
# join the waitlist at https://gh.io/stacksbeta and have your repo allow-listed
```

See [github.github.com/gh-stack](https://github.github.com/gh-stack/).

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
