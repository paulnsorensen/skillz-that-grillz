# 🧀 skillz-that-grillz 🧀

[![CI](https://img.shields.io/github/actions/workflow/status/paulnsorensen/skillz-that-grillz/validate.yml?branch=main&label=CI&style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/github/license/paulnsorensen/skillz-that-grillz?style=flat-square)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/paulnsorensen/skillz-that-grillz?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/releases/latest)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow?style=flat-square)](https://www.conventionalcommits.org)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-spec-blueviolet?style=flat-square)](https://agentskills.io/specification)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](https://github.com/paulnsorensen/skillz-that-grillz/pulls)

> _Tight little toolbelt of git, GitHub, project-runner, and shell-craft skills._

A focused, skills-only repository of [Agent Skills](https://agentskills.io/specification)
for the everyday plumbing around a project: working a GitHub PR and
triaging review comments.
No agents and no orchestration. There are no required MCP servers — just
self-contained `SKILL.md` files that any spec-compliant harness can load.

Repository setup and maintenance skills (`release`, `justfile`, `prek`,
`oss-hygiene`, `safe-settings`, `github-copilot-repo-instructions`) now live in
[git-gouda](https://github.com/paulnsorensen/git-gouda).

The companion repo [easy-cheese](https://github.com/paulnsorensen/easy-cheese)
covers the design / implement / review workflow (mold, cook, press, age, cure)
and local-to-review publication (`/plate`: staging, commits, pushes, and PR
creation — single or stacked). This repo covers the surrounding mechanics.

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
| `skills/file-handler/SKILL.md` | `/file-handler` | Persist, fetch, and search skill artifacts under a shared `.skillz/<type>/<slug>` tree. Wraps a dependency-free `skillz.sh` exposing `save_file`, `get_file`, and `search_files` (titles + body grep). The on-disk convention every other skill in this repo delegates to for scratch space. |
| `skills/gh/SKILL.md` | `/gh` | All GitHub plumbing — PR inspection / review / merge, issues, CI checks, releases, workflow runs, code search, repo and label management — via the `gh` CLI, with idiomatic `--jq` and `--body-file` patterns. Committing, pushing, and PR creation live in easy-cheese's `/plate`. |
| `skills/github-copilot-personal-instructions/SKILL.md` | `/github-copilot-personal-instructions` | Configure or audit per-user GitHub Copilot instructions on github.com (response language, tone, default example language). Doc-faithful walkthrough of the github.com Chat-only surface, precedence vs repo/org instructions, and verification. |
| `skills/respond/SKILL.md` | `/respond` | Triage PR review comments by 0–100 confidence score (FIX / ASK / PUSH BACK / SKIP) and act — fixes the high-scoring ones, pushes back on the low, asks about borderline. Checks build + merge state first. Every reply ends with an `agent on behalf of;` attribution line so reviewers know an agent posted on a teammate's behalf. |

## Scope

Most skills wrap a single CLI you probably already use.

| Skill | Wraps | Required | Optional |
| --- | --- | --- | --- |
| `file-handler` | `bash` + standard POSIX tools (`find`, `grep`) | bash 4+, `find`, `grep` | — |
| `gh` | `gh` CLI | gh | — |
| `github-copilot-personal-instructions` | github.com Copilot UI | — | — |
| `respond` | `gh` CLI + `git` | gh, git | — |

What that means in practice:

- **No orchestration, no intent classification.** Each skill is a single
  focused step the user (or another skill) explicitly invokes.
- **No required MCP servers.** No skill in this repo touches an MCP server.
- **Composes freely with any other skill set** — install just these, install
  alongside something larger, or pick individual skills.

## Suggested flow

```text
work on a branch
    ├── /plate (easy-cheese) ──►  stage + commit + push + create PR (single or stacked)
    └── /gh                  ──►  watch checks + review + merge

review comments
    └── /respond            ──►  triage PR review comments and act on them
```

`/gh` pairs with easy-cheese's `/plate` for everyday change flow: `/plate`
commits, pushes, and opens the PR (single or stacked); `/gh` watches checks,
reviews, and merges.

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
npx skills add paulnsorensen/skillz-that-grillz --skill gh --skill respond
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
gh skill install paulnsorensen/skillz-that-grillz gh
gh skill install paulnsorensen/skillz-that-grillz gh@v1.0.0
gh skill install paulnsorensen/skillz-that-grillz gh@a1b2c3d
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
cp -r skills/gh ~/.claude/skills/            # Claude Code (user)
cp -r skills/gh ~/.codex/skills/             # Codex
cp -r skills/gh ~/.cursor/skills/            # Cursor
cp -r skills/gh ~/.config/opencode/skills/   # opencode (user)
cp -r skills/gh .claude/skills/              # project scope
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

1. Installs the CLI tools the skills wrap (`gh`) via Homebrew.
2. Auto-detects installed Claude Code, Cursor, Codex, and opencode CLIs and
   installs every skill into each via `npx skills` (pass `--harness <name>` to
   target other agents — gemini, copilot, vscode, etc.).
3. Optionally registers the `context7` MCP server. No skill in this repo
   requires it. Auto-registration currently covers Claude Code only; for
   other harnesses it prints a manual-config hint (see the Context7 section
   below).

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
# Just the gh CLI, no MCP registration
curl -fsSL https://raw.githubusercontent.com/paulnsorensen/skillz-that-grillz/main/scripts/install.sh \
  | bash -s -- --tools gh --skip-mcp

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

Used by the `gh` skill and `gh skill install`.

```sh
brew install gh           # macOS/Linux via Homebrew
winget install GitHub.cli # Windows
# or see https://cli.github.com for other methods
gh auth login
```

Minimum version for `gh skill`: **v2.90.0**.

## wedge GitHub Action

[`actions/wedge`](actions/wedge/README.md) packages a pure-Python skill CLI
as a content-addressed `.pyz` release asset in your own repository. Use it to
check locks on pull requests and to publish assets after a merge:

```yaml
- uses: paulnsorensen/skillz-that-grillz/actions/wedge@<full-commit-sha>
  with:
    command: publish # or: check
    roots: skills
```

## Optional MCP servers

### Context7 (optional)

[Context7](https://github.com/upstash/context7) fetches up-to-date library
docs into the session. No skill in this repo requires it; `scripts/install.sh`
registers it by default so it is available if you add skills that use it.

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

## Validate

The reference validator from
[`agentskills/agentskills`](https://github.com/agentskills/agentskills) checks
frontmatter and naming:

```sh
skills-ref validate ./skills/gh
```

Each `SKILL.md` must have YAML frontmatter with at least `name` and
`description`, and `name` must match the parent directory name.

The CI pipeline also runs the in-repo validator:

```sh
python3 .github/scripts/validate_skills.py
```

## License

MIT — see [LICENSE](LICENSE).
