# Agent Instructions for skillz-that-grillz

This document is for LLMs, agents, and automation tools working in this repository.

## Single Quality Gate: `just check`

**Before shipping any work (commit, PR, or merge), run `just check` and verify 0 errors/failures.**

This is the authoritative local gate. It mirrors all CI checks:

- Markdown linting (with autofix) — markdownlint-cli2
- YAML formatting (with autofix) — yamlfmt
- YAML linting — yamllint
- Shell linting — shellcheck
- Python linting — uv ruff
- Python validators — `.github/scripts/validate_skills.py`
- Python tests — `.github/scripts/test_validate_skills.py`
- Bash unit tests — bats

### Run it

```bash
just check
```

If it passes locally, the PR will pass CI. If it fails locally, fix the issues and re-run. Do NOT commit or push when `just check` fails.

### Autofix behavior

`just check` automatically fixes markdown and Python issues where possible:

- `markdownlint-cli2 --fix` — reformats markdown
- `yamlfmt` — reformats YAML (preserves blank lines via `.yamlfmt` config)
- Linting failures that cannot autofix must be corrected manually

Shell and Python issues must be fixed manually.

## Workflow

1. **Do the work** — edit skills, scripts, markdown, etc.
2. **Run `just check`** — autofix where possible, verify all checks pass
3. **Commit & push** — only if `just check` reports 0 failures
4. **Open PR** — CI will re-verify using the same gate

## CI Mode

The CI workflow (`validate.yml`) runs `just ci`, which is identical to `just check` except:

- No autofixes (`markdownlint-cli2` and `uv ruff format` run in verify-only mode)
- All checks must pass without intervention

If CI fails, pull the branch locally, run `just check` to autofix, commit, and push.

## Skills in this repo

| Skill | Purpose |
|---|---|
| `/commit` | Stage and commit changes with conventional-commits messages |
| `/gh` | GitHub plumbing (PRs, issues, CI, releases) |
| `/gh-bootstrap` | One-shot repo configuration (merge queue, squash-only, release notes) |
| `/justfile` | Generate or migrate to justfiles |
| `/prek` | Onboard prek and language-appropriate pre-commit hooks |
| `/safe-settings` | Org-scale GitHub policy as code via safe-settings Probot |

See `README.md` for full details.

## Language ecosystem

This repo contains:

- **Markdown** — skill documentation
- **YAML** — GitHub Actions workflows, config
- **Bash** — install.sh and bats tests
- **Python** — `.github/scripts/` validators

There's no compiled code or build artifacts — just data, documentation, and test harnesses.

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- Keep skills self-contained; avoid external dependencies
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Conventional Commits format for all commits
