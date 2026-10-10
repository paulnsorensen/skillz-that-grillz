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
- **Case draft**: the file `RUN/cases.draft.json` that you write from the skill, and from past sessions when analytics exists.
- **Split**: the train, validation, or holdout group of a case. Code assigns it from a recorded seed.
- **Approval**: the user's answer to one question. The runner binds each answer to a hash or to a call count.
- **Budget**: the estimated calls and seconds. After approval, a run uses at most 200 calls and 7200 seconds.
  The search stops early enough to leave the holdout gate its estimated calls and time.
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
`--harness` accepts only `claude` or `codex`. The runner launches only headless `claude -p` or `codex exec`.
The `claude` harness reuses the Claude login. The `codex` harness reuses the ChatGPT login and needs Codex CLI 0.154.0.
Put the real Codex binary directory first on `PATH`, not a multicall version-manager shim.
Do not create or request provider credentials. The one exception is the API key that `--isolation nono` needs after the user chooses it.
A fresh run first runs the free host checks of `doctor`. They make no model call.
A failed check stops the run with `host-not-ready` before the case draft. The stop lists every check and its fix.
Run `python3 "$SKILLZ/scripts/skillz-experiment.pyz" doctor --harness claude` to see the checks without a run.
`--isolation claude` is the default. Claude Code's own sandbox then confines Bash commands.
`--isolation nono` runs the whole Claude process in nono on Linux. Use it only when the user asks or the host needs it.
The nono backend needs `nono`, Linux 6.7 or later, and `ANTHROPIC_API_KEY` on the host.
nono injects the key through its proxy, so the sandbox never sees it. This backend bills the API key, not the Claude login.
`--edit prose` is the default and changes only Markdown files.
`--edit prose+cli` also changes the helper scripts of the skill, in the same single search.
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
`--min-gain N` and `--min-lower-bound N` set the floors of the gate. `--family-budget N` sets the regression budget of every family. `--max-token-increase-per-gain N` and `--min-token-saving N` set the token rule. Each value must be at least 0. `--min-token-saving` must also be above 0 and below 1. "Set promotion thresholds" describes them.
When the run directory already holds a run, the same command resumes it. A resume needs no `--edit`, `--repeats`, `--seed`, Claude option, or threshold flag.
A value that you pass on a resume must match the first run. The target skill directory must also match.

Each stop prints one JSON object on stdout and a coded error on stderr.
Each object holds `stop` (the code) and `message`. The table lists the other fields.
Map each coded stop to its next step:

