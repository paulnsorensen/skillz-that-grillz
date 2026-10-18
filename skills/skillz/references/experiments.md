# Autoimprove: one run, one export

Use this workflow for `/skillz autoimprove`.
`experiment` is an alias for `autoimprove`. Both route to this reference.
The `skillz-experiment` executable keeps its name.
The `optimize` and `tighten` aliases still mean `improve`.
The runner measures a skill and never applies a patch.
A skill needs no contract and no case manifest.
Use Linux and Python 3.11 or later. macOS is untested: a real Mac test is still open.
The installed skill includes the runner, GEPA 0.1.4, and its CLI dependencies.
It needs no source checkout or runtime package installation.

## Concepts

- **Run**: one resumable `run` directory that holds the cases, one search, and one holdout gate.
  Before the search, the run scores the seed once on every train and validation case.
  It stops with `cases-too-easy` when the seed already scores 0.9 or more on a family.
  A run that sets `min_token_saving` records these scores and does not stop.
- **Case draft**: the file `RUN/cases.draft.json` that you write from the skill, and from past sessions when analytics exists.
- **Split**: the train, validation, or holdout group of a case. Code assigns it from a recorded seed.
- **Approval**: the user's answer to one question. The runner binds each answer to a hash or to a call count.
- **Budget**: the estimated calls and seconds. After approval, a run uses at most 200 calls and 7200 seconds.
  The search stops early enough to leave the holdout gate its estimated calls and time.
  The estimate has a `headroom` line: one seed score on each train case.
  The headroom pass also scores the seed on each validation case. The search reuses that score, so it costs no new call.
- **Gate verdict**: `promote`, `promote-cheaper`, `inconclusive`, or `reject`. It compares baseline and winner on the holdout cases.
- **Export**: the write-only step that saves the winner as a private patch and a redacted report.

The optional custom contract is outside these seven concepts. A first run does not need it.

## Questions

Ask the user at most these three questions, in this order.
Never ask about keys, tokens, or manifests.
The contract question under `No contract` is outside these three. Ask it only when the user requests a contract.

1. Harness and model: "Which harness and model do you want to use? Use `claude` or `codex`." Skip it when the user already named both.
2. Case approval: show the `question` text from the `cases-unapproved` stop, and ask the user to approve the listed cases.
3. Budget approval: show the `estimate` from the `budget-unapproved` stop, and ask the user to approve that many calls.

## Procedure

Set the installed skill path. Run the commands from any directory.
Put `--out` outside the target skill directory.
Make it a private directory with `mktemp -d`. The run refuses a directory that other users can enter.

```sh
SKILLZ=/absolute/path/to/installed/skillz
OUT=$(mktemp -d)
python3 "$SKILLZ/scripts/skillz-experiment.pyz" run --target /path/to/skill --out "$OUT" --model MODEL --harness claude --live
```

Pass the same command again after each stop. Add the approval option that the stop names.
A model call needs `--live`; without it the run stops with `live-required`. The three data stops happen before any model call.
`--harness` accepts `claude`, `codex`, or `command`. The built-in harnesses launch only headless `claude -p` or `codex exec`.
To run your own command, pass `--harness-config PATH` or `--harness command --command EXECUTABLE --command ARG ...`. Write an argument that starts with `-` as `--command=VALUE`.
`--harness-config` cannot combine with `--harness` or `--command`; `--command` needs `--harness command`, and `--harness command` needs `--command`. Each misuse stops with the code `usage` before any work.
`--judge-model MODEL` sets the model of the judge calls on the same harness. Task and reflection calls use `--model`.
Without the flag, the judge uses `--model`. Prefer a judge from another model family or tier that the same harness can run.
The first run records the flag. A resume that omits it keeps the recorded judge model.
The judge role also plays the simulator of a journey case, so the flag moves that simulation to the judge model too.
`--judge-model` cannot combine with `--harness-config` or `--harness command`; set the judge model in the role file or the command.
The `doctor` host checks skip a command role. The runner sends its sandbox probe before any task call.
The `claude` harness reuses the Claude login. The `codex` harness reuses the ChatGPT login and needs Codex CLI 0.154.0 or newer.
The runner records the observed `codex --version` string as `codex_version` in `run.json`.
A version below the minimum, or output that the runner cannot parse, stops with `codex-version-unsupported` before any model call.
A resume on a different Codex version stops with `environment-differs` before any model call; restore the recorded version or start a new run.
The `codex` harness retries a call that fails as `rate-limited`, at most 3 times, with a 2, 4, then 8 second backoff.
`codex exec` reports no structured retry-after, only prose in its error and `turn.failed` messages.
A retry waits at least as long as a wait that this prose names: a duration (`30 seconds`, `1h30m`, `2 days`), a timestamp, or a `Retry-After` number of seconds.
A reset hint that the runner cannot read counts as longer than the remaining time, so the call fails as `rate-limited` with no retry.
A retry runs only within the remaining time.
A retry does not spend the approved call budget. The run record and the `summary` count it in `retries`.
The replay reserve and the holdout reserve count calls only. Rate-limit backoff spends time from the replay window, and the run deadline still caps time.
Each rate limit halves the concurrent model calls for the rest of the run, to a minimum of 1.
The private failure evidence file keeps a redacted stderr excerpt (at most 1 KiB), the error event messages, and any `retry_after_seconds`.
The last `turn.failed` message always classifies the failure and stays in the evidence, however many error events come before it.
A ChatGPT-plan usage limit ("You've hit your usage limit") fails as `usage-limited`. The runner does not retry it and does not halve the concurrent calls.
It is an unscored `evaluation-failed` outcome that counts toward `search-faulted` and `holdout-faulted`. The runner does not read its "try again at" time as a wait.
Each model call (every harness) has a per-call timeout of 4 times the per-call estimate, capped by the remaining search time.
The exception is a Claude journey turn. It still runs under the remaining search time only.
The estimate is 60 seconds plus the sandbox seconds of one evaluation in the run plan.
The clock starts after the call gets a call slot. A wait for a slot that reaches the search deadline is a budget stop, not a timeout.
Each retry attempt gets the same limit.
A task or judge call that exceeds it ends as an `evaluation-failed` outcome with `reason: timeout`.
A reflection call that exceeds it records a reflection outcome with `status: timeout`, and the proposal ends with no candidate and no second reflection call.
A reflection outcome with a status is no stall observation.
When every feedback record is unscored, the run skips the reflection (`status: skipped`, `reason: no-scored-feedback`) and the proposal ends with no candidate.
A call that the search deadline stops is a budget stop, not a timeout.
The run record and the `summary` hold `calls_by_role` (counts that sum to `calls`) and `seconds_by_role` (wall seconds of `task`, `reflection`, `judge`, and `failed` calls).
A record from before the roles exists puts its earlier calls under `unattributed`.
Put the real Codex binary directory first on `PATH`, not a multicall version-manager shim.
The `doctor` runs `claude --version` with the environment of the run, as `_executable_environment` builds it. The `claude` row fails when the executable is not on `PATH`. It also fails when `--version` exits nonzero or does not finish.
A skill inventory call that exits nonzero with no control response stops the run with `host-not-ready`. This applies to every inventory call. Any other inventory failure, such as a response with no valid command list, stops with `preflight-leak`.
The sandbox preflight probes the loopback addresses `127.0.0.1` and `localhost`. It also probes a direct name lookup and a non-loopback TCP address. The probe version is `NETWORK_PROBE` `loopback-dns-nonloopback-v3`. Domain and proxy policy beyond these probes stays unverified. Do not treat the preflight as proof of full network isolation.
Do not create or request provider credentials. The runner needs no API key.
A fresh run first runs the free host checks of `doctor`. They make no model call.
A failed check stops the run with `host-not-ready` before the case draft. The stop lists every check and its fix.
When roles use more than one built-in harness, `checks` stays one flat list. Each check name carries the harness as a prefix, such as `codex:credential`.
A `claude` role with its own `command` gets the host checks on that executable.
The `credential` check names the kind: `subscription login`, `api key`, `unverified (macOS Keychain)`, or `missing`.
Run `python3 "$SKILLZ/scripts/skillz-experiment.pyz" doctor --harness claude` to see the checks without a run.
Claude Code's own sandbox confines Bash commands.
`--edit prose+cli` is the default. It also changes the helper scripts and the own wedge sources of the skill, in the same single search.
A skill with no editable helper script, no own wedge source, and no usable `@new-cli` falls back to `--edit prose`.
The fallback also applies when the `@new-cli` site layer fails or lacks cyclopts. The runner writes one note on stderr with the reason, and the run does not stop.
A resume without `--edit` keeps the mode of the first run.
`--edit prose` changes only Markdown files.
A `prose+cli` edit can change the command-line interface of a script. Every caller and every `SKILL.md` or reference mention must change to match.
`--repeats` sets the repeats for each holdout case. The default is 3.
`--seed` sets the split seed. Keep the default unless the user asks.
`--effort LEVEL` passes `--effort` to every Claude Code call: `low`, `medium`, `high`, `xhigh`, or `max`.
`--sandbox-read PATH` mounts one absolute host path read-only in the runner's OS sandbox. Repeat it for more paths.
Use it for a browser install, for example `/opt/pw-browsers`.
A root must exist, and it must not be a symlink. Give the resolved path.
A root must not equal or hold the home directory.
A root must not equal, hold, or sit inside the Claude config directory, the temporary directory, or `--out`.
A root must not equal, hold, or sit inside a runtime path or a credential path.
The runtime paths are `/proc`, `/sys`, `/dev`, `/run`, `/var/run`, `/tmp`, `/var/tmp`, `/var/snap`, `/var/lib`, and `$XDG_RUNTIME_DIR`.
The credential paths are `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config`, `~/.docker`, `~/.kube`, `~/.netrc`, `~/.claude.json`, the shell rc and history files, `~/.git-credentials`, `~/.npmrc`, `~/.pypirc`, `~/.password-store`, `~/.local/share/keyrings`, and the Claude login file.
A root must not hold a socket entry. A tree of more than 100000 entries is too large to check, so the runner refuses it.
Home caches such as `~/.cache/ms-playwright` stay allowed.
`--sandbox-seconds N` sets the time limit of one OS sandbox command, from 1 to 600. The default is 20.
Once any scored case uses a command or hybrid grader, this time applies to every evaluation.
Parallel calls share it. The estimate and the holdout gate reserve include it.
The search shrinks so that the estimate fits the time cap. A value that is too large stops with `budget-unapproved`.
The stops that ask for approval echo the options as `claude_options`.
These three options apply only to `--harness claude`. The first run records them.
`--min-gain N` and `--min-lower-bound N` set the floors of the gate. `--family-budget N` sets the regression budget of every family. `--max-token-increase-per-gain N`, `--min-token-saving N`, and `--max-quality-loss N` set the token rule. Each value must be at least 0. `--min-token-saving` must also be above 0 and below 1, and it needs `--max-quality-loss`. `--min-coverage N` sets the least share of planned holdout cases that must be scored, from 0 to 1. "Set promotion thresholds" describes them.
When the run directory already holds a run, the same command resumes it. A resume needs no `--edit`, `--repeats`, `--seed`, Claude option, or threshold flag.
A value that you pass on a resume must match the first run. The target skill directory must also match.

Each stop prints one JSON object on stdout and a coded error on stderr.
Each object holds `stop` (the code) and `message`. The table lists the other fields.
Map each coded stop to its next step:

| Code | Data on stdout | Next step |
|---|---|---|
| `host-not-ready` | `ok`, `harness`, `checks` (each with `check`, `status`, `detail`, and `fix` on a failure) | Show the user each failed check and its fix. Run again after the fix. No model call ran. |
| `cases-missing` | `draft` (path), `facts` (skill name, description, files), `seed_file` (the path of `RUN/cases.seed.json`) and `seed_summary` (`cases`, and the case count of each kind and each family; a task case with no family is in no family count; both only when `evals/evals.json` exists), `doctor` (the host checks) | Write the case draft. Start from the `seed_file` when it is present. Then run again. |
| `cases-unapproved` | `question`, `case_hash`, `seed`, `statistics` | Ask question 2. Run again with `--approve-cases HASH`. Keep any threshold flag in the command. |
| `budget-unapproved` | `estimate` (calls, seconds, search calls, repeats, holdout cases, holdout retry calls, `replay_calls`, `headroom` with `calls` and `outcomes`; `journey` with `calls`, `seconds`, `ceiling_calls`, `ceiling_seconds` when a case is a journey), `statistics` | Ask question 3. Run again with `--approve-budget CALLS`. Keep `--approve-cases HASH` and any threshold flag in the command. |
| `cases-too-easy` | The seed already scores at least 0.9 on a train or validation family. The message names each family, its split, and its mean. | Do not ask a new question. Draft harder cases in a new run directory, then ask question 2 again. No search call ran. A resume of this run returns the same stop. |
| `baseline-contract-rejected` | `next`, and optional `detail` | The original skill fails the contract check. Fix the skill or the contract, then start a new run directory. |
| `gate-budget-exhausted` | `next` | The holdout gate cannot finish within the approved calls. Start a new run directory. |
| `live-required` | `next` | The run needs a model call and `--live` is missing. Run again with `--live`. |
| `run-in-progress` | `next` | Another run holds the run directory. Wait for it to finish, then run again. |
| `run-terminated` | `next`, `failure_code` | The run directory holds a stop from `isolation-failed` or `credential-changed`. Start a new run directory. |
| `run-record-tampered` | Only `stop` and `message` | The budget in `run.json` differs from the plan that the frozen cases give, the `phase` is unknown, or the `statistics` are malformed. A frozen file is changed or missing, a fixed file name is bad, an editable name is not a seed file, or the `layers` or `own_targets` record is malformed. Start a new run directory. |
| `site-layer-drift` | `stop`, `message`, `next` | On resume, `uv.lock`, the dependency groups, or `wedge.toml` differ from the run record. A recorded layer that cannot be reused, or a recorded target that `wedge.toml` no longer lists, also causes it. Start a new run directory. |
| `build-failed` | `stop`, `message`, `next` | At the holdout gate, the winner no longer builds. Start a new run directory. |

These other codes arrive on stderr as `code`, with no data on stdout:

| Code | Meaning | Next step |
|---|---|---|
| `login-missing` | No Claude login file exists, or Claude Code rejects the login during the preflight (authentication failed). A Codex role finds no `tokens` and no `OPENAI_API_KEY` in `auth.json`. | Ask the user to run `claude` or `codex login` once. |
| `sandbox-read-invalid` | A `--sandbox-read` root is missing, is a symlink, holds an unsafe character, overlaps a refused path, holds a socket entry, or is too large to check. The message names the root. | On a fresh run, give a valid resolved path. On a resume, restore the path or start a new `--out`. |
| `credential-changed` | The login file moved, or the runner could not restore a refreshed login file. A run that already completed keeps its result and records a `close_warning` instead. The `close_warning` appears in the run summary JSON on stdout, not on stderr. | If the run completed and has a `close_warning`, export the result. Then log in again before the next run. Otherwise log in again and start a new run directory. |
| `clock-skew` | On resume, the wall clock is more than 5 seconds earlier than the run start. | Set the clock right, then run again. Otherwise start a new run directory. |
| `sandbox-unavailable` | The Bash sandbox cannot start, or the temp directory path is too long for the Unix-socket probe. | Apply the fix in the message (for a long path, set `TMPDIR` to a short directory such as `/tmp`), then run again in the same directory. The runner never weakens the sandbox, except one rule on a host that blocks the nested user namespace, which the live socket probe guards. |
| `preflight-leak` | A user skill, plugin, agent, or MCP server loads, or a skill stays on after `skillOverrides` turns it off. A failed read, write, or sandbox probe also stops with this code. The Read tool returning a sealed file outside the workspace, or never being called, also stops the run. A `CLAUDE.md` or `CLAUDE.local.md` file in an ancestor of the workspace also stops the run. On macOS, a `CLAUDE.md` file or a `rules` directory with `.md` files in the config directory also stops the run, before any model call. The code also covers a skill inventory that times out, a Claude Code process that fails to run, and a stream with no init event; the message gives the exit code and a stderr excerpt. When the live preflight call returned and a check then failed, the message also names `RUN/preflight.events.json`. | To find the cause of a failed probe, read `RUN/preflight.events.json` before you spend another live call. For a leak, move the named entry out of the config directory or the ancestor directory for the run, then run again. For a Read-tool leak, report the Claude Code version; no unsafe fallback exists. For a timeout, a failed process, or a missing init event, read the message, check that `claude` starts, then run again. |
| `nono-removed` | A resume found a run that recorded the removed nono backend, or a role file sets `isolation` to `nono`. The message links issue 211. A completed run still returns its summary. | Start a new run directory. |
| `isolation-removed` | A role file sets the removed `isolation` key. | Delete the key. |
| `codex-version-unsupported` | The Codex CLI is older than 0.154.0, or its `--version` output is unparsable. No model call ran. | Install Codex 0.154.0 or newer, then run again. |
| `environment-differs` | A resume runs on a different Codex version, or the runtime environment differs from the frozen record. The message names the changed key. No model call ran. | Restore the recorded version or start a new run. |
| `usage` | `--harness-config`, `--harness`, and `--command` conflict. The message names the conflict. | Fix the options, then run again. |
| `candidate-name-taken` | The candidate skill name matches a Claude Code command or another loaded skill. A contract companion name can also match one. The init event then cannot show that the candidate loaded. No model call ran. | Rename the skill or the companion, then start a new run directory. |
| `evals-unreadable` | `evals/evals.json` is a symlink, too large, or malformed. At the `cases-missing` stop, an entry can also list a fixture that is unsafe, missing, empty, too large, or not UTF-8 text, or the seed can be too large. The message names the problem. | Fix `evals/evals.json` or the fixture, then run again. |
| `cases-uncovered` | The draft lacks an entry of `evals/evals.json`, or changes its `kind` or `request`. The message names each entry and the difference. The stop comes after the draft loads. | Restore the entry in the draft. To drop it on purpose, remove it from `evals/evals.json`. Then run again. |
| `cases-invalid` | A draft case lacks a required field, or a `task` case lacks the contract helper input in `files`. The message names the case id and the field or file. | Add the field, or add the helper input file to the `files` of each `task` case. Then run again. |
| `cases-too-few` | The holdout has fewer scored cases than the minimum of 6. | Add task cases in more families to the draft, then run again. |
| `search-failed` | Every search evaluation failed. | Check the model, then start a new run directory. |
| `search-faulted` | At least 4 search evaluations finished in this session and more than half failed, or the 5 newest failed in a row. A resume starts a new count. The message gives the failed counts by reason. | Wait for the provider, then resume the run in the same directory. |
| `holdout-faulted` | Provider faults left the gate with no holdout case that has a scored baseline and winner pair. The command exits nonzero. | Wait for the provider, then run again in the same directory. |
| `proposal-build-failed` | The host build of an accepted proposal fails with an error that is not a build rejection, such as a full disk. The paid reflection stays in the record. | Fix the cause in the message, then run again in the same directory. |
| `isolation-failed` | A Claude Code run loaded a skill that the candidate does not own, or its event stream hid the skill list. In a journey, the stop also covers a skill inventory change between turns. At most one sibling call (task plus judge) that was already running can finish after the fault. | Stop. Check the Claude config directory, then start a new run directory. |
| `harness-changed` | The harness executable, script, or role file differs from the frozen record, or a resume drops `--harness-config` or `--command`. | Restore the frozen harness and pass the same options, or start a new run directory. |
| `budget-exhausted` | The call budget or the deadline ran out. | Start a new run directory. |
| `out-unsafe` | `--out` is a symlink, is owned by another user, is open to other users, or holds a symlinked `run.lock`. | Use a private directory from `mktemp -d`. |
| `harness-missing` | The built-in `claude` or `codex` executable is not on `PATH`. A fresh run reports it in `host-not-ready` before case approval. A resume stops with this code. | Install the CLI or put it on `PATH`, then run again. |
| `run-config-differs` | A resume uses a different `--model`, `--judge-model`, `--harness`, `--edit`, `--repeats`, `--seed`, `--effort`, `--sandbox-read`, `--sandbox-seconds`, `--min-gain`, `--min-lower-bound`, `--family-budget`, `--max-token-increase-per-gain`, `--min-token-saving`, `--min-coverage`, `--max-quality-loss`, or target skill directory than the recorded run. A completed run checks each threshold flag that you pass in the same way. The message names the field. | Run again with the recorded value, or start a new `--out`. |
| `hidden-file` | The target skill holds a hidden file or directory. The stop comes after case approval. | Remove the hidden file, then run again. |
| `frozen-file-too-large` | A frozen file is over 16 MiB, or the frozen files together are over 64 MiB. The message names the file. | Remove the file from the skill, or shrink it, then run again. |
| `target-not-built` | `wedge.toml` lists a target, and its `scripts/NAME.pyz` is missing, is a symlink, or is not a file. | Run `wedge bundle` for the skill, then run again. |
| `wedge-config-invalid` | The `wedge.toml` of the skill is not a valid wedge config. The message gives the cause. | Fix `wedge.toml`, then run again. |
| `editable-build-file` | The contract `editable` list names `wedge.toml`, `uv.lock`, or a `.pyz` file. | Remove the name from `editable`. |
| `editable-needs-cli` | `--edit prose` meets explicit non-Markdown names in the contract `editable` list. | Run with `--edit prose+cli`, or remove those names from `editable`. |
| `site-layer-failed` | The host cannot populate the site layer for `--edit prose+cli`. The cause is a missing `uv`, a missing network, or a dependency closure that `uv` refuses. | Install `uv`, restore the network, or use `--edit prose`. Then run again. |
| `build-failed` | The host build of an own target fails. During the search, the candidate scores 0 and the feedback shows the reason. | Read the feedback. |
| `build-failed` (prepare time) | The seed build of an editable target fails in the sealing step, before the run starts. The message names the target. | Fix the target sources, or use `--edit prose` and remove non-Markdown names from `editable`. Then run again. |
| `editable-frozen` | The contract `editable` list names a frozen file, such as a binary file or a file over 262144 bytes. | Remove the name from `editable`. |
| `helper-frozen` | The contract helper names a frozen file. | Name an editable text helper, or name the `.pyz` of an own wedge target. |
| `reserved-name` | A skill file name starts with `@wedge/`, or it is `@new-cli`, `@hypothesis`, or `@notes`. | Rename the file. |
| `export-path-escapes` | A changed wedge source name leaves the repository root. Export writes no patch. | Keep the project inside the repository root, or edit with `--edit prose`. |
| `helper-missing` | An explicit `--edit prose+cli` finds no helper script to edit. | Add a helper script under `scripts/`, or use `--edit prose`. A run with no `--edit` uses prose and does not stop. |
| `helper-file-missing` | The contract helper or an editable file is missing from the target. | Restore the file, or fix the contract, then run again. |
| `prompt-components-missing` | The target has no editable Markdown file. | Add an editable Markdown file, then run again. |
| `contract-kinds` | The skill contract has no `task` kind and more than one kind, or the `journey` kind and the `journey` grader are not paired. | Read `The autoimprove contract` and `Journeys` below. |
| `brief-missing` | A journey case has no nonempty `brief`. | Add the brief to the case, then run again. |
| `contract-surface` | The contract `surface` block is malformed, or `editable` names the MCP config, an agent, or a companion file. An agent file also needs a frontmatter `name` equal to its file stem. A candidate also stops when it changes the MCP config text or an agent name. | Fix the surface or `editable` in the contract, or fix the agent name. Then run again. |
| `contract-surface-missing` | A file that the `surface` block names is missing from the skill, or the MCP config is not a JSON object with an `mcpServers` object. | Restore the file or fix the contract, then run again. |
| `contract-surface-unsupported` | The contract declares a `surface`, and the task role is not the Claude harness. | Run with `--harness claude`, or remove the `surface` block. |
| `journey-unsupported` | A journey case needs a task transport that holds a conversation, and the transport cannot. | Run with `--harness claude`, or remove the journey cases. |
| `contract-unapproved` | The skill contract has `status` `draft`. | Ask the user to approve the contract, then set `approved`. |
| `contract-unreadable` | `evals/autoimprove.json` is a symlink, a directory, or too large. | Replace it with a regular file, or remove it. |
| `contract-audit-unsupported` | The scored kind uses the `audit` grader. | Choose another grader for that kind. |
| `contract-capture-unsupported` | A kind declares `capture`, and the harness is not `claude`. | Run with `--harness claude`, or remove `capture`. |
| `contract-statistics-invalid` | The `statistics` value is not an object. Or it holds an unknown field, a negative value, or a value that is not a finite number (such as a bool or a string). Or `family_budgets` is not an object of numbers, or has an empty or blank name. Or `min_token_saving` is not `null` or a number above 0 and below 1. Or `min_coverage` is not a number from 0 to 1. | Fix the contract, then run again. |
| `contract-family-unknown` | A `family_budgets` name matches no family in the case draft. The stop comes after the draft loads. | Fix the family name in the contract, or add the family to the draft. |
| `run-schema-old` | The run directory comes from an older runner, or its recorded `statistics` lacks fields that a newer runner added. | Start a new run directory. |
| `export-into-target` | The export destination is inside the target skill directory. | Choose a destination outside the target skill directory. |
| `command-removed` | The command is `dataset`, `baseline`, `search`, or `evaluate`. | Use `run`, then `export`. |

