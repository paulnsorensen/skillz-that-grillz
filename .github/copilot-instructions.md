# Review instructions — skillz-that-grillz

GitHub Copilot code review reads this file. CodeRabbit also reads it as a
code guideline. `.coderabbit.yaml` holds only the CodeRabbit-specific
settings.

This repo is a skill-authoring and skill-packaging toolbelt. It contains:

- The published Agent Skills under `skills/`. The README `## Skills` table
  lists them.
- Repo-local skills under `.agents/skills/`. They are tooling for this repo
  only.
- A uv workspace of Python packages under `packages/`: `packages/wedge`
  (`wedge`), `packages/skillz` (`skillz_experiments` and `skillz_inspect`,
  bundled into `/skillz`), and `packages/fromargs` (published to PyPI).
- The public composite GitHub Action `actions/wedge`. It packages a skill CLI
  as a content-addressed `.pyz` for other repositories.

It is not an agent framework, an orchestrator, or an MCP server. No skill
requires an MCP server.

## What to find, in priority order

1. **Correctness bugs in `packages/` and `actions/`.** Wrong results, crashes on
   valid input, and silent success on failure. Include a concrete input that
   triggers the bug.
2. **Evaluation and scoring bypasses.** Code in `packages/skillz/src/skillz_experiments/`
   scores candidate skills. Flag any path that lets a candidate get credit
   without doing the work, such as a helper that runs with the wrong input.
3. **Contract drift.** A change that makes a skill, README, CHANGELOG, or ADR
   in `docs/agents/decisions/` disagree with the code or with `AGENTS.md`.
4. **Unsafe procedures in skills.** A skill step that does an irreversible
   action (tag push, publish, merge, deploy) without a user confirmation. A
   step that continues after `just build` fails. `AGENTS.md` makes a failed
   `just build` a hard stop.
5. **Skill design.** Apply `.github/instructions/skills.instructions.md` to
   every `SKILL.md`.
6. **Architecture drift.** Semantic drift in `packages/*/src/` that the
   layout tests cannot see. The "Architecture (Sliced Bread)" section in
   `AGENTS.md` holds the rules. Do not flag what `test_layout.py` or
   `check_architecture.py` already enforces. Flag these changes:
   - A method that mixes lifetimes.
   - A new protocol or registry with one implementation.
   - Logic in a crust `__init__.py`.
   - A new cross-slice dependency that belongs in the kernel.
   - New methods on a class that was just split, such as `Session` or
     `ClaudeCode`. Suggest a collaborator in the slice.
7. **Scope creep.** New abstractions without a demonstrated need. Coupling
   between `wedge` and `fromargs` beyond ADR-006 in
   `docs/agents/decisions/wedge-skill-packaging.md`.

## Verify before you comment

Past false positives came from unverified assumptions. Before you post a
finding, do these checks:

- **Counts and lists.** Count the items in the source of truth. Do not infer a
  count from the diff alone. Some tables have optional rows, for example the
  optional Usage lens in `skills/skillz`.
- **Field meaning.** Read the docstring, reference doc, or ADR that defines a
  field before you assume its semantics. For example, a contract `editable`
  list is an explicit allowlist, not a list of extra paths.
- **Test scope.** A test that covers one configuration does not prove the
  behavior for other configurations. Check which fixture the test uses.
- **Third-party tool behavior.** Do not assume how `gh`, `uv`, or GitHub
  Actions behave on failure or retry. Cite the documentation or source, or
  mark the finding as unverified.
- **Earlier fixes.** Do not suggest a change that reverts a fix from an
  earlier review thread on the same PR.

## What not to flag

- Cheese, Dune, and Mad Max flavor in user-facing docs. This is the
  intentional repo voice. Commit messages and YAML frontmatter stay neutral.
- Missing backward-compatibility shims, deprecation paths, or migration
  plans. Each release is the new baseline.
- Issues that the validators catch: `markdownlint`, `yamllint`, and
  `validate_skills.py`. `just build` runs them in CI.
- Prose style in Simplified Technical English. Short sentences and repeated
  terms are deliberate.
- Sliced Bread growth that `AGENTS.md` allows: a concept that stays in one
  file, and a split for one consumer that puts real pressure on the code.
  Also file or function size alone. A number is not pressure.

## How to write review comments

- State the impact first, with a concrete failure scenario.
- Quote the smallest section that needs a change.
- Suggest the fix. Do not only describe the problem.
- Mark a speculative finding as speculative. Do not make it a blocker.
- Post one comment for each distinct problem. Do not post praise or
  summaries of unchanged code.