| Code | Data on stdout | Next step |
|---|---|---|
| `host-not-ready` | `ok`, `harness`, `isolation`, `checks` (each with `check`, `status`, `detail`, and `fix` on a failure) | Show the user each failed check and its fix. Run again after the fix. No model call ran. |
| `cases-missing` | `draft` (path), `facts` (skill name, description, files), `doctor` (the host checks) | Write the case draft, then run again. |
| `cases-unapproved` | `question`, `case_hash`, `seed`, `statistics` | Ask question 2. Run again with `--approve-cases HASH`. Keep any threshold flag in the command. |
| `budget-unapproved` | `estimate` (calls, seconds, search calls, repeats, holdout cases, holdout retry calls), `statistics` | Ask question 3. Run again with `--approve-budget CALLS`. Keep `--approve-cases HASH` and any threshold flag in the command. |
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
| `login-missing` | No Claude login file exists, or Claude Code rejects the login during the preflight (authentication failed). Under nono, the host API key is missing or invalid. | Ask the user to run `claude` once and log in. Under nono, export a valid `ANTHROPIC_API_KEY`. |
| `sandbox-read-invalid` | A `--sandbox-read` root is missing, is a symlink, holds an unsafe character, overlaps a refused path, holds a socket entry, or is too large to check. The message names the root. | On a fresh run, give a valid resolved path. On a resume, restore the path or start a new `--out`. |
| `credential-changed` | The login file moved, or the runner could not restore a refreshed login file. A run that already completed keeps its result and records a `close_warning` instead. The `close_warning` appears in the run summary JSON on stdout, not on stderr. | If the run completed and has a `close_warning`, export the result. Then log in again before the next run. Otherwise log in again and start a new run directory. |
| `clock-skew` | On resume, the wall clock is more than 5 seconds earlier than the run start. | Set the clock right, then run again. Otherwise start a new run directory. |
| `sandbox-unavailable` | The Bash sandbox cannot start, or the temp directory path is too long for the Unix-socket probe. | Apply the fix in the message (for a long path, set `TMPDIR` to a short directory such as `/tmp`), then run again in the same directory. The runner never weakens the sandbox, except one rule on a host that blocks the nested user namespace, which the live socket probe guards. |
| `preflight-leak` | A user skill, plugin, agent, or MCP server loads, or a skill stays on after `skillOverrides` turns it off. A failed read, write, or sandbox probe also stops with this code. The Read tool returning a sealed file outside the workspace, or never being called, also stops the run. A `CLAUDE.md` or `CLAUDE.local.md` file in an ancestor of the workspace also stops the run. On macOS, a `CLAUDE.md` file or a `rules` directory with `.md` files in the config directory also stops the run, before any model call. The code also covers a skill inventory that times out, a Claude Code process that fails to run, and a stream with no init event; the message gives the exit code and a stderr excerpt. | For a leak, move the named entry out of the config directory or the ancestor directory for the run, then run again. For a Read-tool leak, run with `--isolation nono` or report the Claude Code version; no unsafe fallback exists. For a timeout, a failed process, or a missing init event, read the message, check that `claude` starts, then run again. |
| `nono-unavailable` | `--isolation nono` cannot run. The message names each unmet requirement and its fix. | Apply each fix, or use `--isolation claude`. |
| `candidate-name-taken` | The candidate skill name matches a Claude Code command or another loaded skill, so the init event cannot show that the candidate loaded. No model call ran. | Rename the skill for the run, then start a new run directory. |
| `cases-too-few` | The holdout has fewer scored cases than the minimum of 6. | Add task cases in more families to the draft, then run again. |
| `search-failed` | Every search evaluation failed. | Check the model, then start a new run directory. |
| `isolation-failed` | A Claude Code run loaded a skill that the candidate does not own, or its event stream hid the skill list. At most one sibling call (task plus judge) that was already running can finish after the fault. | Stop. Check the Claude config directory, then start a new run directory. |
| `harness-changed` | The harness executable or script differs from the frozen record. | Restore the frozen harness, or start a new run directory. |
| `budget-exhausted` | The call budget or the deadline ran out. | Start a new run directory. |
| `out-unsafe` | `--out` is a symlink, is owned by another user, is open to other users, or holds a symlinked `run.lock`. | Use a private directory from `mktemp -d`. |
| `harness-missing` | The built-in `claude` or `codex` executable is not on `PATH`. A fresh run reports it in `host-not-ready` before case approval. A resume stops with this code. | Install the CLI or put it on `PATH`, then run again. |
| `run-config-differs` | A resume uses a different `--model`, `--harness`, `--isolation`, `--edit`, `--repeats`, `--seed`, `--effort`, `--sandbox-read`, `--sandbox-seconds`, `--min-gain`, `--min-lower-bound`, `--family-budget`, `--max-token-increase-per-gain`, `--min-token-saving`, or target skill directory than the recorded run. The message names the field. | Run again with the recorded value, or start a new `--out`. |
| `hidden-file` | The target skill holds a hidden file or directory. The stop comes after case approval. | Remove the hidden file, then run again. |
| `frozen-file-too-large` | A frozen file is over 16 MiB, or the frozen files together are over 64 MiB. The message names the file. | Remove the file from the skill, or shrink it, then run again. |
| `target-not-built` | `wedge.toml` lists a target, and its `scripts/NAME.pyz` is missing, is a symlink, or is not a file. | Run `wedge bundle` for the skill, then run again. |
| `wedge-config-invalid` | The `wedge.toml` of the skill is not a valid wedge config. The message gives the cause. | Fix `wedge.toml`, then run again. |
| `editable-build-file` | The contract `editable` list names `wedge.toml`, `uv.lock`, or a `.pyz` file. | Remove the name from `editable`. |
| `site-layer-failed` | The host cannot populate the site layer for `--edit prose+cli`. The cause is a missing `uv`, a missing network, or a dependency closure that `uv` refuses. | Install `uv`, restore the network, or use `--edit prose`. Then run again. |
| `build-failed` | The host build of an own target fails. During the search, the candidate scores 0 and the feedback shows the reason. | Read the feedback. |
| `build-failed` (prepare time) | The seed build of an editable target fails in the sealing step, before the run starts. The message names the target. | Fix the target sources, or use `--edit prose`. Then run again. |
| `editable-frozen` | The contract `editable` list names a frozen file, such as a binary file or a file over 262144 bytes. | Remove the name from `editable`. |
| `helper-frozen` | The contract helper names a frozen file. | Name an editable helper, or use `--edit prose`. |
| `reserved-name` | A skill file name starts with `@wedge/`. | Rename the file. |
| `export-path-escapes` | A changed wedge source name leaves the repository root. Export writes no patch. | Keep the project inside the repository root, or edit with `--edit prose`. |
| `helper-missing` | `--edit prose+cli` finds no helper script to edit. | Add a helper script under `scripts/`, or use `--edit prose`. |
| `helper-file-missing` | The contract helper or an editable file is missing from the target. | Restore the file, or fix the contract, then run again. |
| `prompt-components-missing` | The target has no editable Markdown file. | Add an editable Markdown file, then run again. |
| `contract-kinds` | The skill contract has no `task` kind and more than one kind. | Read `The autoimprove contract` below. |
| `contract-unapproved` | The skill contract has `status` `draft`. | Ask the user to approve the contract, then set `approved`. |
| `contract-unreadable` | `evals/autoimprove.json` is a symlink, a directory, or too large. | Replace it with a regular file, or remove it. |
| `contract-audit-unsupported` | The scored kind uses the `audit` grader. | Choose another grader for that kind. |
| `contract-capture-unsupported` | A kind declares `capture`, and the harness is not `claude`. | Run with `--harness claude`, or remove `capture`. |
| `contract-statistics-invalid` | The `statistics` value is not an object. Or it holds an unknown field, a negative value, or a value that is not a finite number (such as a bool or a string). Or `family_budgets` is not an object of numbers, or has an empty or blank name. Or `min_token_saving` is not `null` or a number above 0 and below 1. | Fix the contract, then run again. |
| `contract-family-unknown` | A `family_budgets` name matches no family in the case draft. The stop comes after the draft loads. | Fix the family name in the contract, or add the family to the draft. |
| `run-schema-old` | The run directory comes from an older runner. | Start a new run directory. |
| `export-into-target` | The export destination is inside the target skill directory. | Choose a destination outside the target skill directory. |
| `command-removed` | The command is `dataset`, `baseline`, `search`, or `evaluate`. | Use `run`, then `export`. |

