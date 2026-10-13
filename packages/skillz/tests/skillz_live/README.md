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
| `basic` | Built-in harness, no contract, default judge | 85 |
| `command-harness` | `--harness command` through a test-only bridge to `codex exec` | 85 |
| `command-grader` | Contract with a `command` grader, and one `source: session` case | 47 |
| `hybrid-mixed` | `--harness-config` with a Codex task role and a command judge role, a `hybrid` grader, contract `statistics`, and `--min-gain` | 85 |
| `prose-cli` | `--edit prose+cli` with a helper script and an `exact-json` grader | 47 |
| `journey` | Journey cases. Only `claude` holds a conversation, so `codex` must stop with `journey-unsupported` | 199 |
| `resume` | `basic`, stopped with SIGINT after 3 outcomes, then the same command again | 85 |
| `analytics` | The bundled `agent-session-analytics.pyz`: a forced ingest of the real session logs into a private database, then every query of the three Usage packs | no model call |

A run approves its estimate only when the estimate is at most its cap. The preflight gets at most 3 more calls.
The `command-harness` and `hybrid-mixed` scenarios need `codex` and skip with another `HARNESS`.
A test passes when the run reaches `phase == complete` and the export writes `candidate.patch`, or when a step stops with a coded error.
A scenario that expects a stop code passes only with that code. Any other failure fails the test.
Each attempt appends one JSON line to `.live-smoke/runs.jsonl`: scenario, harness, model, host OS, revision, stage, verdict, code, calls, and seconds.
Git ignores that folder.
