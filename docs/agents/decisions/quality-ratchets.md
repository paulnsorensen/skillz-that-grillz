# Quality ratchet decisions

These records explain three gates ported from paulnsorensen/milknado: the file-size ratchet
(`.github/scripts/check_file_lengths.py`), the whole-symbol dead-code gate (`.github/scripts/check_dead_code_coverage.py`),
and the coverage gate (`.github/scripts/check_coverage.py`).
milknado runs them in `just check-llm` as `file-size`, `dead-code-coverage`, and the project and diff coverage steps.

## Records

### ADR-001: The file-size budget is a one-way ratchet  [status: accepted]

- **Context:** milknado holds `src/` files to 300 lines. A larger file needs a waiver with a measured ceiling and a reason. A PR may raise a ceiling when it gives a reason. At adoption, 24 of the 132 `packages/*/src` files here have more than 300 lines.
- **Decision:** Keep the 300-line budget and the per-file waivers in `[tool.skillz.file-size]` of `pyproject.toml`. Unlike milknado, `check` also fails when a waiver is stale, when its file fits the budget, or when its file is shorter than its ceiling. `check_file_lengths.py tighten` lowers each ceiling to the measured count and removes the waivers that are no longer needed. It never raises a ceiling.
- **Consequences:** A change that shrinks a waived file runs `tighten` in the same change, as with the depth ratchet. Only a human raises a ceiling, and the PR says why. The gate runs as the lanes `file-size` and `file-size-tests`.

### ADR-002: One coverage lane checks the combined data of every pytest lane  [status: accepted]

- **Context:** milknado runs one pytest suite with `--cov-fail-under` and then `diff-cover`. Here, five pytest lanes measure four packages, and CI runs the two skillz lanes in six shard jobs.
- **Decision:** Each pytest lane runs as `coverage run --parallel-mode -m pytest` on the source of every workspace package, because the tests of one package also run the code of another (the skillz tests call `wedge.layer_import_names`). `[tool.coverage.run] patch = ["subprocess"]` measures the xdist workers and the CLI tests that start Python subprocesses. Each process writes `<lane>[.<i>of<n>].cov.<host>.<pid>.<random>` to `$GATE_COVERAGE_DIR`, else to a directory below the gate temporary root. No process combines or removes these files before the `coverage` lane. A gate removes stale lane data from its directory when it starts, so a nested gate (such as the `test_run_gate.py` fakes) must not inherit `GATE_COVERAGE_DIR`: it removed the files of live test subprocesses, and the failed save put a traceback on their stderr. After every other lane passes, the `coverage` lane runs `check_coverage.py`. It combines the lane files, then checks the project floor (`[tool.coverage.report].fail_under`), whole-symbol dead code, and diff coverage (`[tool.skillz.coverage].diff_fail_under`, 95 as in milknado). The diff base resolves as the slice guard base does (architecture-gates ADR-005). When `GATE_SKIP` names a pytest lane, the `coverage` lane does not run. In CI, the gate job and each shard upload their lane files, and the `coverage` job combines them.
- **Consequences:** `just build` runs the full check locally. CI reports it as the `coverage` check, a required check of the `main-protection` ruleset. The job has no `if:`, so it is skipped when the gate or a shard fails; GitHub counts a skipped required check as passing, so `coverage` stays required together with `gate (just ci)` and `skillz tests`. The diff check prints the measured line count and says so when a diff has no measured source line. The project floor is the measured total at adoption, rounded down. The gate asks for a raise when the total reaches one point above the floor; it does not raise the floor itself.

### ADR-003: Whole-symbol dead code reads only `packages/*/src`  [status: accepted]

- **Context:** The milknado gate maps one `src/` tree to module names and exempts `[project.scripts]` targets of one package.
- **Decision:** A module name is the path below `packages/<name>/src`. Exemptions are the `[tool.vulture].ignore_decorators` patterns, the `[project.scripts]` targets of every workspace package, and the `module:qualname` entries in `[tool.skillz.dead-code].allow`. Each allow entry carries a comment that says why no test reaches the symbol.
- **Consequences:** A function that only a built `.pyz` archive or a live harness runs needs a test, a `# pragma: no cover`, or an allow entry. A function whose whole body is on its `def` line cannot be flagged.

_Source: milknado `scripts/check_file_lengths.py`, `scripts/check_dead_code_coverage.py`, and `justfile` `check-llm` at origin/main 39e9247 · Updated: 2026-10-29_
