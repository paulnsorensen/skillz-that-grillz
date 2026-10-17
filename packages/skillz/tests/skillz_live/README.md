# Live smoke runs

The repository tests fake every model call.
The `just live-smoke HARNESS MODEL [SCENARIO]` recipe runs the real stack from a source checkout: harness, sandbox, GEPA reflection, gate, and export.
It runs only when `SKILLZ_LIVE=1` is set. `just build` and `just ci` never set it, and the gate removes it from each step.

```sh
SKILLZ_LIVE=1 just live-smoke codex gpt-6-luna
SKILLZ_LIVE=1 SKILLZ_LIVE_DRY=1 just live-smoke codex gpt-6-luna all
```

Each run scenario runs `self-test --preflight-only`, then one `run` of a fixture skill with 12 fixed cases and `--repeats 1`, then `export`.
Each fixture seed carries one fixable flaw, so the search can beat it. A completed run must show scored holdout cases.
With `SKILLZ_LIVE_DRY=1`, each run scenario stops at its budget question, records the estimate, and makes no model call.
`SCENARIO` is `basic` (the default), `all`, or a comma list of these names:

| Scenario | What it runs | Run cap |
| --- | --- | --- |
| `basic` | Built-in harness, no contract, default judge | 97 |
| `command-harness` | `--harness command` through a test-only bridge to `codex exec` | 97 |
| `command-grader` | Contract with a `command` grader, and one `source: session` case | 53 |
| `hybrid-mixed` | `--harness-config` with a Codex task role and a command judge role, a `hybrid` grader, contract `statistics`, and `--min-gain` | 97 |
| `prose-cli` | `--edit prose+cli` with a helper script and an `exact-json` grader | 53 |
| `journey` | Journey cases. Only `claude` holds a conversation, so `codex` must stop with `journey-unsupported` | 229 |
| `resume` | `basic`, stopped with SIGINT after 3 outcomes, then the same command again | 97 |
| `analytics` | The bundled `agent-session-analytics.pyz`: a forced ingest of the real session logs into a private database, then every query of the three Usage packs | no model call |

A run approves its estimate only when the estimate is at most its cap. The preflight gets at most 3 more calls.
The `command-harness` and `hybrid-mixed` scenarios need `codex` and skip with another `HARNESS`.
A test passes when the run reaches `phase == complete`, the export writes `candidate.patch`, and the gate scores holdout cases.
A test also passes when a step after the preflight stops with a coded error.
An unexpected preflight stop does not pass: the preflight checks the host and the sandbox, and the run makes no task call.
A scenario that expects a stop code passes only with that code, at any stage.
The `resume` scenario must interrupt its first process with a nonzero exit before `phase == complete`.
When it stops before the interrupt, the failure message or the record shows the stage and the stop code.
Any other failure fails the test. The `skillz-pytest` gate step checks this pass rule offline, without a model call.
Each attempt appends one JSON line to `.live-smoke/runs.jsonl`: scenario, harness, model, host OS, revision, stage, verdict, code, calls, and seconds.
Git ignores that folder.
