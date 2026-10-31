# Agent Instructions for skillz-that-grillz

This document is for LLMs, agents, and automation tools working in this repository.

## Single Quality Gate: `just build`

This project has ONE canonical verification command. ALWAYS run it after changing
code, and treat a non-zero exit as a hard stop — fix the reported failures before
doing anything else.

- `just build` — autofix markdown and YAML, then verify formats, run root
  basedpyright and Vulture checks, skill validators, fromargs/wedge suites,
  the file-size ratchet, and wedge check. A final `coverage` lane combines the
  pytest lane coverage and checks the project floor, whole-symbol dead code, and
  diff coverage. Run after every change.
- `just ci` — the same gate with NO autofixes; this is what CI runs.
  CI sets `GATE_SKIP=skillz-e2e-pytest,skillz-pytest` on the `gate (just ci)` job
  and runs those two lanes in six parallel shard jobs (`GATE_SHARD=<n>/6 just test-skillz`),
  which the required `skillz tests` check gates. The `coverage` job combines the
  coverage data of the gate job and the shards (`GATE_COVERAGE_DIR`).
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

## Branch targets for bots and jobs

- `main` is the source branch. `dist` is the default installation branch, not a development branch.
- Set every Dependabot version-update entry to `target-branch: main`.
  Set `commit-message: {prefix: chore, include: scope}` so PR titles pass the title check.
- Dependabot security updates always use the default branch. `target-branch` does not redirect them.
  Recreate their dependency changes on a branch from `main`; do not merge a branch cut from `dist`.
- Keep CodeRabbit `reviews.auto_review.base_branches: ["^main$"]`.
  This adds `main` to its default-branch review scope; it does not remove `dist`.
- Keep Copilot auto-review and source protection rules on `refs/heads/main`, not `~DEFAULT_BRANCH`.
- Run source validation on PR merge commits and pushes to `main`. Review dependencies on PRs targeting `main`.
  PR labeling uses the event payload and does not check out source.
- Keep release tag checks against `origin/main`. Check out the tag, not the default branch, for release builds.
- `publish dist` checks out `main` explicitly, including scheduled and manual runs. Only this publisher writes `dist`.
- Scorecard intentionally scans `dist`: published results require the default branch.
- The retarget job handles PRs targeting `dist` without running PR code.
  Retargeting does not repair unrelated history or remove built archives from a branch cut from `dist`.
- Audit branch targets when adding bots, schedules, manual jobs, or release jobs.
  Do not replace event merge refs or release tags with `main` in validation or release checkouts.

## Development notes

- Use `uv` for Python: `uv run <script>`, `uv pip install <pkg>`
- Keep skills self-contained; avoid external dependencies
- SKILL.md files must pass `validate_skills.py` (YAML frontmatter validation)
- Repo-local skills (for work on this repo only) live in `.agents/skills/<name>/`,
  with a relative symlink in `.claude/skills/`. Set `metadata.internal: true`
  so `npx skills add` does not publish them. Keep them out of the README table.
- `skills/*/scripts/*.pyz` archives are build output, and Git ignores them. `just build` writes them before the tests run.
  The `publish dist` workflow appends `main` plus the archives to `dist`, the default branch
  (`docs/agents/decisions/wedge-skill-packaging.md` ADR-012). Branch from `main`, never from `dist`.
  Target `main`: pass `--base main` to every `gh pr create`. `dist` shares no history with `main`,
  so a PR without `--base` targets the default branch and GitHub refuses it.
- Use `/skillz-self-update` to refresh cross-harness research and maintain the published `/skillz` skill. Do not use `/skillz self-update`.
- A ticket's PR adds `docs/agents/criteria/gh-<n>.md` (its Acceptance list, one row per criterion) and marks each test
  `@pytest.mark.ac("gh-<n>:AC-<m>")`. `just build` fails on an untested `test` criterion or an unknown marker;
  see `docs/agents/criteria/index.md`.
- Conventional Commits format for all commits
- Architecture (Sliced Bread):
  - A slice is a subpackage of `packages/skillz/src/skillz_experiments/`. Import it only through its
    `__init__.py` facade (`docs/agents/decisions/skillz-autoimprove.md` ADR-011).
    A test may import slice internals.
  - Move a flat root module into a slice when a change puts pressure on it
    (`docs/agents/decisions/skillz-autoimprove.md` ADR-011).
  - `packages/skillz/tests/skillz_experiments/test_layout.py` holds the frozen sets: `ROOT_MODULES`
    and `KERNEL` (the root modules that a slice may import).
    Each entry cites an ADR id as `<decision file stem>:ADR-NNN`.
  - The `src/` code of a workspace package does not use a private `_` module of another workspace package.
    `.github/scripts/check_architecture.py` enforces this. Its `ALLOWED` set may only shrink.
  - Write or amend an ADR when a PR adds, removes, or renames a slice or a workspace package,
    adds a `ROOT_MODULES` or `KERNEL` entry, adds a cross-package import edge or an `ALLOWED` entry,
    or adds a second public seam to a slice. A move into an existing slice needs no ADR.
  - Do not add methods to `Session` or `ClaudeCode`. Add a collaborator in the slice.
  - `slices.toml` maps every `packages/*/src` file to one slice or spine node. A human owns it.
    `.github/scripts/check_slices.py` (the slice guard) enforces it; `sbguard-baseline.json` only shrinks.
    A new module must go into a mapped path, or a human adds the path to `slices.toml`.
  - `.github/scripts/check_depth.py` (the depth ratchet) caps six depth keys per slice in `depth-ratchet.json` (block mode).
    When `check` reports `improved`, run `check_depth.py tighten` in the same change.
    When it reports `regressed`, move the logic behind the crust, or ask the human owner to raise the cap.
  - Never edit `slices.toml`, `sbguard-baseline.json`, or `depth-ratchet.json` to make a check pass.
    Do not pass `--accept-denominator-gains`; that decision belongs to the human owner.
    See `docs/agents/decisions/architecture-gates.md`.
- Quality ratchets (`docs/agents/decisions/quality-ratchets.md`):
  - `.github/scripts/check_file_lengths.py` holds each `packages/*/src` file to 300 lines;
    `[tool.skillz.file-size.exceptions]` in `pyproject.toml` lists the measured ceilings.
    When a waived file shrinks, run `check_file_lengths.py tighten` in the same change. Split a file that grows; only a human raises a ceiling.
  - The `coverage` lane fails below `[tool.coverage.report].fail_under`, on a function with zero coverage hits,
    and when the lines a change adds are below `[tool.skillz.coverage].diff_fail_under`.
    Add tests; never lower a floor. Raise `fail_under` when the gate asks for it.