Two close codes are warnings, not stops. They never arrive on stderr as `code`:

| Code | Meaning | Next step |
|---|---|---|
| `credential-rotated` | Another process replaced the login file during the run. The run used the new file. | Continue. |
| `credential-refreshed` | Claude Code refreshed the login during the run. The runner copied the refreshed login file back to the login file. | Continue. |

A run that completes shows the code in `close_warning` in the summary JSON on stdout, with the hint `no action needed`.
A run that fails records the code in `close_failure` in `run.json` and in a note on the error. The run stays resumable.

A `budget-unapproved` stop without `estimate` means the fixed cost exceeds 200 calls.
Lower `--repeats` or the number of holdout cases in the draft, then run again.

## Draft the cases

Read the `facts` from the `cases-missing` stop, then the skill files that it lists.
Write `RUN/cases.draft.json`. It holds a nonempty list of cases.
Each case has these fields:

- `id`: a unique name.
- `family`: a group name for related cases. A family never crosses splits.
- `kind`: `trigger`, `near-miss`, or `task`.
- `request`: the user request that the case sends.
- `files`: a map of path to text, for the starting fixture. A `task` case needs it.
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

## Read the gate verdict

The gate scores baseline and winner on holdout cases that no search step saw.
It scores each case `--repeats` times and averages the repeats.
It then takes the mean of the paired case deltas and its standard error (SE).

- `promote`: the mean delta exceeds 2·SE and passes the floors. When the SE is 0, every case delta must be positive.
- `promote-cheaper`: the mean delta is from 0 to 2·SE, and the task tokens drop by more than 2·`token_se` and by at least `min_token_saving`. See "The token rule".
- `reject`: the mean delta falls below -2·SE.
- `inconclusive`: any other result, or fewer than two holdout cases, or a winner equal to the baseline.

