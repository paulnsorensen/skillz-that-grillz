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

The [`## Skills` table in `README.md`](README.md#skills) is the single source of
truth for every skill in this repo — paths, commands, and purpose. Read it
there rather than duplicating it here.

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- Keep skills self-contained; avoid external dependencies
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Conventional Commits format for all commits
