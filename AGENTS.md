# Agent Instructions for skillz-that-grillz

This document is for LLMs, agents, and automation tools working in this repository.

## Single Quality Gate: `just check`

**Before shipping any work (commit, PR, or merge), run `just check` and verify 0 errors/failures.**

`just check` autofixes lint and runs tests. CI runs `just ci` (same checks, no autofixes).

```bash
just check
```

Do NOT commit or push when `just check` fails. If CI fails, pull the branch locally, run `just check`, commit the autofixes, and push.

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

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- Keep skills self-contained; avoid external dependencies
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Conventional Commits format for all commits
