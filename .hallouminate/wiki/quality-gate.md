# Quality gate

## Why one gate

The repo deliberately exposes a *single* canonical verification command so an agent's
feedback loop matches CI exactly. `AGENTS.md` names it and warns against inventing
ad-hoc lint/test commands — the point is that a green local run means a green CI run.

- `just build` — autofix pass (markdown + YAML format) then verify markdown / YAML /
  shell and run the validators + Bats suites. Run after every change.
- `just ci` — the identical checks with **no** autofix; this is what CI runs.

Both dispatch to the private `_gate` recipe in the `justfile`; the only difference is
whether the markdown/YAML steps autofix (`fix`) or verify (`check`). Do not commit or
push when the gate fails.

## What the gate actually runs

`just test` (invoked by the gate) is the source of truth for the test surface, not any
external framework's defaults. It runs the in-repo skill/eval validators and their
self-tests, the bash-shortening rewriter self-test, the ralphify-spec pytest, and the
Bats suites under `tests/`:

- `python3 .github/scripts/validate_skills.py` — validates every `SKILL.md`'s YAML
  frontmatter (`name` must match the parent directory) per the Agent Skills spec.
- `python3 .github/scripts/validate_evals.py` — validates skill eval definitions.
- Bats suites in `tests/bash/` and `tests/bash-shortening/`.

Shell linting is scoped narrowly (`shellcheck scripts/install.sh
skills/file-handler/scripts/skillz.sh`) because those are the only first-party shell
scripts; most skills are pure Markdown.