The floors apply only to a result that would promote. That result needs `delta >= min_gain` and `delta > 2·SE + min_lower_bound`.
When the SE is 0, the second test uses 2·SE = 0.
A result that fails a floor becomes `inconclusive`. A floor never creates a `reject`.
With both floors at 0, the verdict equals the built-in rule.

The family rule runs after the floors. It uses the holdout cases, grouped by `family`.
A family needs at least two holdout cases. For that family, the gate takes the mean delta and its SE (SE_f).
The budget is the `family_budgets` value for that family, else `family_budget`. A family with no budget has no family rule.
The family breaches when `mean + 2·SE_f` is below `-budget`. When SE_f is 0, the family breaches when `mean` is below `-budget`.
A breach turns any verdict into `reject`. This also applies to `promote`. Each breach adds the reason `family-regression:<name>`, sorted by family name.
The rule skips a family with fewer than two holdout cases, whatever its budget. `skipped_families` lists it, sorted by name. A skipped family never changes the verdict.
The gate object always holds `skipped_families`. Without a budget, the verdict stays the same.

### The token rule

The token rule is off while `max_token_increase_per_gain` and `min_token_saving` are both `null`. Then no token rule runs. The verdict, the reasons, and the reflection prompt match a run without these fields.
When a field is set, the rule runs after the floors and before the family rule.
The task tokens of one holdout outcome are `input_tokens - cached_input_tokens + output_tokens`.
Both adapters count the cached input inside `input_tokens`, so the charge covers only the uncached input and the output.
A count that is missing, a bool, or negative makes the task tokens unknown. A `cached_input_tokens` value above `input_tokens` does the same.
Judge usage never enters the charge. The outcomes keep all counts for the report.
For each case, the token change is `mean(winner task tokens) / mean(baseline task tokens) - 1`, with the mean over the repeats.
The gate takes the mean of the case token changes (`token_delta`) and its SE (`token_se`).
When every case token change is equal, `token_se` is 0 and `token_delta` is that change.
Each token threshold treats two values as equal when they differ by a relative 1e-9 or an absolute 1e-12.
The score rule, the floors, and the family rule compare exactly. ADR-002 and the pinned false-promotion rates require this.

- `token-cost`: a `promote` becomes `reject` when `token_delta` exceeds `max_token_increase_per_gain * delta`. The reason is `token-cost`.
- `promote-cheaper`: this needs `min_token_saving`. The score result must be `inconclusive` with a mean delta from 0 to 2·SE.
  A mean delta below 0 never promotes cheaper. A result that a floor turned into `inconclusive` is outside the band.
  `-token_delta` must exceed 2·`token_se` and must be at least `min_token_saving`.
  The floors do not apply, because the verdict claims no gain.
- `unknown-usage`: a holdout outcome has unknown task tokens, or a case has a baseline mean of 0.
  An active rule with no task tokens at all gives the same result.
  Then a `promote` becomes `inconclusive` with this reason.
  A band result with `min_token_saving` set gets the same reason and stays `inconclusive`.

A family breach still turns any verdict, including `promote-cheaper`, into `reject`.
A `promote-cheaper` winner counts as a promotion. Export it as you would a `promote` winner. The user decides whether to apply it.
With a token field set, the winner choice breaks a validation-mean tie in favor of fewer mean task tokens. This holds for the best candidate and for the seed.
A candidate with unknown task tokens, or equal task tokens, keeps the usual winner. In a tie, that is the incumbent.
The reflection prompt then also says: "Prefer fewer tokens when correctness is equal."

The winner choice treats two validation means as equal when they differ by a relative 1e-9 or an absolute 1e-12.
This holds in every run, with or without a token field.
Without a token field, the seed wins a tie.

### Reasons and reporting

The gate verdict holds `reasons`, a list of the rules that fired. The floor reasons are `min-gain` and `lower-bound`. The family reason is `family-regression:<name>`. The token reasons are `token-cost` and `unknown-usage`.
A plain verdict has an empty list. A winner equal to the baseline has the reason `winner-equals-baseline`.
The summary and the export report show `reasons`, `skipped_families`, `token_delta`, and `token_se` in the `gate` object. The token fields are `null` while the token rule is off or the usage is unknown.
The summary reports the delta, the SE, the case count, and the repeats.
Small runs often report `inconclusive`. That result is honest: the data cannot separate the winner from noise.
Report the verdict as it is. Never promote an `inconclusive` winner by hand.
Apply a winner only after a `promote` or `promote-cheaper` verdict.
Never report a delta without its SE and case count.
`python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --simulate` prints the false-promotion rates of the gate. It makes no model call.
The rates cover `promote` only. They do not cover `promote-cheaper`.

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

