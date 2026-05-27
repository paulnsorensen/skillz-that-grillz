# Agent Instructions for skillz-that-grillz

This document is for LLMs, agents, and automation tools working in this repository.

## Single Quality Gate: `just build`

This project has ONE canonical verification command. ALWAYS run it after changing
code, and treat a non-zero exit as a hard stop — fix the reported failures before
doing anything else.

- `just build` — autofix (markdown + YAML format), then verify markdown/YAML/shell
  and run the skill validators + bats suites. Run after every change.
- `just ci` — the same gate with NO autofixes; this is what CI runs.

```bash
just build
```

Output is compacted: each step prints `✓ <step>` on success and the full tool
output (with file:line) only on failure, then aborts. Don't invent ad-hoc
lint/test commands — run `just build` so your feedback loop matches CI.

Do NOT commit or push when `just build` fails. If CI fails, pull the branch
locally, run `just build`, commit the autofixes, and push.

## Skills in this repo

| Skill | Purpose |
|---|---|
| `/commit` | Stage and commit changes with conventional-commits messages |
| `/chezmoi` | Manage dotfiles with chezmoi (file naming, templating, secrets, bootstrap) |
| `/file-handler` | Save / fetch / search skill artifacts under `.skillz/<type>/<slug>` — shared on-disk convention for every skill that needs scratch space |
| `/gh` | GitHub plumbing (PRs, issues, CI, releases) |
| `/gh-bootstrap` | One-shot repo configuration (merge queue, squash-only, release notes) |
| `/justfile` | Generate or migrate to justfiles |
| `/oss-hygiene` | Bring a public repo up to GitHub Community Standards + OpenSSF Scorecard baseline |
| `/prek` | Onboard prek and language-appropriate pre-commit hooks |
| `/safe-settings` | Org-scale GitHub policy as code via safe-settings Probot |
| `/serena-config` | Configure the Serena MCP server — global `serena_config.yml` (settings, contexts, modes) and per-repo `project.yml` (languages, ignore rules, monorepo workspace folders) |

See `README.md` for full details.

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- Keep skills self-contained; avoid external dependencies
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Conventional Commits format for all commits
