# Acceptance-criterion traceability decisions

These records explain how `just build` traces acceptance criteria to tests. The source is issue #267 and the approved spec `ac-traceability`.

## Records

### ADR-001: Qualify each ac marker with its source  [status: accepted]

- **Context:** The `ac(id)` marker took a bare id, so `AC-3` meant different criteria in skillz `test_layout.py`, skillz `test_compatibility.py`, and wedge `test_targets.py`. Nothing but the fromargs mutation runner read the markers.
- **Decision:** A marker argument is `<source>:<id>`. The source is a durable spec slug (`[a-z0-9]+(-[a-z0-9]+)*`) or `gh-<issue number>`. The id keeps its original form (`AC-3`, `AC-W1`). A `pytest_collection_modifyitems` hook in the `tests/conftest.py` of skillz, wedge, and fromargs raises `pytest.UsageError` for a bare or malformed argument. Each package carries its own copy of the hook, because the packages are independent distributions.
- **Alternatives:** Keep bare ids and infer the source from the test path. Rejected: a path mapping hides the link. Share one hook through a plugin. Rejected: the packages publish separately.
- **Consequences:** Every marker names its criterion without ambiguity. The fromargs mutation runner selects tests by the qualified id.

### ADR-002: Keep criteria in the repository, with a kind  [status: accepted]

- **Context:** Criteria lived in GitHub issues, in a spec corpus outside the repository, or only in test docstrings. CI could not read them.
- **Decision:** `docs/agents/criteria/<source>.md` holds one H1, one `Source:` line, and a table `| id | kind | criterion | verified by |`. The kind is `test` or `process`. A `test` row needs a marked test. A `process` row needs a non-empty `verified by` and needs no marked test. A criterion of an already-merged source with no marked test is `process`, with `verified by` set to `pre-gate: PR #<n> review`. A source absent from the repository gets rows reconstructed from its issue, its PR, or its test docstrings, and its `Source:` line says so. The folder sits beside the decision records, so the wiki indexes it.
- **Alternatives:** Read the criteria from the spec corpus or from GitHub. Rejected: the gate must read repository files only. Treat an untestable criterion as prose. Rejected: it reopens the `prose-fallback` loophole.
- **Consequences:** The cook that lands a ticket writes `gh-<n>.md` in the same PR as its tests. Old untested criteria stay `process`, so only new tickets get full enforcement.

### ADR-003: One stdlib gate reads markers with ast  [status: accepted]

- **Context:** The gate must not import the packages under test, and it must see every marker form.
- **Decision:** `.github/scripts/check_ac_trace.py` is stdlib-only. It parses `packages/{skillz,wedge,fromargs}/tests/**/*.py` and `.github/scripts/test_*.py` with `ast` and finds each `pytest.mark.ac(...)` call: a decorator, a module or class `pytestmark`, and `pytest.param(marks=...)`. It exits 1 and prints one line per fault. A path that resolves outside the repository is a fault. `run_gate.py` runs it as `ac-trace`, with `ac-trace-tests` beside it.
- **Alternatives:** `pytest --collect-only`. Rejected: it imports the packages and needs three environments.
- **Consequences:** The gate proves that a link exists. It does not prove that the marked test verifies the criterion. The `.github/scripts` tests are in scope so the gate's own tests carry markers.

_Source: issue #267, `.github/scripts/check_ac_trace.py`, and `docs/agents/criteria/index.md` · Updated: 2026-10-12_
