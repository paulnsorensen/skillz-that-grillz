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
No agents and no orchestration. There are no _required_ MCP servers — three
skills (`chezmoi`, `prek`, `serena-config`) _optionally_ use Context7 for current
docs, and `chezmoi`/`serena-config` may also use Tavily for web extracts; when
those tools are absent they fall back to bundled guidance and CLI help — just self-contained `SKILL.md` files that any spec-compliant harness can load.

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
| `skills/bash-shortening/SKILL.md` | `/bash-shortening` | Rewrite verbose Bash into idiomatic forms — parameter expansion, brace expansion, process substitution, arithmetic contexts, heredocs, associative arrays, and 45 other techniques. Knows when shortening hurts readability and refuses cryptic one-liners. Methodology + a deterministic rewriter (`scripts/bash-shorten.py`) that requires `ast-grep`; without it, fall back to invoking the skill directly in your harness. |
| `skills/commit/SKILL.md` | `/commit` | Stage and commit changes with conventional-commits messages, no `git add -A`, no `--no-verify`, no amends to published commits. Hand off to `/gh` for push / PR. |
| `skills/chezmoi/SKILL.md` | `/chezmoi` | Manage dotfiles with [chezmoi](https://chezmoi.io/) — file-naming attribute table (`dot_`, `private_`, `encrypted_`, `run_once_`), safe-apply ritual (`status` → `diff` → `dry-run` → `apply`), secrets decision tree (1Password / Bitwarden / age / gpg / SOPS), `.chezmoi.toml.tmpl` bootstrap recipe, and the canonical pitfall list. |
| `skills/copilot/SKILL.md` | `/copilot` | Drive the GitHub Copilot CLI / coding agent. Three modes: `review` (PR review with `@copilot fix this` inline comments) and `delegate` (`gh agent-task` create + monitor) are routine; `setup` is a one-time bootstrap that writes `.github/copilot-instructions.md` and per-language `.github/instructions/*.instructions.md`. |
| `skills/file-handler/SKILL.md` | `/file-handler` | Persist, fetch, and search skill artifacts under a shared `.skillz/<type>/<slug>` tree. Wraps a dependency-free `skillz.sh` exposing `save_file`, `get_file`, and `search_files` (titles + body grep). The on-disk convention every other skill in this repo delegates to for scratch space. |
| `skills/gh/SKILL.md` | `/gh` | All GitHub plumbing — PRs, issues, CI checks, releases, workflow runs, code search, repo and label management — via the `gh` CLI, with idiomatic `--jq` and `--body-file` patterns. |
| `skills/gh-bootstrap/SKILL.md` | `/gh-bootstrap` | One-time configuration of a single GitHub repo via `gh` CLI: enable the merge queue on `main`, lock to squash-only merging with PR-title commits, wire required CI checks, scaffold `.github/release.yml` for auto-generated release notes, and optionally add a tag-driven release workflow. Idempotent. |
| `skills/github-copilot-personal-instructions/SKILL.md` | `/github-copilot-personal-instructions` | Configure or audit per-user GitHub Copilot instructions on github.com (response language, tone, default example language). Doc-faithful walkthrough of the github.com Chat-only surface, precedence vs repo/org instructions, and verification. |
| `skills/github-copilot-repo-instructions/SKILL.md` | `/github-copilot-repo-instructions` | Add or audit `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` so Copilot Chat, code review, and the coding agent pick up project-wide guidance. Covers `applyTo`/`excludeAgent` frontmatter, `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` alternates, surfaces, verification, **and the full `copilot_code_review` ruleset knob inventory** (see `references/code-review-knobs.md`). |
| `skills/pr-stack/SKILL.md` | `/pr-stack` | Stacked-PR workflows across whichever tool is installed: Graphite (`gt`) or GitHub's native `gh stack` extension. Auto-detects which is available, dispatches to the matching per-tool reference, and refuses to fake stacking with plain `git push` chains when neither is present. |
| `skills/justfile/SKILL.md` | `/justfile` | Generate or migrate to a justfile, detect the project ecosystem (Rust / Python / TypeScript / Go / Ruby), and write idiomatic recipes with token-optimized output for LLM-driven builds. |
| `skills/oss-hygiene/SKILL.md` | `/oss-hygiene` | Bring a public repo up to the GitHub Community Standards baseline and the OpenSSF Scorecard supply-chain baseline: scaffold `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, issue + PR templates, `dependabot.yml`, the dependency-review / Scorecard / CodeQL workflows; toggle Dependabot alerts and secret scanning; audit existing workflows for `Token-Permissions` and `Dangerous-Workflow`. Idempotent. |
| `skills/prek/SKILL.md` | `/prek` | Onboard [prek](https://prek.j178.dev/) and pick language-appropriate pre-commit hooks. Migrates `.pre-commit-config.yaml` → `prek.toml` when asked. |
| `skills/ralphify-spec/SKILL.md` | `/ralphify-spec` | Generate a ralphify-approved ralph directory (RALPH.md + scripts) from a plain-English description of repetitive or iterative work. Ships an iteration-cap-enforcing runner wrapper, a `<promise>COMPLETE</promise>` stop sentinel, and a burn-down-todos template. |
| `skills/release/SKILL.md` | `/release` | Cut a versioned release end to end: decide the next semantic version from the Conventional Commits since the last tag (with the `0.x` exception), draft proper release notes (auto-generated via `.github/release.yml`, hand-curated grouped by change type with highlights + upgrade notes, or hybrid), update `CHANGELOG.md`, create and push an annotated tag, and publish the GitHub release. Stops at the tag push when a tag-driven release workflow already publishes. |
| `skills/respond/SKILL.md` | `/respond` | Triage PR review comments by 0–100 confidence score (FIX / ASK / PUSH BACK / SKIP) and act — fixes the high-scoring ones, pushes back on the low, asks about borderline. Checks build + merge state first. Every reply ends with an `agent on behalf of;` attribution line so reviewers know an agent posted on a teammate's behalf. |
| `skills/safe-settings/SKILL.md` | `/safe-settings` | Onboard [`github/safe-settings`](https://github.com/github/safe-settings) for declarative, org-wide repo policy as code. Scaffolds the admin-repo layout (`settings.yml` + `suborgs/` + `repos/`), the GitHub App install steps, and a scheduled `full-sync` GitHub Actions workflow. |
| `skills/serena-config/SKILL.md` | `/serena-config` | Configure the [Serena](https://oraios.github.io/serena/) MCP server across both layers: global `~/.serena/serena_config.yml` (settings, contexts, modes, `ls_specific_settings`) and per-repo `.serena/project.yml` (languages, ignore rules, monorepo `additional_workspace_folders`, `read_only`). Routes to `references/project-config.md` and `references/global-config.md`; covers the create → index → activate lifecycle, the layered override model, and `serena print-system-prompt` verification. |

## Scope

Most skills wrap a single CLI you probably already use. `bash-shortening`
can be used as pure methodology (invoke `/bash-shortening` in any compliant
harness); the bundled `bash-shorten.py` rewriter additionally requires
**ast-grep**.

| Skill | Wraps | Required | Optional |
| --- | --- | --- | --- |
| `bash-shortening` | methodology + `bash-shorten.py` rewriter | bash 4+ in target scripts, **ast-grep** (when running the rewriter) | shellcheck (post-validation), sd / ripgrep / fd (`--include modernize`) |
| `commit` | `git` | git | — |
| `chezmoi` | `chezmoi` CLI | chezmoi | `op` / `bw` / `age` / `gpg` (one of, when using encrypted dotfiles); Context7 MCP (latest template-function docs) |
| `copilot` | `gh` CLI + `gh agent-task` + GitHub Copilot Chat | gh, gh agent-task extension | review skill (e.g. `age` or `code-review`) for `review` mode |
| `file-handler` | `bash` + standard POSIX tools (`find`, `grep`) | bash 4+, `find`, `grep` | — |
| `gh` | `gh` CLI | gh | — |
| `gh-bootstrap` | `gh` CLI (`gh api`) | gh | — |
| `github-copilot-personal-instructions` | github.com Copilot UI | — | — |
| `github-copilot-repo-instructions` | repo files + `gh api repos/.../rulesets` | — | — |
| `pr-stack` | `gt` (Graphite) **or** `gh stack` (GitHub extension) | one of: `gt`, or `gh` v2.0+ with `gh extension install github/gh-stack` | — |
| `justfile` | `just` | just | — |
| `oss-hygiene` | `gh` CLI (`gh api`) + scaffolded GitHub Actions (Dependabot, Scorecard, dependency review, CodeQL) | gh | — |
| `prek` | `prek` | prek | Context7 MCP (for current hook revisions) |
| `ralphify-spec` | [`ralphify`](https://github.com/ghuntley/ralphify) | ralphify (`uv tool install ralphify`), Python 3.10+ | — |
| `release` | `git` + `gh` CLI (`gh release`) | git, gh | `.github/release.yml` (for `--generate-notes` grouping), `CHANGELOG.md` (when the repo keeps one) |
| `respond` | `gh` CLI + `git` | gh, git | — |
| `safe-settings` | `gh` CLI + [`github/safe-settings`](https://github.com/github/safe-settings) GitHub App | gh, Node 20+ on the runner that executes the GHA `full-sync` workflow | — |
| `serena-config` | [Serena](https://oraios.github.io/serena/) MCP `serena` CLI + config files | serena (for `print-system-prompt` / `project` verification) | Context7 MCP (latest config-key docs) |

What that means in practice:

- **No orchestration, no intent classification.** Each skill is a single
  focused step the user (or another skill) explicitly invokes.
- **No required MCP servers.** Only `chezmoi`, `prek`, and `serena-config`
  touch an MCP server at all, and only optionally: each uses Context7 for
  current docs and falls back to the wrapped CLI's own self-docs when it is
  missing (e.g. `prek` uses documented hook revisions).
- **Composes freely with any other skill set** — install just these, install
  alongside something larger, or pick individual skills.

## Suggested flow

```text
work on a branch
    ├── /commit            ──►  stage + commit (conventional commits)
    ├── /pr-stack          ──►  manage stacked branches (gt or gh stack — auto-detects)
    └── /gh                ──►  push + create PR + watch checks

ship a release
    └── /release           ──►  decide semver bump + draft notes + tag + publish

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

### npx skills (recommended)

[`npx skills`](https://skills.sh) is the harness-agnostic installer. It
auto-detects which agents you have installed and works with Claude Code,
Codex, Cursor, opencode, Gemini CLI, GitHub Copilot, Windsurf, and 30+ other
clients. Requires Node.js (for `npx`).

Install interactively — pick agents and skills from a menu:

```sh
npx skills add paulnsorensen/skillz-that-grillz
```

Install every skill into every detected agent, no prompts:

```sh
npx skills add paulnsorensen/skillz-that-grillz --all
```

Install specific skills:

```sh
npx skills add paulnsorensen/skillz-that-grillz --skill commit --skill gh
```

Target specific agents at user scope, non-interactive (CI-friendly):

```sh
npx skills add paulnsorensen/skillz-that-grillz --skill '*' --global --yes \
  --agent claude-code --agent codex
```

List the available skills without installing anything:

```sh
npx skills add paulnsorensen/skillz-that-grillz --list
```

Scope defaults to the current project (`./<agent>/skills/`). Pass `-g` /
`--global` for a user-wide install (`~/<agent>/skills/`), and `--copy` to copy
files instead of symlinking.

### gh skill

Requires [GitHub CLI](https://cli.github.com) v2.90.0 or later with the
`gh skill` command.

Install all skills interactively:

```sh
gh skill install paulnsorensen/skillz-that-grillz
```

Install a specific skill, or pin to a release tag / commit SHA:

```sh
gh skill install paulnsorensen/skillz-that-grillz commit
gh skill install paulnsorensen/skillz-that-grillz commit@v1.0.0
gh skill install paulnsorensen/skillz-that-grillz commit@a1b2c3d
```

Pick the agent and scope (swap `claude-code` for your harness — `codex`,
`cursor`, `copilot`, etc.):

```sh
# User-wide (recommended for personal toolkits)
gh skill install paulnsorensen/skillz-that-grillz --agent claude-code --scope user

# Committed into the current project repo
gh skill install paulnsorensen/skillz-that-grillz --agent codex --scope project
```

### Manual (any harness)

Copy `skills/<name>/` into wherever your harness loads Agent Skills from:

```sh
cp -r skills/commit ~/.claude/skills/            # Claude Code (user)
cp -r skills/commit ~/.codex/skills/             # Codex
cp -r skills/commit ~/.cursor/skills/            # Cursor
cp -r skills/commit ~/.config/opencode/skills/   # opencode (user)
cp -r skills/commit .claude/skills/              # project scope
```

opencode loads skills from `~/.config/opencode/skills/<name>/SKILL.md` at user
scope and `.opencode/skills/<name>/SKILL.md` per project; it also reads the
`.claude/skills/` and `.agents/skills/` paths above as fallbacks, so a single
`.claude/skills/` copy serves both Claude Code and opencode.

The format follows the [agentskills.io spec](https://agentskills.io/specification)
and works in any compliant client.

> **Claude Code frontmatter extensions:** some `SKILL.md` files carry
> Claude-Code-specific frontmatter keys (e.g. `allowed-tools`) alongside the
> spec-required `name` + `description`. These are ignored by harnesses that
> don't recognize them, so the skills still load — but a green
> [validator](#validate) confirms only spec conformance, not that every
> frontmatter key is portable.

## One-shot installer (macOS)

`scripts/install.sh` does the whole setup in one shot:

1. Installs the CLI tools the skills wrap (`gh`, `just`, `prek`, `graphite`)
   via Homebrew.
2. Auto-detects installed Claude Code, Cursor, Codex, and opencode CLIs and
   installs every skill into each via `npx skills` (pass `--harness <name>` to
   target other agents — gemini, copilot, vscode, etc.).
3. Optionally registers the `context7` MCP server (used by the `prek` skill).
   Auto-registration currently covers Claude Code only; for other harnesses it
   prints a manual-config hint (see the Context7 section below).

Currently macOS only — it relies on Homebrew. Skill install goes through
`npx skills`, so Node.js (which provides `npx`) must be available.

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

### Context7 (optional, used by `/prek`)

[Context7](https://github.com/upstash/context7) fetches up-to-date library
docs into the session. The `prek` skill uses it to pin current revisions of
community hook repos (ruff-pre-commit, shellcheck-py, etc.).

Add it to your harness's MCP config file (works for any harness):

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

Claude Code shortcut — register it from the CLI instead:

```sh
claude mcp add context7 -- npx -y @upstash/context7-mcp@latest
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