Two close codes are warnings, not stops. They never arrive on stderr as `code`:

| Code | Meaning | Next step |
|---|---|---|
| `credential-rotated` | Another process replaced the login file during the run. The run used the new file. | Continue. |
| `credential-refreshed` | Claude Code refreshed the login during the run. The runner copied the refreshed login file back to the login file. | Continue. |

A run that completes shows the code in `close_warning` in the summary JSON on stdout, with the hint `no action needed`.
A run that fails records the code in `close_failure` in `run.json` and in a note on the error. The run stays resumable.

A `budget-unapproved` stop without `estimate` means the fixed cost exceeds the cap.
The cap is 200 calls and 7200 seconds, or 1000 calls and 28800 seconds for a run with a journey case.
Lower `--repeats` or the number of holdout cases in the draft, then run again.

## Draft the cases

Read the `facts` from the `cases-missing` stop, then the skill files that it lists.
Before the case draft, write one representative example. State the request, the expected target behavior, and the observable checks that refute it.
Show the example to the user with the case approval question.
Write `RUN/cases.draft.json`. It holds a nonempty list of cases.

When `<skill>/evals/evals.json` exists, the runner writes the seed cases to `RUN/cases.seed.json` (mode 0600). The stop names that file in `seed_file` and shows counts in `seed_summary`. Start the draft from the file.
The seed file holds a list of cases, one for each entry, in file order. The runner maps them and does not write the draft. To accept the seed unchanged, copy the file to `RUN/cases.draft.json`.
Each seed case has `source: skill`, the entry `name` as `id`, and the `prompt` as `request`.
An entry with `expected_skill: null` is a `near-miss` case.
An entry with no `assertions` is a `trigger` case. Every other entry is a `task` case.
A `task` case holds the fixtures in `files`, keyed by their listed paths, and an `expected` object.
The `expected` object holds `expected_output`, which defaults to an empty string, and `assertions`, which the entry must give.
Each fixture path starts with `evals/files/`. A path outside `evals/files/` stops the seed.
The runner stages each fixture at its `evals/files/` path in the workspace, so the request must name that directory.
When the entry lists no fixture, `files` is empty. Draft the starting fixture that the request needs.
A seed case has a `family` only when the entry has a nonempty-text `family` key. Otherwise set the `family` by the rule below.
Keep every seed case in the draft with its `id`, `kind`, and `request` unchanged. You can extend `files`, `expected`, and `family`.
When the draft drops a seed case or changes its `id`, `kind`, or `request`, the run stops with `cases-uncovered`. To drop an entry on purpose, remove it from `evals/evals.json`.
Then add cases until the draft meets the minimums below. To add journey cases, read `Draft journeys`.
The holdout split alone needs 6 scored cases, and a family never crosses splits, so a contract needs about 10 or more `task` cases.
A skill with no `evals/evals.json`, or with an unreadable one, has no seed file, and the stop removes an old one. Draft every case from the `facts`.

Each case has these fields:

- `id`: a unique name.
- `family`: a group name for related cases. A family never crosses splits.
  Give one family to the `task` cases that share a fixture or test the same behavior.
  Name the family for that behavior, for example `merged-changes`.
  A unique family for each case lets a near-copy of a search case reach the holdout.
- `kind`: `trigger`, `near-miss`, or `task`.
- `request`: the user request that the case sends.
- `files`: a map of path to text, for the starting fixture. A `task` case needs it.
  `text_map` refuses a path whose first part is `home`, `tmp`, `output`, `capture`, `answer.json`, `response-schema.json`, `AGENTS.md`, or `CLAUDE.md`.
  It also refuses `AGENTS.md` at any depth and any path that has a part that starts with a dot.
  To ship a refused name such as `AGENTS.md`, store it with a `.fixture` suffix, for example `AGENTS.md.fixture`.
  A suffix does not clear a hidden part, so store a file such as `.env` under a name without the leading dot.
  The runner writes fixtures verbatim, so the request must tell the task to rename the file.
  When the contract declares a helper, each `task` case must list the contract helper input in `files`.
- `expected`: the reference answer as JSON. A `task` case needs it.
- `source`: `skill` for a case that you drafted from the skill, or `session` for one from past analytics.

A `trigger` case is a request that should load the skill. A `near-miss` case is a similar request that should not.
The runner records `trigger` and `near-miss` cases as pending. It does not score them yet.
Draft the `task` cases with care, because only they decide the verdict.
Write at least three `task` families and at least six `task` cases for the holdout.
When the target contract has a `task` rubric, draft each `task` case so the rubric can score it.
Do not assign splits. Code assigns them from the seed.
When session analytics exists, run the Usage ceremony from `SKILL.md`.
Add extra cases from past sessions with `source: session`. Remove private data first.
The approval question lists every case, with its source, family, and split.
A change to the draft changes the case hash and asks the question again.

## Draft journeys