Only `--edit prose+cli` edits sources. `--edit prose` keeps every own target frozen.
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
It cites the fromargs rules: one parser, JSON errors on stderr, and stdout only for results.
A usage error exits with code 2. A failed input contract exits with code 3.
A non-Python helper keeps its language.
A Python helper may be a stdlib `argparse` helper only when the skill has no `wedge.toml` that can build a fromargs target.
The prompt describes `@new-cli` only when the run offers it.
The `prose` prompt does not change.
Run `audit-facts` first to see the wedge state of the skill.

## Export

Run `export` only after the run reports the phase `complete`.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" export "$OUT" --out "$OUT/export"
```

Export is write-only. It writes `candidate.patch` and a redacted `report.json` to a new directory.
Its result lists the written patch files under `patches`.
It never applies or installs a patch, and it never changes the target skill.
Show the user each patch and the verdict. The user decides whether to apply it.
`candidate.patch` is a plain unified diff with paths relative to the skill directory.
Apply it from the skill directory with `git apply` or `patch -p1`. Added and deleted skill files appear in it.
When a candidate changes wedge sources, export also writes `wedge-sources.patch`.
It has git headers and paths relative to the repository root. Apply it from the repository root with `git apply`.
Only a changed wedge source must lie inside the repository root.
A changed source path outside the repository root stops export with `export-path-escapes`.
A skill-only change exports outside a repository.
`doctor --harness claude` runs the free host checks without a run. Add `--isolation nono` to check the nono backend.
`self-test --preflight-only --model MODEL` checks the harness without a run.
Pass `--harness-config FILE` to check a custom command adapter. The harness reference that `SKILL.md` lists describes it.


## The autoimprove contract

A contract is optional. It tells the runner how to grade the cases.
It lives at `<skill>/evals/autoimprove.json`. The shipped `skills/skillz/evals/autoimprove.json` is the example.
Without it, the runner grades each `task` case with a judge against its `expected` answer.

The contract has these fields:

- `schema_version`: integer `1`.
- `status`: `approved` or `draft`. A draft stops the run.
- `skill`: the skill directory name.
- `invocation`: the request that calls the skill. It supports `{skill}` and `{path}`.
- `kinds`: a map of case kind to `{grader, argv?, rubric?, capture?, pillars?}`.
- `helper` (optional): `path`, `input`, and `fixtures` for a bundled helper script.
- `editable` (optional): relative paths that search can change.
- `statistics` (optional): promotion thresholds for the holdout gate. See "Set promotion thresholds".

A run needs a contract with a `task` kind or with exactly one kind. Otherwise it stops with `contract-kinds`.
It stops with `contract-unapproved` when `status` is `draft`.
Unknown fields and malformed values stop the run.

## Set promotion thresholds

The `statistics` object in the contract tightens the `promote` rule. It never loosens it.
Every field except `min_token_saving` only tightens the rule. Only `min_gain` and `min_lower_bound` are floors.
`min_token_saving` adds the verdict `promote-cheaper`. That verdict needs no observed loss and claims no gain.
A contract without it keeps its identity hash and the built-in rule.
The object accepts these fields:

- `min_gain`: the smallest mean holdout delta that can promote. The default is 0.
- `min_lower_bound`: the margin that the delta must clear beyond 2·SE. The default is 0.
- `family_budget`: the regression budget of every holdout family. The default is `null`, which turns the family rule off.
- `family_budgets`: an object that maps a family name to its own budget. It overrides `family_budget` for that family. A flag cannot set it.
- `max_token_increase_per_gain`: the largest token change that one unit of score gain can buy. The default is `null`, which turns this check off.
- `min_token_saving`: the smallest token saving, as a share of the baseline task tokens, that can promote a winner with a mean delta of at least 0. The default is `null`, which turns `promote-cheaper` off.

Each number is finite and at least 0. `family_budget`, `max_token_increase_per_gain`, and `min_token_saving` may also be `null`.
`min_token_saving` must also be above 0 and below 1. A bool, a string, a negative number, or an unknown field stops the run with `contract-statistics-invalid`.
A `family_budgets` value that is not an object, or that has an empty or blank name, stops the run with the same code.
Each name must match a family in the case draft, else the run stops with `contract-family-unknown`.
The SE multiplier stays 2. A negative threshold would loosen the rule, so the runner refuses it.

Each field resolves in this order: the CLI flag, then the contract, then the built-in default.
The flags are `--min-gain`, `--min-lower-bound`, `--family-budget`, `--max-token-increase-per-gain`, and `--min-token-saving`. A flag may set a value below the contract value.
Every flag value except `--min-token-saving` must still be at least 0, so the gate stays at least as strict as the built-in rule.
`--min-token-saving` adds `promote-cheaper`.
The first run freezes the resolved values in `run.json`.
The flags repeat on every call until `run.json` exists. The approval stops echo the resolved values as `statistics`.
A flag that you drop after an approval stop changes the frozen values.
A resume that passes a different value stops with `run-config-differs`. A resume that passes none keeps the frozen values.

## No contract

A run needs no contract. Follow this section only when the user asks for a contract.
When the skill has none, ask the user to choose one path.

1. Choose judge-only grading. No flag selects it.
   Draft a contract that maps each kind to the `judge` grader with a `rubric`.
   Under `run`, the judge uses the `--model` value, so pass a powerful model there.
   Show the user the rubric.
2. Use a contract. Find an existing contract, or draft one with the user.

Save every drafted contract with `"status": "draft"`, for both choices.
Keep that status until the user approves the contract.
Set `"status": "approved"` only after the user approves it.
The runner reports `contract-unapproved` for a draft.


## Graders

Each kind in `kinds` names one grader:

- `exact-json`: compares the task result with the case `expected` JSON. A `result_json` that is not a string scores 0 with status `invalid-answer`.
- `judge`: a separate invocation scores the output against the `rubric`. It answers `score_percent`, an integer from 0 to 100, unless the kind declares `pillars`.
- `command`: runs `argv` in an isolated workspace. The case fixtures sit at the workspace root. Candidate outputs sit under `output/`. The command never sees `expected` or the rubric.
- `hybrid`: runs the `command` gate first. A failed gate scores 0, skips the judge, and records `scores.judge` as null.
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


## Isolation and records

The built-in Codex adapter gives Codex an isolated home and an isolated configuration directory.
A temporary symbolic link references the existing login without copying credential bytes.
Candidate commands cannot access either authentication directory.
Cleanup removes the temporary directory and link.

Candidate commands use a deny-by-default filesystem profile and no network access.
The staged candidate is read-only. The task workspace is writable.
The runner disables external skills, user configuration, hooks, plugins, apps, and web search.
An existing administrator skill directory stops the Codex run.
The `claude` adapter runs `claude -p --setting-sources project` with `--tools Bash,Read,Skill` and `--strict-mcp-config`.
It applies the sandbox floor, denies host reads, and disables bundled skills. It turns off every foreign skill that a free inventory finds.
The harness reference that `SKILL.md` lists defines its preflight and login handling.
The runner rejects failed probes or missing discovery before inference.

The preflight, the search, the reflection calls, and the holdout gate share one persisted call budget.
Approval sets the limit: up to 200 calls and 7200 seconds. At most two model calls run at once.
The deadline spans the whole run, including pauses between `run` commands.
Process-group cancellation enforces the deadline. The runner does not retry automatically.
Changed frozen inputs require a new run directory.

`run.json` holds the run checkpoint. `cases.json` holds the approved cases and the seed.
`summary` and `report.json` carry `contract_hash` and `contract_source`.
Exported outcomes carry `scores`.

Run records contain private local inputs.
Exports contain a candidate patch and measurements, not requests or expected answers.
A candidate can memorize training content. Therefore, every export remains private and local.
Review it before sharing. The runner never applies or installs a patch.


## Inspection helper contract

Run `python3 -I scripts/inspect-skill.pyz PATH`.
The `.pyz` is the `inspect-skill` target of `wedge.toml`. Its source is the `lib/src/skillz_inspect` package, a fromargs CLI.
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
