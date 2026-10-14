# criteria

Acceptance criteria that tests trace to. The gate `.github/scripts/check_ac_trace.py` runs in `just build` and `just ci`.

## Format

One file per source, named `<source>.md`. A source is a durable spec slug (`skillz-wedge-first-class`) or a ticket (`gh-<issue number>`). The file holds one H1, one `Source:` line, and one table:

```markdown
| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN ... THE SYSTEM SHALL ... |  |
| AC-2 | process | WHEN ... THE SYSTEM SHALL ... | pre-gate: PR #1 review |
```

- `id` keeps the original criterion id (`AC-3`, `AC-W1`). It is unique in its file.
- `kind` is `test` or `process`.
- A `test` row needs at least one test marked `@pytest.mark.ac("<source>:<id>")`.
- A `process` row names its evidence in `verified by` and needs no marked test. A criterion of a source that merged before the gate, with no marked test, is `process` with `pre-gate: PR #<n> review`.
- The `Source:` line holds the spec slug, the issue URL, or `reconstructed from <where>` when the spec text is absent from the repository.

## Markers

A marker argument is `<source>:<id>`. A bare id such as `AC-3` fails pytest collection in skillz, wedge, and fromargs. The gate scans `packages/{skillz,wedge,fromargs}/tests` and `.github/scripts/test_*.py` with `ast`. It counts a decorator, a module or class `pytestmark`, and `pytest.param(marks=...)`.

The gate fails on a `test` row with no marked test, a marker for an unknown source or id, a `process` row without `verified by`, a kind other than `test` or `process`, a non-literal marker argument, a duplicate id, and a malformed table.

A ticket's pull request adds `gh-<n>.md` and its markers together. The decision record is `docs/agents/decisions/ac-traceability.md`.

<!-- HALLOUMINATE:INDEX-START -->
- [fromargs-cli-library](./fromargs-cli-library.md) — fromargs-cli-library: fromargs public contract
- [gh-131](./gh-131.md) — gh-131: the network preflight probes name lookup and a non-loopback address
- [gh-223](./gh-223.md) — gh-223: skillz_experiments module layout and borrow waves
- [gh-235](./gh-235.md) — gh-235: freeze a run compatibility fingerprint (W2.4)
- [gh-236](./gh-236.md) — gh-236: deterministic proposal leakage check (W3.1)
- [gh-238](./gh-238.md) — gh-238: move the reflection prompt into search/_proposer.py (W4.1)
- [gh-239](./gh-239.md) — gh-239: annealed edit budget and run-status block in the reflection prompt (W4.2)
- [gh-240](./gh-240.md) — gh-240: detect search stalls and stop GEPA through stop_callbacks (W4.3)
- [gh-267](./gh-267.md) — gh-267: acceptance-criterion traceability gate
- [gh-279](./gh-279.md) — gh-279: reuse recorded search outcomes on resume
- [gh-285](./gh-285.md) — gh-285: isolate the temp root of the journey sandbox tests
- [gh-290](./gh-290.md) — gh-290: the settings floor and the approval block in the preflight
- [gh-292](./gh-292.md) — gh-292: seeded trigger and near-miss cases default family to the case id
- [gh-293](./gh-293.md) — gh-293: read block-scalar descriptions in full in the facts audit
- [gh-294](./gh-294.md) — gh-294: --edit prose with non-Markdown editable names stops with editable-needs-cli
- [gh-295](./gh-295.md) — gh-295: the doctor checks the run's claude executable, and a nonzero inventory exit stops with host-not-ready
- [gh-298](./gh-298.md) — gh-298: document code-enforced fixture and case rules in experiments.md
- [gh-299](./gh-299.md) — gh-299: the shipped skillz contract scores task cases
- [gh-302](./gh-302.md) — gh-302: prose+cli proposals leak runner context into candidate code comments
- [skillz-experiments-end-to-end](./skillz-experiments-end-to-end.md) — Skillz experiments end-to-end prototype criteria
- [skillz-wedge-first-class](./skillz-wedge-first-class.md) — skillz-wedge-first-class: wedge first-class in autoimprove
- [wedge-skill-packaging](./wedge-skill-packaging.md) — wedge-skill-packaging: wedge skill packaging
<!-- HALLOUMINATE:INDEX-END -->