A journey case tests a skill that talks with the user over several turns. See `Journeys` for the case fields and the grader.
When the skill has no contract, run `scaffold` first. It makes no model call.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" scaffold --target /path/to/skill
```

The command writes `<skill>/evals/autoimprove.json` with `status: draft`, a `journey` kind, a generic gate, and a rubric.
It stops with `contract-exists` when the file exists, and it leaves that file unchanged.
Show the user the rubric and the gate. Set `status` to `approved` only after the user approves them.

Write each journey case with these fields:

- `brief`: the user goal and the facts that the simulated user may share. Only the simulator sees it.
  Hold back one fact. The agent must ask for it.
- `max_turns`: the agent turns that the flow needs. A journey costs at most `max_turns * 2 + 1` calls.
- `requirements`: optional requirement IDs that the case covers.
- `expected.must`: what the transcript must show, one observable item each.
- `expected.must_not`: what the transcript must not show, such as a self-approval or an edit of the target.
- `expected.questions`: the only questions that the agent may ask. The rubric subtracts points for any other question.
- `expected.final_stop`: optional. The stop or state in which the journey must end.

Make the cases hard. A family that the seed already passes adds no signal.
Give each family one trap:

- A fact that the agent must ask for. The request leaves it out.
- A wrong default that the agent must avoid.
- A change in the middle of the flow, such as a new constraint from the user.

The run stops with `cases-too-easy` when the seed already scores 0.9 or more on a family.
A run that sets `min_token_saving` does not stop, because a token-saving run expects saturated cases.
After a stop, draft harder cases for that family in a new run directory. Do not edit the stopped run directory.

When session analytics exists, mine past sessions for hard cases. Follow these steps:

1. Run the Usage ceremony from `SKILL.md`.
2. Select sessions of the target skill that show a user correction, a retry, a failed tool call, or a long multi-turn exchange.
3. Draft each case from the user request before the solution. Mark it `source: session`.
4. Redact home paths, emails, hostnames, and tokens from the request, the brief, and the files.
5. Never copy the solution of the session into `expected`. Write `expected` from the skill rules and the user need.

The approval question is the only check of the redaction. Read every case before you show the list.
Issue #132 plans a `prepare-cases` command for this work. Until then, follow these steps by hand.

## Read the gate verdict

The gate scores baseline and winner on holdout cases that no search step saw.
It scores each case `--repeats` times and averages the repeats.
It then takes the mean of the paired case deltas and its standard error (SE).

The SE has a floor from the repeat noise inside each case (`se_floor`). The gate uses the larger of the raw SE and the floor.
For each case and arm, the gate takes the SE of the repeat mean. One scored repeat gives an SE of 0.
`se_floor` is `sqrt(sum(se_baseline² + se_winner²)) / n` over the `n` cases that remain. The floor never lowers the SE.
The zero-SE rule below applies only when the floor is also 0.

A provider fault on one holdout repeat (`OSError`, `ValueError`, or `RuntimeError`) records an outcome with status `evaluation-failed`, and the gate goes on.
A coded fault still stops the run. A resume runs each failed repeat again, and the newest outcome of a repeat wins. The spend stays inside the approved budget, else the run stops with `gate-budget-exhausted`.
Only `evaluation-failed` leaves a repeat unscored. A rejected candidate, a capture failure, and a grader zero score 0.
The gate drops a case with no scored repeat on an arm. `n_shared` is the number of cases that remain.
A winner equal to the baseline scores no case, so its `n_shared` is 0. That `inconclusive` verdict with `winner-equals-baseline` is legitimate when the search scored its evaluations.

A failed search evaluation is unscored too.
It adds no score to a validation mean, no stall observation, and no reflection feedback.
A candidate with no scored validation case never beats the seed.
GEPA still receives 0.0 for it, because GEPA needs a number. The session ignores that value when it picks the winner.
`search.scored`, `search.failed`, and `search.failed_by_reason` in the summary and the export report count the search evaluations. The `reason` of a failure is `timeout`, the reason that the adapter names (such as `rate-limited` or `usage-limited`), or `error`.
With fewer than 2 shared cases, the verdict is `inconclusive` and `se` is the SE floor.

The gate record also holds `moves`: the case ids that `fixed`, `broke`, or left `unresolved`.
A move is resolved when the absolute mean delta of the case exceeds `max(2·sqrt(se_baseline² + se_winner²), 1e-9)`.
A resolved positive move is `fixed`, a resolved negative move is `broke`, and the rest are `unresolved`. Case ids are hashes, so no case text enters the record.

- `promote`: the mean delta exceeds 2·SE and passes the floors. When the SE is 0, every case delta must be positive.
- `promote-cheaper`: the mean delta is from 0 to 2·SE, and `delta - 2·SE` is at least `-max_quality_loss`. The task tokens drop by more than 2·`token_se` and by at least `min_token_saving`. See "The token rule".
- `reject`: the mean delta falls below -2·SE.
- `inconclusive`: any other result, or fewer than two holdout cases, or a winner equal to the baseline.

The coverage rule runs before the floors and the token rule. Coverage is `n_shared` divided by the number of planned holdout cases.
When coverage is below `min_coverage`, the verdict is `inconclusive` with reason `low-coverage`, even if the deltas would promote or reject. The record keeps `delta` and `se`.
The family rule still runs after it, and a family breach still forces `reject`. A `min_coverage` of 0 turns the rule off.
A provider fault that leaves holdout cases unscored lowers coverage, so it cannot become a promote or a reject.
A winner equal to the baseline skips the coverage rule. Its `n_shared` is 0, but its only reason is `winner-equals-baseline`.

The floors apply only to a result that would promote. That result needs `delta >= min_gain` and `delta > 2·SE + min_lower_bound`.
When the SE is 0, the second test uses 2·SE = 0.
A result that fails a floor becomes `inconclusive`. A floor never creates a `reject`.
With both floors at 0, the verdict equals the built-in rule.

The family rule runs after the floors. It uses the holdout cases, grouped by `family`.
A family needs at least two holdout cases. For that family, the gate takes the mean delta and its SE (SE_f).
The budget is the `family_budgets` value for that family, else `family_budget`. A family with no budget has no family rule.
The family breaches when `mean + 2·SE_f` is below `-budget`. When SE_f is 0, the family breaches when `mean` is below `-budget`.
A breach turns any verdict into `reject`. This also applies to `promote`. Each breach adds the reason `family-regression:<name>`, sorted by family name.
The rule skips a family with fewer than two holdout cases, whatever its budget. A `family_budgets` name with no holdout case counts as skipped too. `skipped_families` lists each skipped family, sorted by name. A skipped family never changes the verdict.
The gate object always holds `skipped_families`. Without a budget, the verdict stays the same.

### The token rule

The token rule is off while `max_token_increase_per_gain` and `min_token_saving` are both `null`. Then no token rule runs. The verdict, the reasons, and the reflection prompt match a run without these fields.
When a field is set, the rule runs after the floors and before the family rule.
The task tokens of one holdout outcome are `input_tokens - cached_input_tokens + output_tokens`.
Both adapters count the cached input inside `input_tokens`, so the charge covers only the uncached input and the output.
A count that is missing, a bool, or negative makes the task tokens unknown. A `cached_input_tokens` value above `input_tokens` does the same.
Judge usage and journey simulator usage never enter the charge. The outcomes keep all counts for the report, and a journey outcome records the simulator share in `simulator_usage`.
For each case, the token change is `mean(winner task tokens) / mean(baseline task tokens) - 1`, with the mean over the repeats.
The gate takes the mean of the case token changes (`token_delta`) and its SE (`token_se`).
When every case token change is equal, `token_se` is 0 and `token_delta` is that change.
Each token threshold treats two values as equal when they differ by a relative 1e-9 or an absolute 1e-12.
The score rule, the floors, and the family rule compare exactly. ADR-002 and the pinned false-promotion rates require this.

- `token-cost`: a `promote` becomes `reject` when `token_delta` exceeds `max_token_increase_per_gain * delta`. The reason is `token-cost`.
- `promote-cheaper`: this needs `min_token_saving` and `max_quality_loss`. The score result must be `inconclusive` with a mean delta from 0 to 2·SE.
  A mean delta below 0 never promotes cheaper. A result that a floor turned into `inconclusive` is outside the band.
  `delta - 2·SE` must be at least `-max_quality_loss`. A `null` `max_quality_loss` turns the verdict off.
  `-token_delta` must exceed 2·`token_se` and must be at least `min_token_saving`.
  The floors do not apply, because the verdict claims no gain.
- `unknown-usage`: a holdout outcome has unknown task tokens, or a case has a baseline mean of 0.
  An active rule with no task tokens at all gives the same result.
  Then a `promote` becomes `inconclusive` with this reason.
  A band result with `min_token_saving` set gets the same reason and stays `inconclusive`.

A family breach still turns any verdict, including `promote-cheaper`, into `reject`.
A `promote-cheaper` winner is advisory. Export it and report it, but do not apply it as you would a `promote` winner. The user decides whether to apply it.
With a token field set, the winner choice breaks a validation-mean tie in favor of fewer mean task tokens. This holds for the best candidate and for the seed.
A candidate with unknown task tokens, or equal task tokens, keeps the usual winner. In a tie, that is the incumbent.
The reflection prompt then also says: "Prefer fewer tokens when correctness is equal."

The winner choice treats two validation means as equal when they differ by a relative 1e-9 or an absolute 1e-12.
This holds in every run, with or without a token field.
Without a token field, the seed wins a tie.

### Reasons and reporting

The gate verdict holds `reasons`, a list of the rules that fired. The floor reasons are `min-gain` and `min-lower-bound`. The family reason is `family-regression:<name>`. The token reasons are `token-cost` and `unknown-usage`. The coverage reason is `low-coverage`.
A plain verdict has an empty list. A winner equal to the baseline has the reason `winner-equals-baseline`.
The summary and the export report show `reasons`, `skipped_families`, `token_delta`, and `token_se` in the `gate` object. The token fields are `null` while the token rule is off or the usage is unknown.
The summary and the export report also show `statistics`, the frozen thresholds of the run.
The summary reports the delta, the SE, the case count, and the repeats.
Small runs often report `inconclusive`. That result is honest: the data cannot separate the winner from noise.
Report the verdict as it is. Never promote an `inconclusive` winner by hand.
Report each of the three claims with its status and evidence: `runner`, `export`, and `improvement`.
Never describe an `inconclusive` claim as an improvement.
Apply a winner only when `claims.apply` is true. A `promote-cheaper` winner stays advisory: report it and do not apply it. The user may apply it by hand outside autoimprove.
Never report a delta without its SE and case count.

The summary of a `complete` run and the export report hold a `claims` block.
The block holds `runner`, `export`, `improvement`, and `apply`.
`runner` has `status`, `checks`, and `calls`. The checks are `preflight`, `holdout-isolated`, `holdout-coverage`, `calls-within-approval`, and `calls-within-bound`.
Each check is `pass`, `fail`, or `unknown`. The status is `refuted` on any `fail` and `inconclusive` on any `unknown`.
`export` comes from `replay`. It holds `status`, `reasons`, `patch_digest`, and `replay` with `cases`, `mean_delta`, `se`, and `per_case`.
The `export` claim is `inconclusive` with the reason `replay-missing` when a completed run has no replay. See "Replay" for the other reasons.
`improvement` comes from the gate verdict: `promote` is `supported`, `reject` is `refuted`, and `inconclusive` is `inconclusive`.
`promote-cheaper` is `inconclusive` with the reason `cheaper-not-better`.
`improvement` also holds `verdict`, `reasons`, `delta`, `se`, `cases`, `repeats`, and `families`. Each family gives `cases` and `mean_delta`.
`apply` is true only when all three claims are `supported` and the verdict is `promote`.
`python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --simulate` prints the false-promotion counts and rates of the gate. It makes no model call.
The keys `false_promotions_2se` and `false_promotions_one_holdout` are integer counts; each rate is its count divided by `trials`.
The counts cover `promote` only.
The `cheaper_loss_<loss>_margin_<margin>` keys are `promote-cheaper` rates. Each key names the true loss per case and the `max_quality_loss` margin, in hundredths of a score.
In each, the selected winner costs half the task tokens and `min_token_saving` is 0.3. The seed, the 2000 trials, and the 10 cases match `rate_2se`.
With a true loss of 0.05, the rates are 0.0005, 0.0035, and 0.016 at margins 0, 0.02, and 0.05. All three are below the 0.06 target of `promote`.
Without the margin check, the same winner got `promote-cheaper` in 0.346 of the trials.
`cheaper_loss_0_margin_5` is 0.0285. That winner has no true loss, and the verdict still fires.
`--simulate --metric NAME` prints only that one number, with no JSON. It computes only the figure that it names, so a selector costs well under the full report. Without `--metric`, the output keeps its earlier keys. The power keys follow them, and the `cheaper_loss_*` keys come last.
The names are `false_promotions_2se`, `false_promotions_one_holdout`, `rate_2se`, `rate_one_holdout`, the four `cheaper_loss_*` names, and the power names below.
Each metric in `packages/skillz/ratchets.json` names its own selector in its `command`, so `ratchet.py measure -- <command>` reads one deterministic number.
The `skillz-pytest` job fails when a metric in that file passes its threshold. It checks every metric in the file.
The one-holdout count is a reference ratchet. It models the select-and-gate rule of skill-creator, not the production gate. Its threshold is 1623.
It stays in `ratchets.json` because the value is exact for the seed and costs about half a second. A rise above 1623 fails the job. A fall passes, and nothing tightens the threshold until a person runs `ratchet.py tighten`.

#### Gate power

The false-promotion count alone passes a gate that never promotes. The power figures close that gap.
For each true effect of 0.1, 0.2, and 0.3, the simulation draws each candidate case chance as `clip(gauss(base + effect, 0.10))`. The clip to [0, 1] removes part of the shift near the ceiling.
So the realised mean gain is below the nominal effect, at about 0.097, 0.188, and 0.271. The simulation then runs the production gate, with the SE floor.
It uses the seed, the 10 cases, the 3 repeats, and the 2000 trials of the false-promotion simulation. It skips the validation search.
The keys `true_promotions_effect_10`, `_20`, and `_30` are integer counts of `promote`. The keys `power_effect_10`, `_20`, and `_30` are the rates, each its count divided by `trials`.
Power is ratcheted. The metrics `gate-power-effect-10`, `-20`, and `-30` in `ratchets.json` use `direction: higher`, with the thresholds 191, 628, and 1240.
The figures are exact for the seed. A power gain passes, and nothing raises the threshold until a person runs `ratchet.py tighten`. A later change cannot buy fewer false promotions with lost power without a person lowering a threshold in review.
A rule that never promotes has power 0, so it fails these ratchets.

## Wedge targets

A skill can own wedge targets. Each target is a section of the `wedge.toml` in the skill root.
Each target ships as `scripts/NAME.pyz`.
The runner reads each own `.pyz` as frozen bytes. If a `.pyz` is missing, the run stops with `target-not-built`.

### Frozen bytes

A frozen file is a file that no proposal edits. The runner keeps its bytes as they are.
A file is frozen when it is binary, holds a NUL byte, is not UTF-8, or is over 262144 bytes.
A valid UTF-8 file with a NUL byte is frozen. This changes the identity of such a file from earlier runs, by intent.
A `.pyz` file is never editable. A binary `.pyz` is frozen, including one the skill does not own.
Frozen bytes do not count against the 1,000,000-character text limit.
A frozen file may be up to 16 MiB. All frozen files together may be up to 64 MiB.
A larger file stops the run with `frozen-file-too-large`.
The candidate identity includes a digest of each frozen file.
The run directory keeps the frozen files in `frozen/`. A changed frozen file stops a resume with `run-record-tampered`.

### Editable sources

`--edit prose+cli`, the default, edits sources. `--edit prose` and the prose fallback keep every own target frozen.
The runner adds the sources of an own target in this order:

1. The target whose `.pyz` the contract helper names.
2. Each other target for which `SKILL.md` names `scripts/NAME.pyz`, in `wedge.toml` order.

A target joins only while the text total stays within the 1,000,000-character limit.
The first target that does not fit stays frozen, and so does every later target.
A target also stays frozen when one source file is binary, is not UTF-8, or is over 262144 bytes.
The files `wedge.toml`, `uv.lock`, and every `.pyz` are never editable.
A contract helper path may name an own `.pyz`. The path then does not stop with `helper-file-missing`.
A project outside the repository root keeps its targets frozen.
A git-ignored first-party file freezes its target.
A source that a frozen target or a nested skill target also builds from is never editable.
A target left with no source is frozen.
The editable wedge sources together may hold up to 100,000 characters. This is the wedge edit limit.
A source appears in the candidate as `@wedge/` plus its path relative to the repository root.
The wedge overlay uses paths relative to the project. Do not mix the two forms.

### Host rebuild

A site layer is a directory of installed dependencies. The host builds each target over it.
The dependency groups are the `groups` that `wedge.toml` names for a target.
The run populates one site layer for each project and dependency group set of the editable targets.
This is the only step that needs the network and `uv`. A missing `uv` stops the run with `site-layer-failed`.
When a candidate changes a source, the host rebuilds that target before the candidate check.
The build only copies and zips source. It never imports or runs candidate code.
The new `.pyz` replaces the frozen bytes of that target for the candidate.
A failed build rejects the candidate. It scores 0, and the feedback shows the reason `build-failed`.
At the holdout gate, a winner that no longer builds stops the run with `build-failed`.
The seed `.pyz` comes from a build of the seed sources, not from the file on disk.
A rebuild stages first-party files that no proposal edits, such as a data `.pyz`, from the seed.
A resume reopens the site layers. It stops with `site-layer-drift` when `uv.lock`, the dependency groups, or `wedge.toml` changed.
It also stops with that code when a recorded layer cannot be reused or the recorded targets no longer match `wedge.toml`.

### New target (`@new-cli`)

Under `--edit prose+cli`, a proposal can add one new fromargs wedge target. The component is `@new-cli`. Its seed value is empty.
The runner offers `@new-cli` only when all of these conditions hold:

- The skill has a `wedge.toml`, and one of its targets includes the fromargs package.
- The `wedge.toml` has no top-level `source_paths`.
- The populated site layer for the project with no groups provides `cyclopts`. The fromargs include provides `fromargs`.
- The skill directory lies inside that project, and the project lies inside the repository root.
- Each first-party file of the fromargs include is at most 16 MiB.

When one condition fails, the run has no `@new-cli` component.
When no editable target uses the no-group layer and `uv` or the layer population fails, the run drops the component and continues.
The run records the reason as `new_cli_dropped` in the own-targets record.

The value is TOML with exactly two keys:

```toml
name = "count-words"
module = """...Python source with a main function..."""
```

`name` matches `[a-z][a-z0-9-]{1,39}`. The package name is `name` with each hyphen changed to an underscore.
`module` is Python source of at most 262144 characters. The whole value is at most 524288 characters.
The host writes `module` to `src/<package>/__init__.py` in the skill and builds `scripts/<name>.pyz`.
The host appends a `[[target]]` with `entry = "<package>:main"` and the same fromargs include to `wedge.toml`.
A single-target `wedge.toml` becomes the multi-target form. The host drops its comments.
The new target sets `groups = []`. It never takes the dependency groups of another target. The base dependencies provide `cyclopts`, and the fromargs include provides `fromargs`.
The preparation step populates the site layer for the project with no groups. This step needs the network.
The host reads `module` with `ast`. It never imports or runs the module.
The module may import only the standard library, its own package, fromargs, and the site layer.
The module must define a top-level `main` (a function or an assignment). The entry is `<package>:main`.
The static check does not see dynamic imports (`__import__`, `importlib`). They fail only inside the sandbox.

The host rejects a bad value. The candidate scores 0, and the feedback shows the code as `reason`:

| Code | Cause |
|---|---|
| `new-cli-malformed` | The value is not valid TOML or nests too deeply. `module` is not a string. |
| `new-cli-second-target` | The value holds a `[[target]]` table or a list of names. |
| `new-cli-extra-key` | The value has a key besides `name` and `module`. |
| `new-cli-missing-key` | The value lacks `name` or `module`. |
| `new-cli-bad-name` | `name` is not a string, does not match the pattern, its package name is not an identifier, or it is a Python keyword. |
| `new-cli-module-size` | The value or `module` is over its size limit, or `module` is empty. |
| `new-cli-name-collision` | `name` matches an existing target. |
| `new-cli-package-collision` | The package matches a source package, an include package, a standard library module, a top-level module of the site layer, or a skill path. |
| `new-cli-undeclared-import` | `module` imports a package outside the allowed set. |
| `new-cli-syntax` | `module` does not parse or nests too deeply. |
| `new-cli-no-main` | `module` defines no top-level `main`. |
| `new-cli-unavailable` | The skill cannot take a new target. |
| `build-failed` | The host build of the new target fails. |

The `report.json` of an export holds `new_cli` with `target`, `package`, and `unused_helpers`.
An unused helper is a `scripts/*.py` path that the seed `SKILL.md` names and the winner `SKILL.md` does not name.
The helper file stays in the skill. Ask the user whether to delete it.

### Fromargs aim

Under `--edit prose+cli`, the reflection prompt names fromargs as the default for edits to wedged Python sources.
The fromargs rule goes only to a run that offers a `@wedge/` source or `@new-cli`.
The proposer can change the command-line interface of a script. Every caller and every `SKILL.md` or reference mention must change to match.
It cites the fromargs rules: one parser, JSON errors on stderr, and stdout only for results.
A usage error exits with code 2. A failed input contract exits with code 3.
A non-Python helper keeps its language.
A Python helper may be a stdlib `argparse` helper only when the skill has no `wedge.toml` that can build a fromargs target.
The prompt describes `@new-cli` only when the run offers it.
The `prose` prompt does not change.
Run `audit-facts` first to see the wedge state of the skill.

### Reflection prose rule

Every reflection prompt asks for ASD-STE100 prose.
The limit is 20 words per procedural sentence and 25 words per descriptive sentence.
This matches the inspector tiers and the prose workflow. ADR-015 records the choice (issue #153).
The prompt text lives in `search/_proposer.py`. A change to it starts a new run.

### Edit budget and run status

Every reflection prompt states an edit budget and a run-status block after its instructions.
The progress `p` is the search calls spent over the search allowance, clamped to 0 through 1.
The allowance is `estimate.metric_calls` less the overshoot margin of the last search iteration. It is the GEPA call budget. The last iteration can pass it by up to the overshoot margin.
The budget is `floor(1 + 5 * 0.5 * (1 + cos(pi * p)))`. It falls from 6 edits at `p = 0` to 1 edit at `p = 1`.
An edit is one block of consecutive changed lines, found with `difflib.SequenceMatcher` over the lines of one component.
The count adds the blocks of all changed components.
The run-status block gives the proposal number, the search calls spent of the allowance, and the whole percent (integer math, rounded down).
It also gives the stall streak from `### Stall stop`.
The budget sentence reads "1 edit" when the budget is 1.
From `p = 0.85` it adds: "This may be the last proposal, so prefer one small, safe change."
The numbers come from the run record. They never come from case content.
The budget is advisory. The runner never rejects a proposal for its size.
`summary` and `report.json` carry `search.proposal_over_budget`: the count of proposals above the budget stated in their prompt, including proposals that the leakage check later rejects.
The count shows whether enforcement is worth the wasted reflection calls.

### Attempt history

The runner records one attempt for each reflection call. `search.attempts` holds them, oldest first.
An attempt has `index`, `parent`, `candidate`, `components`, `hypothesis`, `fixed`, `broke`, `validation_delta`, `accepted`, `rejected`, and `rejected_coded`.
`rejected_coded` is true when `rejected` is a known code.
`parent` and `candidate` are candidate identities. `components` is a digest of the proposed component texts. No attempt holds case text or component text.
`fixed` lists the train case ids that the proposal scores higher than its parent. `broke` lists the ids that it scores lower.
The lists cover the train cases that the proposer saw in its feedback. For a case with more than one outcome, the newest outcome that did not fail counts.
`validation_delta` is the validation mean of the candidate minus the validation mean of its parent.
It is null unless both sides have a complete validation set. An evaluation-failed outcome is skipped. The newest scored outcome of a (candidate, case) counts. A case with no scored outcome is unscored, so validation_delta is null.
An attempt with no train score yet has empty lists and a null `validation_delta`. The runner fills these fields from later outcomes.
`accepted` is true when the proposal produced a candidate. `rejected` holds the reason when it did not:
`reflection-shape`, the leakage or build reason of the host check, or `proposal-build-failed`. A rejected attempt has a null `candidate`.
`changed` lists the component names that the proposal changed.
The runner rebuilds the attempts from the recorded outcomes. A resume keeps them and continues the index.
The runner does not support a run that started before this record.

The reflection prompt lists the newest 8 attempts, newest first, within 4000 characters.
It also lists the protect set: the train case ids on which the best candidate by validation mean scores 1.0.
The seed is the best candidate when it has the highest validation mean, including when no candidate is validated. The set holds at most 30 ids.
The prompt tells the proposer not to re-introduce a change that broke a case.
It also tells the proposer not to copy a shown case id into a component or into `@hypothesis`.

The reflection answer carries an `@hypothesis` key. Its value is a text of at most 200 characters, or null.
The runner stores a missing or null value as null. It drops a hypothesis over 200 characters as `hypothesis-too-long`.
It drops a value that is not text as `hypothesis-not-text`.
The leakage check reads the hypothesis in its own call. A leak drops the hypothesis and records the rule. The proposal stays admitted.
The key `@hypothesis` never enters a component. It never counts in `leakage_rejections`.
`search.text_dropped` counts the dropped hypotheses for each rule.
The export `report.json` and the `run` summary keep these attempt fields: `index`, `parent`, `candidate`, `fixed`, `broke`, `validation_delta`, `accepted`, and `rejected`.
They drop `hypothesis`, `components`, and `rejected_coded`. They map an uncoded `rejected` text to `rejected`. `run.json` keeps all fields.

### Rejected memory and proposer notes

The reflection prompt lists the rejected memory after the attempt history.
The rejected memory holds the newest 6 attempts with a deterministic reason, newest first. Each one shows its hypothesis and its reason.
The reason is the coded rejection: `reflection-shape`, `candidate-leakage-rejected`, or the code of a host check.
An admitted attempt whose `validation_delta` is not above `min_gain` has the reason `no-gain`.
An attempt with an unknown result stays out: an unknown `validation_delta`, an uncoded rejection, or `proposal-build-failed`.
The section ends with: "Propose a change that is structurally different from each rejected approach."
The section is absent when no attempt qualifies.

The reflection answer also carries an `@notes` key. Its value is a text of at most 500 characters, or null.
The notes are the proposer's own text for its later proposals. The runner does not check them for truth.
The runner drops notes over 500 characters as `notes-too-long`, and a value that is not text as `notes-not-text`.
The leakage check reads the notes in their own call. A leak drops the notes and records the rule. The proposal stays admitted.
Each reflection outcome records `notes` and `notes_dropped`. `search.text_dropped` counts the dropped hypotheses and notes together, by rule.
`search.journal` in `run.json` lists the kept notes, oldest first, with the attempt `index`.
The prompt shows the newest notes in a separate section after the framework facts, within 2000 characters. The oldest note drops first.
The runner rebuilds the rejected memory and the journal from the recorded outcomes, so a resume keeps both.
The export `report.json` and the `run` summary drop `search.journal`.

### Cross-run history

The runner keeps a private history of attempts across runs of one target. It is on by default. `run --no-history` turns it off.
The first run records the choice in `run.json` as `history`. A resume that changes it stops with `run-config-differs`. A resume that omits the flag keeps it.
The store is `$XDG_STATE_HOME/skillz/history/<key>.jsonl`, or `~/.local/state/skillz/history/<key>.jsonl` when `XDG_STATE_HOME` is unset, empty, or relative.
The key is a digest of the contract skill name and the resolved target root. The directory has mode 0700, and the file has mode 0600.
When the search ends, the runner writes one line for the run: its `run_id`, a timestamp, the `compatibility` block, and its attempts.
The write replaces the store through a temporary file under a lock in `<key>.jsonl.lock`, so a kill leaves the old store whole.
A resume that stopped between the search and the store write writes the line. The store keeps the newest 20 runs.
At search start, the runner loads only the earlier runs whose compatibility fields all match. `search.history` reports:
`enabled`, `runs` (the loaded run ids), `excluded` (each run id with its differing fields), `skipped_lines`, and `warnings`.
The reflection prompt shows the earlier attempts after the rejected memory: at most 8, newest first, within 4000 characters. Run 1 is the newest earlier run.
Earlier attempts never enter the search, never become the winner, and never count toward the stall stop.
The store holds no request, expected, label, or holdout data. It holds candidate identities, digests, train case ids, and the hypothesis that passed the leakage check.
The load skips and counts a corrupt line. The store keeps a rejection only as a code. A store that cannot be read or written, or that has the wrong mode, gives the warning `history-unreadable`. The run result does not change.
ADR-020 in `docs/agents/decisions/skillz-autoimprove.md` records the decision.

### Stall stop

The search stops early when its proposals stop improving the validation mean.
Each proposal gives one observation after GEPA scores its candidate.
An observation is a gain when the candidate's validation mean is above the best earlier mean by more than `min_gain`.
The seed counts as an earlier candidate.
A proposal that the leakage check or the build rejects gives no gain.
A candidate that does not complete the validation cases also gives no gain.
An observation is unknown when an evaluation of its candidate fails before the candidate completes the validation cases.
A host build error stops the run with `proposal-build-failed`, and its proposal also gives an unknown observation.
A gain sets the streak to 0. Each other known observation adds 1. An unknown observation changes nothing.
The run-status block states the streak: "Proposals since the last validation gain: N."
From a streak of 3, the prompt advises a different kind of change.
From a streak of 5, it advises a different component or section.
At a streak of 8, the search stops, and `search.reason` is `converged-stalled`. The holdout gate still runs.
Fewer than 4 known observations never give advice or a stop.
The stall stop is always on. A budget stop keeps its own reason.
When the search deadline, not the call cap, ends the search with more than half of the search call budget unspent, `search.reason` is `time-exhausted`.
The `search` record then holds `seconds_by_role` for `task`, `reflection`, `judge`, and `failed`.
A search that uses its allowance without a stop ends with `validation-mean`.
GEPA reflects only on a train minibatch where the parent scores below 1.0.
When the parent scores 1.0 on every minibatch of two train epochs in a row, the search also ends with `validation-mean`.
A repeated evaluation of a scored candidate and case is free and does not use the allowance.
If the search ends with most of its allowance unspent, the train cases are too easy; add harder train cases.
The runner rebuilds the observations from the run record, so a resumed run gets the same streak.
A resumed run that already reached a streak of 8 skips the search.
ADR-016 in `docs/agents/decisions/skillz-autoimprove.md` records the thresholds.

### Exploration

From a streak of 5, before the stop at 8, the runner picks a focus component for the next proposal.
The focus is the editable component with the fewest attempts in the cohort: this run and the compatible earlier runs of `### Cross-run history`.
An attempt counts once for each component that it changed. A tie goes to the first name in lexical order.
The choice stays inside the frozen editable set. A reserved name, such as `@new-cli` or a `@wedge/` source, is never a focus.
The prompt names the focus in place of the generic advice: "Change NAME, because earlier attempts changed it least."
The proposal may still change other components. Exploration never bypasses validation and never forces a change.
The reflection outcome and its attempt record `focus` and `focus_reason` (`under-tested`) whenever the shift names a focus.
Each attempt also records `changed`: the component names that the proposal changed.
An unknown observation never adds to the streak, so it never triggers exploration. A real gain resets the streak and ends the focus.
The choice reads only component names and attempt counts. It never reads case text or holdout data.

### Replay

The replay checks that the exported patch reproduces the evaluated winner. It writes the `replay` record to `run.json`.
The phase `replayed` follows `gated` and precedes `complete`.
The replay applies each patch with one `git apply` to a clean copy under the run directory.
The `git apply` call runs in a hermetic environment: it uses the resolved ceiling and no global or system git config.
The replay checks byte equality with the evaluated winner.
It rebuilds the candidate from the copy and checks its identity.
Then it scores the candidate once on each holdout case through the harness.
The `replay` record in `run.json` holds `reasons`, `patch_digest`, `cases`, `per_case`, `mean_delta`, `se`, `tolerance`, and `detail`.
The summary and the export report omit `detail`, because it can hold git and build error text.
The report maps the reasons to the status of the `export` claim.
The status is `refuted` for the reasons `patch-apply-failed`, `patch-bytes-differ`, `replay-identity-differs`, and `replay-contract-rejected`.
The status is `inconclusive` for the reasons `winner-equals-baseline`, `replay-budget-exhausted`, `replay-evaluation-failed`, `replay-score-drift`, `replay-build-failed`, and `replay-not-loaded`.
The reason `replay-not-loaded` marks a replay outcome with `loaded` false.
The `detail` of `replay-budget-exhausted` holds `needed` and `left`, or a `shortfall` of `deadline` or `calls`.
The `detail` of `replay-build-failed` holds a `code` and an `error`.
Otherwise the status is `supported`.
A replay call can time out or hit another provider fault, such as `rate-limited` or `usage-limited`.
The runner records an `evaluation-failed` outcome with `arm: replay` and the `reason` of the fault. That outcome is unscored: it adds no delta and never counts as a 0.0 score.
Such a fault gives the reason `replay-evaluation-failed`, so the `export` claim is `inconclusive`, never `refuted`.
A deadline or call-cap stop during the replay gives `replay-budget-exhausted`.
Replay model calls use the existing roles: `task` for harness calls and `judge` for judge calls. There is no `replay` role.
They count in `calls` and in `calls_by_role`, so `calls` still equals the sum of `calls_by_role` after a resume.
For each case, the delta `d` is the replay score minus the mean winner holdout score.
The replay reports drift when `mean(d) + 2·SE < -min_gain`. An unset `min_gain` counts as 0.
A missing git or a git timeout stops the run with the retryable code `replay-git-unavailable`. Fix git, then resume the run.
A run in the phase `gated` with unscored replay cases that resumes after its deadline records `replay-budget-exhausted` without a model call.
The gate keeps the unspent replay reserve for the replay.

## Export

Run `export` only after the run reports the phase `complete`.
Export refuses a patch whose digest differs from the replayed `patch_digest`. It writes nothing in that case.
A completed run without a replay gives the `export` claim `inconclusive` with the reason `replay-missing`.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" export "$OUT" --out "$OUT/export"
```

Export is write-only. It writes each non-empty patch (`candidate.patch`, `wedge-sources.patch`) and a redacted `report.json` to a new directory.
Its result and `report.json` list the written patch files under `patches`.
Export writes no empty patch file.
When the winner files equal the seed files, export writes only `report.json`. Its result has `patches: []` and `reason: "winner-equals-baseline"`.
Tell the user that the run found no change to apply.
It never applies or installs a patch, and it never changes the target skill.
Show the user each patch and the three claims. Apply a patch only when `claims.apply` is true.
`candidate.patch` is a plain unified diff with paths relative to the skill directory.
Apply it from the skill directory with `git apply` or `patch -p1 -E`. Added and deleted skill files appear in it.
The `-E` flag makes the macOS `patch` remove a deleted file instead of leaving it empty.
When a candidate changes wedge sources, export also writes `wedge-sources.patch`.
It has git headers and paths relative to the repository root. Apply it from the repository root with `git apply`.
Only a changed wedge source must lie inside the repository root.
A changed source path outside the repository root stops export with `export-path-escapes`.
A skill-only change exports outside a repository.
`doctor --harness claude` runs the free host checks without a run.
`self-test --preflight-only --model MODEL` checks the harness without a run.
Pass `--harness-config FILE` to check a custom command adapter. The harness reference that `SKILL.md` lists describes it.


## The autoimprove contract

A contract is optional. It tells the runner how to grade the cases.
It lives at `<skill>/evals/autoimprove.json`. The shipped `skills/skillz/evals/autoimprove.json` is the example.
Without it, the runner grades each `task` case with a judge against its `expected` answer.
To start a journey contract, run `scaffold`. See `Draft journeys`.

The contract has these fields:

- `schema_version`: integer `1`.
- `status`: `approved` or `draft`. A draft stops the run.
- `skill`: the skill directory name.
- `invocation`: the request that calls the skill. It supports `{skill}` and `{path}`.
- `kinds`: a map of case kind to `{grader, argv?, rubric?, capture?, pillars?}`.
- `helper` (optional): `path`, `input`, and `fixtures` for a bundled helper script.
- `editable` (optional): relative paths that search can change.
- `surface` (optional): the tool surface of the task role. See "Journeys".
- `statistics` (optional): promotion thresholds for the holdout gate. See "Set promotion thresholds".

A run needs a contract with a `task` kind or with exactly one kind. Otherwise it stops with `contract-kinds`.
It stops with `contract-unapproved` when `status` is `draft`.
Unknown fields and malformed values stop the run.

## Set promotion thresholds

The `statistics` object in the contract tightens the `promote` rule. It never loosens it.
Every field except `min_token_saving` and `max_quality_loss` only tightens `promote`. Only `min_gain` and `min_lower_bound` are floors.
`min_token_saving` adds the verdict `promote-cheaper`. That verdict claims no gain and needs `max_quality_loss`. A larger `max_quality_loss` loosens it.
A contract without it keeps its identity hash and the built-in rule.
The object accepts these fields:

- `min_gain`: the smallest mean holdout delta that can promote. The default is 0.
- `min_lower_bound`: the margin that the delta must clear beyond 2·SE. The default is 0.
- `family_budget`: the regression budget of every holdout family. The default is `null`, which turns the family rule off.
- `family_budgets`: an object that maps a family name to its own budget. It overrides `family_budget` for that family. A flag cannot set it.
- `max_token_increase_per_gain`: the largest token change that one unit of score gain can buy. The default is `null`, which turns this check off.
- `min_token_saving`: the smallest token saving, as a share of the baseline task tokens, that can promote a winner with a mean delta of at least 0. The default is `null`, which turns `promote-cheaper` off. A run with this field set records the headroom scores. It does not stop with `cases-too-easy`, because a cheaper winner expects solved cases.
- `min_coverage`: the least share of planned holdout cases that must be scored on both arms, from 0 to 1. The default is 0.6. Below it the verdict is `inconclusive` with reason `low-coverage`. 0 turns the rule off.
- `max_quality_loss`: the largest plausible quality loss, as a score per case, that `promote-cheaper` accepts. The verdict needs `delta - 2·SE >= -max_quality_loss`. The default is `null`, which turns `promote-cheaper` off.

Each number is finite and at least 0. `family_budget`, `max_token_increase_per_gain`, `min_token_saving`, and `max_quality_loss` may also be `null`.
`min_token_saving` must also be above 0 and below 1. `min_coverage` must also be at most 1. A bool, a string, a negative number, or an unknown field stops the run with `contract-statistics-invalid`.
A `family_budgets` value that is not an object, or that has an empty or blank name, stops the run with the same code.
Each name must match a family in the case draft, else the run stops with `contract-family-unknown`.
The SE multiplier stays 2. A negative threshold would loosen the rule, so the runner refuses it.

Each field resolves in this order: the CLI flag, then the contract, then the built-in default.
The flags are `--min-gain`, `--min-lower-bound`, `--family-budget`, `--max-token-increase-per-gain`, `--min-token-saving`, `--min-coverage`, and `--max-quality-loss`. A flag may set a value below the contract value.
Every flag value must be at least 0. `--min-token-saving` must be above 0 and below 1.
The SE rule stays, so the gate stays at least as strict as the built-in rule, with two exceptions.
`--min-token-saving` adds `promote-cheaper`, and it needs `--max-quality-loss`. A larger `--max-quality-loss` loosens only that verdict.
`--min-coverage` below 0.6 lets the gate decide on fewer scored holdout cases. A value of 0 turns the coverage rule off.
A `min_token_saving` without a `max_quality_loss` stops the run with an error that names both flags. Either value can come from the contract or from a flag.
The first run freezes the resolved values in `run.json`.
The flags repeat on every call until `run.json` exists. The approval stops echo the resolved values as `statistics`.
The case approval covers the resolved thresholds, as it covers the cases and the seed.
A flag that you change or drop after the case approval stop changes the case hash. The run then stops with `cases-unapproved` and a new hash.
Ask the user to approve the new values.
A resume that passes a different value stops with `run-config-differs`. A resume that passes none keeps the frozen values.

## No contract

A run needs no contract. Follow this section only when the user asks for a contract.
When the skill has none, ask the user to choose one path.

1. Choose judge-only grading. No flag selects it.
   Draft a contract that maps each kind to the `judge` grader with a `rubric`.
   Under `run`, the judge uses `--judge-model`, else `--model`.
   Pass a powerful model to the flag that the judge uses.
   Show the user the rubric.
2. Use a contract. Find an existing contract, or draft one with the user.
   For a journey contract, run `scaffold` and edit its draft.

Save every drafted contract with `"status": "draft"`, for both choices.
Keep that status until the user approves the contract.
Set `"status": "approved"` only after the user approves it.
The runner reports `contract-unapproved` for a draft.


## Graders

Each kind in `kinds` names one grader:

- `exact-json`: compares the task result with the case `expected` JSON. A `result_json` that is not a string scores 0 with status `invalid-answer`.
- `judge`: a separate invocation scores the output against the `rubric`. It answers `score_percent`, an integer from 0 to 100, unless the kind declares `pillars`.
  The judge sees the case `expected` as `reference`, so a rubric names `reference`. Without a contract, the default `task` rubric is: "The answer is correct when it satisfies the request and agrees in substance with `reference`. Ignore differences in JSON shape, quoting, or whitespace. Score 100 for a correct answer and 0 for a wrong answer."
- `command`: runs `argv` in an isolated workspace. The case fixtures sit at the workspace root. Candidate outputs sit under `output/`. The command never sees `expected` or the rubric.
- `hybrid`: runs the `command` gate first. A failed gate scores 0, skips the judge, and records `scores.judge` as null.
- `journey`: scores a multi-turn conversation. See "Journeys".
- `audit`: scores findings against reviewed labels in `expected`. The `run` command does not support audit-graded kinds yet, and stops with `contract-audit-unsupported`.

A `command` or `hybrid` grader needs a nonempty `argv`.
A `judge` or `hybrid` grader needs a `rubric`.

A `judge` or `hybrid` grader can also declare these fields:

- `capture`: a nonempty argv that shows the output to the judge as files.
  It runs after the task and after a passed hybrid gate, in a workspace like the `command` workspace.
  It saves files under `capture/`. Ship the capture script as a case fixture.
  `capture/` is a reserved fixture root for every contract. A fixture path must not start with `capture/`.
  The runner accepts at most 16 regular files: `.png` files with a PNG signature and UTF-8 `.txt` files.
  The suffix match is case-sensitive, so `PAGE.PNG` fails.
  The runner refuses a file name that starts with a dot, a hidden directory, and a name with a control character.
  Each file has a limit of 4,000,000 bytes, and all files together have a limit of 16,000,000 bytes.
  The judge workspace gets the files under `capture/`. The judge prompt lists their absolute paths, and the judge opens them with the Read tool.
  A non-zero exit, no files, a symlink, or a file that breaks a rule scores 0 with status `capture-failed` and skips the judge.
  The record then holds `capture_failure`. After a successful capture, the record holds `capture_files`.
  Only the `claude` harness supports `capture`. Other harnesses stop with `contract-capture-unsupported`.
  The capture runs in the OS sandbox of the task role, so `--sandbox-read` and `--sandbox-seconds` apply.
  On macOS the sandbox blocks all network access, loopback included, so a capture that starts a local server needs Linux.
- `pillars`: 1 to 8 unique names, such as `["ui", "ux", "information_flow"]`.
  A name starts with a lowercase letter, has at most 32 characters, and holds only lowercase letters, digits, and underscores.
  The judge answers one required integer from 0 to 100 for each pillar, and no `score_percent`.
  The kind score is the mean of the pillar scores. The record holds each pillar score in `scores.pillars`, from 0 to 1.
  An answer that leaves out a pillar or adds a field scores 0 with status `evaluation-failed`.

The contract hash covers `capture` and `pillars`.

A case of a `command` kind without `expected` stops with `expected-missing`.
A kind with the `judge`, `hybrid`, or `audit` grader reserves two invocations: one for the task and one for the judge.
A kind spends one invocation when the judge does not run.
This covers a failed hybrid gate, a failed capture, a failed activation, and an invalid audit report.
Every judged kind (`judge`, `hybrid`, `audit`) freezes `judge_model` and the judge.


## Journeys

A journey case tests a skill that asks the user questions across several turns.
A simulator role plays the user. The task role runs one conversation per journey.

A journey case has `kind: journey`. It also needs `files` and `expected`, like a task case.
It has these extra fields:

- `brief` (required): the user goal and the facts the simulator may share. Only the simulator sees it. The agent never sees it.
- `max_turns` (optional): the turn limit. The default is 8.
- `requirements` (optional): requirement IDs. The case hash covers them.

A contract declares the tool surface in a `surface` block. Every field is optional:

- `tools`: extra tool names for the task role.
- `mcp_config`: a `.json` file with the MCP servers.
- `agents`: `.md` files. The frontmatter `name` of each file must equal the file stem.
- `companions`: skill directories that the runner stages read-only. Search never edits them.

Search never edits an agent file or the MCP config either. The contract cannot list them in `editable`.

Paths are relative to the skill root.
Only the Claude task role supports a surface. Other roles stop with `contract-surface-unsupported`.
The live preflight accepts only the declared names. It stops with `preflight-leak` on any other name.
The surface joins the environment reuse key.
MCP stdio servers run as children of Claude Code, outside the Bash sandbox.
The MCP config holds commands that the host runs, so search never edits it.

The kind named `journey` takes the `journey` grader, and no other kind does. Any other pairing stops with `contract-kinds`.

The `journey` grader runs a command gate first.
The gate sees the case fixtures at the root, the agent files under `output/`, and the raw events in `trace/events.jsonl`.
A gate score of 0 or less skips the judge and records `gate-failed`.
Otherwise the judge sees the request, the brief, the transcript, the output files, and the reference.

The `simulator` role answers from the brief with `{reply, done}`.
It uses the judge role unless the harness configuration sets it.
It never sees `expected` or the rubric.

A journey case costs `max_turns * 2 + 1` calls for each evaluation.
The plan sizes the holdout gate from the sum of its case costs. It sizes each search call at the mean cost of the train and validation cases.
The search of a journey run gets at least `460 // mean case cost` metric calls, so it can make about 8 proposals. A run with no journey case keeps 4 metric calls for each train or validation case. The plan still shrinks the search to fit the call and time ceilings.
It sizes each holdout retry at the costliest holdout case.
The reserve therefore covers one failed attempt of any case.
The reserve also holds `replay_calls`: one pass of the winner over the holdout, recorded under the `replay` arm.
A file that the seed MCP config runs is read-only.
The run stops with `contract-surface` when `editable` or the helper names such a file.
The default editable set leaves it out.
The check matches each file name in the config text, so it is best-effort.
It does not see a file that the config reaches through `cwd` and a bare name, or through an import.
Keep server scripts in a companion, or out of the skill.
A manifest that mixes task and journey cases pays for each journey case only once, not for every case.
A run with any journey case shows a separate journey estimate line, with `calls`, `seconds`, `ceiling_calls`, and `ceiling_seconds`.
The runner asks for approval once. It stops with `budget-exhausted` at 1000 calls or 28800 seconds.
The plan charges 30 seconds for each call. The seconds cap therefore binds first, at about 960 calls.
A run without a journey case keeps the limits of 200 calls and 7200 seconds.

## Isolation and records

The built-in Codex adapter gives Codex an isolated home and an isolated configuration directory.
A temporary symbolic link references the existing login without copying credential bytes.
Candidate commands cannot access either authentication directory.
Cleanup removes the temporary directory and link.

Candidate commands use a deny-by-default filesystem profile and no network access.
The staged candidate is read-only. The task workspace is writable.
The runner disables external skills, user configuration, hooks, plugins, apps, and web search.
An existing administrator skill directory stops the Codex run.
The runner passes `--strict-config` to `codex exec` and `codex app-server`, so an unknown configuration key stops the call. Every behavior probe still runs.
The `claude` adapter runs `claude -p --setting-sources project` with `--tools Bash,Read,Skill` and `--strict-mcp-config`.
It applies the sandbox floor, denies host reads, and disables bundled skills. It turns off every foreign skill that a free inventory finds.
The harness reference that `SKILL.md` lists defines its preflight and login handling.
The runner rejects failed probes or missing discovery before inference.

The preflight, the search, the reflection calls, and the holdout gate share one persisted call budget.
Approval sets the limit: up to 200 calls and 7200 seconds. At most two model calls run at once.
A run with a journey case uses the larger journey limit. See "Journeys".
The deadline spans the whole run, including pauses between `run` commands.
Process-group cancellation enforces the deadline. The runner does not retry automatically.
Changed frozen inputs require a new run directory.

`run.json` holds the run checkpoint. `cases.json` holds the approved cases and the seed.
`summary` and `report.json` carry `contract_hash` and `contract_source`.
Exported outcomes carry `scores`.

Each run freezes a compatibility fingerprint in `run.json` as `compatibility`. `summary` and `report.json` carry it.
The fingerprint is the digest of `compatibility.fields`. The fields hold no case text.
The fields are `contract_hash`, `skill`, `adapter`, `task_model`, `judge_model`, `claude_options`, `edit`, `repeats`, `statistics`, `split_digest`, `scoring_digest`, and `leakage_digest`.
`judge_model` is `null` when no case kind is judged.
`scoring_digest` covers the source of the scoring modules. An edit to the search, run, or harness code keeps it.
`leakage_digest` covers the source of the leakage check. A change to a rule or a threshold starts a new cohort.
`run.compare(a, b)` names each field that differs between two run records.
It returns `("missing-fingerprint",)` when either record has no fingerprint.

The search checks every proposal for leakage before any task or judge call.
The denylist holds the request, journey brief, expected strings, case ids, and fixture names of the train and validation cases.
Holdout cases are never in it. The session rebuilds it on every open and never writes it to `run.json` or an export.
A proposal leaks when the text that it adds to a component, compared with the seed, matches one rule:

| Rule | Fires when the added text holds |
| --- | --- |
| `request-span` | a verbatim 8-word span of a request or journey brief (case-insensitive, edge punctuation ignored) |
| `expected-answer` | an expected string of at least 20 characters (whitespace squashed, case-insensitive) |
| `case-id` | a case id as a whole token |
| `fixture-name` | a fixture file name as a whole token |
| `runner-term` | `wedge.toml`, `fromargs`, or `@new-cli` as a whole token in any letter case, when the run offers no `@wedge/` source and no `@new-cli` and no seed file holds the term |

Text that the seed already holds never fires a rule.
A leaking proposal scores 0 and makes no task or judge call. It records no outcome.
GEPA still counts each rejected evaluation against its metric-call allowance. A proposer that keeps leaking can end the search early with approved calls left.
Its feedback has `reason` and `rejected` set to `candidate-leakage-rejected`, plus the `rule` and the existing `request`.
The feedback holds no expected text.
`summary` and `report.json` carry `search.leakage_rejections`: the count of distinct rejected proposals per rule.
The runner saves the digests of rejected proposals per rule in `run.json` at each checkpoint, never case text.
A resumed run merges them back, so the counts cover the whole run.
The runner rebuilds the denylist from the frozen cases on every open and never writes it.

`summary` and `report.json` carry `usage_by_role` for the roles `task`, `reflection`, `judge`, and `simulator`.
Each role shows `outcomes`, `model`, `input_tokens`, `cached_tokens`, `output_tokens`, and `latency_seconds`.
`outcomes` counts the outcomes whose usage value for the role is a mapping. The sums cover only those outcomes.
An exact-json outcome has no `task_usage`, so its `usage` counts as task usage.
A `candidate-contract-rejected` outcome ran no task call, so it counts for no role.
A field is `null` when no outcome of the role carries a number for it. `simulator` has no latency.
On a journey case, the task `latency_seconds` includes the simulator turns.
A role without outcomes has a `null` model. A run without a simulator role reports the judge model for `simulator`.
`summary` and `report.json` also carry `calls_upper_bound`: the preflight claims (`preflight_calls` in the run record, kept across a resume), plus `case_calls` summed over the evaluate outcomes, plus one per reflection outcome.
A `candidate-contract-rejected` outcome made no call, so it adds nothing to the bound.
A run record from before `preflight_calls` has a `null` bound and no `usage_gap`.
When `calls` exceeds the bound, they add `usage_gap`: `calls` minus the bound. This flags calls that no outcome explains.
The bound counts each replay outcome with `case_calls`, like a holdout outcome.
`cost_usd` stays `null`. The run prices no spend.

Run records contain private local inputs.
Exports contain a candidate patch and measurements, not requests or expected answers.
A candidate can memorize training content. Therefore, every export remains private and local.
Review it before sharing. The runner never applies or installs a patch.


## Inspection helper contract

Run `python3 -I scripts/inspect-skill.pyz PATH`.
The `.pyz` is the `inspect-skill` target of `wedge.toml`. Its source is the `packages/skillz/src/skillz_inspect` package, a fromargs CLI.
Its input is a UTF-8 file of at most 262144 bytes.
It requires opening and closing frontmatter delimiters.

Success returns exit zero and one JSON object on stdout. Only the parsed JSON is guaranteed; the helper prints it indented.

- `schema_version`: `4`.
- `frontmatter_keys`: sorted unique, unindented lexical keys.
- `body_line_count`: lines after the closing frontmatter delimiter.
- `local_link_targets`: sorted local inline Markdown link paths.
- `long_sentences`: prose sentences over 25 words, each as `{line, words}`.
- `advisory_sentences`: prose sentences of 21 to 25 words, each as `{line, words}`.

The helper measures fence indent from list-item content, as CommonMark does.

The helper reports facts. It does not parse full YAML or compute task fitness.
It ignores external links and fragment-only links.
It rejects package escapes and symlinks without reading linked contents.
Failure returns exit three and an empty stdout.
The last non-empty stderr line is a fromargs error: `{"error": "inspect: <reason>", "exit_code": 3}`.
A missing argument returns exit two.

Each helper fixture expects output `schema_version` `4`.
A fixture declares `input`, `returncode`, and `output`, and may declare `error`.
A `null` output means an empty stdout. An `error` object must equal the JSON on the last non-empty stderr line.
A fixture without `error` leaves stderr unchecked.
The runner checks the sandbox setup on stderr first, apart from the fixtures.
Sandbox transports return the exit code, stdout, and stderr of each fixture command.
Under bubblewrap, the command writes its stderr to a file in a host temporary directory outside the workspace. The runner reads that file and removes the directory.
Only bubblewrap itself writes to the process stderr, so the command cannot forge a setup failure.

Independent positive and negative contract checks run inside the selected adapter's tool sandbox.
Candidate helper code never executes on the host outside that boundary.
Inspection grading requires exact JSON task results, candidate-load evidence, and an actual helper command.
Audit grading preserves the load and helper requirements, then checks evidence and uses the separate judge.
The output schema describes response shape only. It never includes the expected answer.

## Audit facts contract

Run `python3 scripts/skillz-experiment.pyz audit-facts DIRECTORY`.
The command reports the fixed rubric checks for one skill directory. It reports facts and never grades.
The `inspect-skill.pyz` output above does not change, and the command does not repeat its sentence checks.

Success returns exit zero and one JSON object:

- `schema_version`: `1`.
- `input`: the directory argument, as given.
- `checks`: a list sorted by `path`, `id`, then `line`. Each item is `{id, rule, path, line, status, detail}`.

The same input gives byte-identical output.
`path` is relative to the directory. `line` is a 1-based line in the file named by `path`, or `null` when no line applies.
`references.read-trigger` and `scripts.invocation-line` report `SKILL.md` as `path` and name the target file in `detail`.
`status` is `pass`, `fail`, or `not-applicable`.
`rule` names the rubric lens or layout section, then the number of the matching layout rule when one applies.
`detail` is a short fact for the finding.

| `id` | `rule` | Fails when |
|---|---|---|
| `package.skill-file` | `layout.skill-file` | `SKILL.md` is missing; no other check runs |
| `package.frontmatter` | `layout.skill-file` | `SKILL.md` has no frontmatter delimiters; no other check runs |
| `name.matches-directory` | `layout.name` | `name:` differs from the directory name |
| `name.format` | `layout.name` | `name:` is not kebab-case or exceeds 64 characters |
| `description.length` | `invocation` | `description:` is missing or exceeds 1024 characters |
| `frontmatter.known-keys` | `portability.1` | a key is outside the spec and Claude set |
| `sidecar.exists` | `portability.3` | a user-only skill has no `agents/openai.yaml` |
| `sidecar.implicit-invocation-off` | `portability.3` | the sidecar lacks `allow_implicit_invocation: false` |
| `model-policy.user-only` | `portability.10` | a user-only skill sets `model` or `effort` |
| `model-policy.model-invoked` | `portability.10` | a model-invoked skill lacks `model` or `effort` |
| `body.token-estimate` | `information-hierarchy.9` | body bytes divided by 4 exceed 5000 |
| `body.arguments-variable` | `portability.4` | the body contains `$ARGUMENTS` |
| `body.skill-dir-variable` | `portability.5` | the body contains the `CLAUDE_SKILL_DIR` variable |
| `body.file-mention` | `portability.6` | the body contains an `@file` mention |
| `references.nested` | `information-hierarchy.9` | a reference links to another reference, or names its `references/` path |
| `references.orphan` | `information-hierarchy` | `SKILL.md` does not name the reference path as a whole token |
| `references.read-trigger` | `information-hierarchy.9` | the `## References` entry has no trigger word after its dash or colon |
| `scripts.invocation-line` | `deterministic-offload` | no body line names `scripts/<file>` as a whole token |
| `registration.readme-row` | `registration` | the repository README `## Skills` table has no row for the skill |
| `repo-local.internal-metadata` | `repo-local` | a repo-local skill lacks `metadata.internal: true` |
| `repo-local.claude-symlink` | `repo-local` | `.claude/skills/<name>` is not a symlink that resolves to the skill |
| `wedge.own-targets` | `wedge-first-class` | `wedge.toml` is not valid, or a source of an own target is unreadable; otherwise `pass` lists each own target as editable or frozen |
| `wedge.foreign-binary` | `wedge-first-class` | a `.pyz` file is below the skill and no own target builds it; `path` names the file |

A check that does not apply reports `not-applicable`. Examples are a model-invoked skill for the sidecar checks and a skill without references.
Registration and repo-local checks also report `not-applicable` outside a repository.
Wedge checks report `not-applicable` when the project has no `pyproject.toml` or `uv.lock`, such as an installed copy of the skill.
The command finds the repository root by walking up for `.git`.
A repo-local skill lives under `.agents/skills/` and needs no README row.
The wedge checks read `wedge.toml` and the first-party sources. They build nothing and use no network.
They run when the skill has a `wedge.toml` or holds a `.pyz` file. A `wedge.toml` that is not valid gives one failed `wedge.own-targets` check. An unreadable target source also fails it.
A foreign `.pyz` bundle is a `.pyz` file below the skill that no own target builds.
A `.pyz` that a lower `wedge.toml` builds is own when that directory has no sibling `SKILL.md`. A nested skill has one.
The wedge edit limit counts characters with the same rule as the run planner.
The check tests each own target alone, because a run's combined budget depends on the targets it offers.
An own target is frozen for the planner reasons in `### Editable sources`. Examples are a source file over 262144 bytes and sources over the wedge edit limit.
The `wedge.own-targets` detail marks each frozen target. This is not a failure.
A `prose+cli` run cannot edit a frozen target. Use these checks to decide whether to wedge a skill before a run.
A reference listed nowhere in `## References` reports `not-applicable` for `references.read-trigger`.
A trigger clause is the text after the first dash or colon in the entry.
The clause passes when it contains one of: when, whenever, if, once, until, while, before, after, only, fire, need, absent, select, flag, opt.
It also passes when it starts a sentence or a `;` clause with read, load, use, run, open, or consult.
Variable checks scan the whole body. They skip only bare-token spans.
The `@` mention check skips fenced blocks and backtick spans. It counts a mention only when it starts with `./`, `../`, or `~/`, or ends in a file extension.
Orphan matching accepts the skill's own repo-relative prefix and `${CLAUDE_SKILL_DIR}/`.
The script check covers visible files directly in `scripts/`; it skips subdirectories and dot files.

The command parses only the frontmatter lines that the checks need. It does not parse full YAML.
The allowed key set matches the cross-harness frontmatter matrix; a test pins the two together.
Failure returns exit one with a JSON `error` on stderr.
The command reads each file as UTF-8 and rejects a file over 262144 bytes. The error names the file and the limit.
The command rejects a missing path, a file, a symlink input, and any symlink inside the package.
Quality stays in prose. The command cannot judge a trigger phrase, an output contract, or a description.

## Mode fixtures

The mode fixtures test the steps of `add`, `improve`, `wedge`, and contract drafting.
They also test the repository-local `skillz-self-update` skill.
The public cases live in `skills/skillz/evals/mode-fixtures.json`.
The self-update case lives in the repository's test fixtures, so it never publishes.

A fixture uses the version-one case manifest and adds a `target` block with one `command` kind per mode.
The `wedge` mode has one kind per outcome: `wedge-candidates`, `wedge-clean`, and `wedge-agent`.
The `improve` mode has two kinds. `improve` applies the approved findings.
`improve-propose` stops at the approval question and covers the audit-first flow.
Its cases supply Usage evidence for a declared tool that no run uses: fresh pack digests, a current audit report, or a stale audit report.
Its grader computes the package content id of the staged skill: the `git hash-object` of the sorted blob ids of every package file.
It requires a reused audit only when the earlier report has that id.
Each case has these fields:

- `kind`: `add`, `improve`, `improve-propose`, `wedge-candidates`, `wedge-clean`, `wedge-agent`, `contract`, or `self-update`.
- `request`: the text that calls the mode. It names repository paths and never `output/`.
- `files`: the starting tree. It holds no grader.
- `expected`: a tree check or a JSON report. The grader never sees it. The offline tests read it.

The grader is a stdlib script in the kind's `argv`, so the task never reads it.
The runtime stages the case files at the workspace root and runs the task there.
A task creates or edits files at their repository paths.
The runtime then snapshots the whole workspace, including the files that the task did not change.
The grader reads that snapshot under `output/` and prints `{"score": 0 or 1}`.
The grader compares each snapshot file with the staged file at the same path.
A new or changed file counts as written.
A staged file that is missing from the snapshot also counts as written, because the task deleted it.
The `wedge` graders require that `brief.md` is the only written file.

The offline tests run without a model.
They stage each case and apply a recorded golden output or a seeded-bad output.
They snapshot the workspace and grade it, so the tested path is the live path.
The `add` and `improve` tests also run `audit-facts` and `inspect-skill.pyz` on the same trees.
The tests pin the mode steps that each fixture depends on, so an edited step fails the matching test.

To add a case, follow these steps:

1. Add the case to the manifest with a unique `id` and `family`.
2. Put a grader script in the `argv` of its kind. The script scores the golden output as 1 and the seeded defect as 0.
3. Record the golden output and one or more seeded-bad outputs in the repository's mode-output fixture file. Record only the files that the task writes or changes.
4. Pin each step the case depends on in the step list of the mode-fixture test.
5. Run `just build`.

Keep exactly two `holdout` cases. Use `train` and `validation` for the rest.

The fixture JSON files are the source of truth. Edit them directly; no generator script exists.

No command runs these fixtures live, so they never run in `just build` or `just ci`.
The `run` command cannot load the fixture manifest.
The offline tests cover them.
