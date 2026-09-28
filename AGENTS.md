# Agent Instructions for skillz-that-grillz

This document is for LLMs, agents, and automation tools working in this repository.

## Single Quality Gate: `just build`

This project has ONE canonical verification command. ALWAYS run it after changing
code, and treat a non-zero exit as a hard stop — fix the reported failures before
doing anything else.

- `just build` — autofix (markdown + YAML format), then verify markdown/YAML
  and run the skill validators, fromargs/wedge suites, and wedge check. Run
  after every change.
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

This repo publishes no Agent Skills — see the README's "Where the skills
went" section. `.agents/skills/` is the source of truth for repo-local
skills (tooling for work on this repository only).

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Repo-local skills (for work on this repo only) live in `.agents/skills/<name>/`,
  with a relative symlink in `.claude/skills/`. Set `metadata.internal: true`
  so `npx skills add` does not publish them. Keep them out of the README table.
- Conventional Commits format for all commits
