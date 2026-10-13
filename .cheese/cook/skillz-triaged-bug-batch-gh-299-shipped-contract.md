# Cook handoff: gh-299 shipped contract

## Done
- Removed `kinds.audit` from `skills/skillz/evals/autoimprove.json`; rewrote the task rubric to grade `expected_output` and each assertion on 0-100.
- Invocation changed from `$skillz audit` to `$skillz`. Reason: the task requests are `/skillz wedge ...` and `/skillz improve ...`, so a fixed `audit` mode would conflict with them.
- Seeded task cases `wedge-supported` and `improve-proposes-offload` with family, assertions, and fixtures under `skills/skillz/evals/files/`.
- Added `docs/agents/criteria/gh-299.md` and `test_shipped_contract.py` (AC-7, AC-8).

## Verified
- `just build` exits 0.

taste_test: deferred-to-orchestrator
