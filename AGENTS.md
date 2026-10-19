# Agent Instructions for skillz-that-grillz

This document is for LLMs, agents, and automation tools working in this repository.

## Single Quality Gate: `just build`

This project has ONE canonical verification command. ALWAYS run it after changing
code, and treat a non-zero exit as a hard stop — fix the reported failures before
doing anything else.

- `just build` — autofix markdown and YAML, then verify formats, run root
  basedpyright and Vulture checks, skill validators, fromargs/wedge suites,
  and wedge check. Run after every change.
- `just ci` — the same gate with NO autofixes; this is what CI runs.
  CI sets `GATE_SKIP=skillz-e2e-pytest,skillz-pytest` on the `gate (just ci)` job
  and runs those two lanes in six parallel shard jobs (`GATE_SHARD=<n>/6 just test-skillz`),
  which the required `skillz tests` check gates.
  Locally, `just build` and `just ci` still run every lane.

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
truth for every published skill in this repo — paths, commands, and purpose.
Read it there rather than duplicating it here. `.agents/skills/` is the source
of truth for repo-local skills (tooling for work on this repository only).

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- Keep skills self-contained; avoid external dependencies
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Repo-local skills (for work on this repo only) live in `.agents/skills/<name>/`,
  with a relative symlink in `.claude/skills/`. Set `metadata.internal: true`
  so `npx skills add` does not publish them. Keep them out of the README table.
- Use `/skillz-self-update` to refresh cross-harness research and maintain the published `/skillz` skill. Do not use `/skillz self-update`.
- A ticket's PR adds `docs/agents/criteria/gh-<n>.md` (its Acceptance list, one row per criterion) and marks each test
  `@pytest.mark.ac("gh-<n>:AC-<m>")`. `just build` fails on an untested `test` criterion or an unknown marker;
  see `docs/agents/criteria/index.md`.
- Conventional Commits format for all commits
- Architecture (Sliced Bread):
  - A slice is a subpackage of `packages/skillz/src/skillz_experiments/`. Import it only through its
    `__init__.py` facade (`docs/agents/decisions/harness-module-interface.md` ADR-002).
  - Move a flat root module into a slice when a change puts pressure on it
    (`docs/agents/decisions/skillz-autoimprove.md` ADR-011).
  - `packages/skillz/tests/skillz_experiments/test_layout.py` holds the frozen sets: `ROOT_MODULES`
    and `KERNEL` (the root modules that a slice may import).
    Each entry cites an ADR id as `<decision file stem>:ADR-NNN`.
  - A workspace package does not import a private `_` module of another workspace package.
    `.github/scripts/check_architecture.py` enforces this. Its `ALLOWED` set may only shrink.
  - Write or amend an ADR when a PR adds, removes, or renames a slice or a workspace package,
    adds a `ROOT_MODULES` or `KERNEL` entry, adds a cross-package import edge or an `ALLOWED` entry,
    or adds a second public seam to a slice. A move into an existing slice needs no ADR.
  - Do not add methods to `Session` or `ClaudeCode`. Add a collaborator in the slice.
