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
- **Gate verdict**: `promote`, `inconclusive`, or `reject`. It compares baseline and winner on the holdout cases.
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
Do not create or request provider credentials.
`--edit prose` is the default and changes only Markdown files.
`--edit prose+cli` also changes the helper scripts of the skill, in the same single search.
`--repeats` sets the repeats for each holdout case. The default is 3.
`--seed` sets the split seed. Keep the default unless the user asks.
`--effort LEVEL` passes `--effort` to every Claude Code call: `low`, `medium`, `high`, `xhigh`, or `max`.
`--sandbox-read PATH` mounts one absolute host path read-only in the runner's OS sandbox. Repeat it for more paths.
Use it for a browser install, for example `/opt/pw-browsers`.
A root must exist, and it must not be a symlink. Give the resolved path.
A root must not hold the home directory, the Claude config directory, the temporary directory, or `--out`.
A root must not equal, hold, or sit inside a runtime path or a credential path.
The runtime paths are `/proc`, `/sys`, `/dev`, `/run`, `/var/run`, and `$XDG_RUNTIME_DIR`.
The credential paths are `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config`, `~/.docker`, `~/.kube`, `~/.netrc`, and `~/.claude.json`.
Home caches such as `~/.cache/ms-playwright` stay allowed.
`--sandbox-seconds N` sets the time limit of one OS sandbox command, from 1 to 600. The default is 20.
The estimate and the holdout gate reserve add this time for each command-grader evaluation.
The stops that ask for approval echo the options as `claude_options`.
These three options apply only to `--harness claude`. The first run records them.
When the run directory already holds a run, the same command resumes it. A resume needs no `--edit`, `--repeats`, `--seed`, or Claude option.
A value that you pass on a resume must match the first run. The target skill directory must also match.

Each stop prints one JSON object on stdout and a coded error on stderr.
Each object holds `stop` (the code) and `message`. The table lists the other fields.
Map each coded stop to its next step:

| Code | Data on stdout | Next step |
|---|---|---|
| `cases-missing` | `draft` (path), `facts` (skill name, description, files) | Write the case draft, then run again. |
| `cases-unapproved` | `question`, `case_hash`, `seed` | Ask question 2. Run again with `--approve-cases HASH`. |
| `budget-unapproved` | `estimate` (calls, seconds, search calls, repeats, holdout cases, holdout retry calls) | Ask question 3. Run again with `--approve-budget CALLS`. Keep `--approve-cases HASH` in the command. |
| `baseline-contract-rejected` | `next` | The original skill fails the contract check. Fix the skill or the contract, then start a new run directory. |
| `gate-budget-exhausted` | `next` | The holdout gate cannot finish within the approved calls. Start a new run directory. |
| `live-required` | `next` | The run needs a model call and `--live` is missing. Run again with `--live`. |
| `run-in-progress` | `next` | Another run holds the run directory. Wait for it to finish, then run again. |
| `run-terminated` | `next`, `failure_code` | The run directory holds a stop from `isolation-failed` or `credential-changed`. Start a new run directory. |
| `run-record-tampered` | Only `stop` and `message` | The budget in `run.json` differs from the plan that the frozen cases give, or the `phase` is unknown. Start a new run directory. |

These other codes arrive on stderr as `code`, with no data on stdout:

| Code | Meaning | Next step |
|---|---|---|
| `login-missing` | No Claude login file exists. | Ask the user to run `claude` once and log in. |
| `sandbox-read-invalid` | A `--sandbox-read` root is missing, is a symlink, holds an unsafe character, or overlaps a refused path. The message names the root. | Restore the path or start a new `--out`. |
| `credential-changed` | The login file moved, or the runner could not restore a refreshed login file. A run that already completed keeps its result and records a `close_warning` instead. The `close_warning` appears in the run summary JSON on stdout, not on stderr. | If the run completed and has a `close_warning`, export the result. Then log in again before the next run. Otherwise log in again and start a new run directory. |
| `clock-skew` | On resume, the wall clock is more than 5 seconds earlier than the run start. | Set the clock right, then run again. Otherwise start a new run directory. |
| `sandbox-unavailable` | The Bash sandbox cannot start. | Apply the fix in the message, then run again in the same directory. The runner never weakens the sandbox. |
| `preflight-leak` | A user skill, plugin, agent, or MCP server loads. On macOS, a `CLAUDE.md` file or a `rules` directory with `.md` files in the config directory also stops the run, before any model call. | Move it out of the config directory for the run, then run again. |
| `cases-too-few` | The holdout has fewer scored cases than the minimum of 6. | Add task cases in more families to the draft, then run again. |
| `search-failed` | Every search evaluation failed. | Check the model, then start a new run directory. |
| `isolation-failed` | A Claude Code run loaded a skill that the candidate does not own, or its event stream hid the skill list. At most one sibling call (task plus judge) that was already running can finish after the fault. | Stop. Check the Claude config directory, then start a new run directory. |
| `harness-changed` | The harness executable or script differs from the frozen record. | Restore the frozen harness, or start a new run directory. |
| `budget-exhausted` | The call budget or the deadline ran out. | Start a new run directory. |
| `out-unsafe` | `--out` is a symlink, is owned by another user, is open to other users, or holds a symlinked `run.lock`. | Use a private directory from `mktemp -d`. |
| `harness-missing` | The built-in `claude` or `codex` executable is not on `PATH`. The run stops before case approval. | Install the CLI or put it on `PATH`, then run again. |
| `run-config-differs` | A resume uses a different `--model`, `--harness`, `--edit`, `--repeats`, `--seed`, `--effort`, `--sandbox-read`, `--sandbox-seconds`, or target skill directory than the recorded run. The message names the field. | Run again with the recorded value, or start a new `--out`. |
| `hidden-file` | The target skill holds a hidden file or directory. The stop comes after case approval. | Remove the hidden file, then run again. |
| `undecodable-file` | A target file is not UTF-8. The stop comes after case approval. | Convert the file to UTF-8 or remove it, then run again. |
| `helper-missing` | `--edit prose+cli` finds no helper script to edit. | Add a helper script under `scripts/`, or use `--edit prose`. |
| `helper-file-missing` | The contract helper or an editable file is missing from the target. | Restore the file, or fix the contract, then run again. |
| `prompt-components-missing` | The target has no editable Markdown file. | Add an editable Markdown file, then run again. |
| `contract-kinds` | The skill contract has no `task` kind and more than one kind. | Read `The autoimprove contract` below. |
| `contract-unapproved` | The skill contract has `status` `draft`. | Ask the user to approve the contract, then set `approved`. |
| `contract-unreadable` | `evals/autoimprove.json` is a symlink, a directory, or too large. | Replace it with a regular file, or remove it. |
| `contract-audit-unsupported` | The scored kind uses the `audit` grader. | Choose another grader for that kind. |
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
Do not assign splits. Code assigns them from the seed.
When session analytics exists, run the Usage ceremony from `SKILL.md`.
Add extra cases from past sessions with `source: session`. Remove private data first.
The approval question lists every case, with its source, family, and split.
A change to the draft changes the case hash and asks the question again.

## Read the gate verdict

The gate scores baseline and winner on holdout cases that no search step saw.
It scores each case `--repeats` times and averages the repeats.
It then takes the mean of the paired case deltas and its standard error (SE).

- `promote`: the mean delta exceeds 2·SE. When the SE is 0, every case delta must be positive.
- `reject`: the mean delta falls below -2·SE.
- `inconclusive`: any other result, or fewer than two holdout cases, or a winner equal to the baseline.

The summary reports the delta, the SE, the case count, and the repeats.
Small runs often report `inconclusive`. That result is honest: the data cannot separate the winner from noise.
Report the verdict as it is. Never promote an `inconclusive` winner by hand.
Never report a delta without its SE and case count.
`python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --simulate` prints the false-promotion rates of the gate. It makes no model call.

## Export

Run `export` only after the run reports the phase `complete`.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" export "$OUT" --out "$OUT/export"
```

Export is write-only. It writes `candidate.patch` and a redacted `report.json` to a new directory.
It never applies or installs the patch, and it never changes the target skill.
Show the user the patch and the verdict. The user decides whether to apply it.
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
- `kinds`: a map of case kind to `{grader, argv?, rubric?}`.
- `helper` (optional): `path`, `input`, and `fixtures` for a bundled helper script.
- `editable` (optional): relative paths that search can change.

A run needs a contract with a `task` kind or with exactly one kind. Otherwise it stops with `contract-kinds`.
It stops with `contract-unapproved` when `status` is `draft`.
Unknown fields and malformed values stop the run.

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
- `judge`: a separate invocation scores the output against the `rubric`. It answers `score_percent`, an integer from 0 to 100.
- `command`: runs `argv` in an isolated workspace. The case fixtures sit at the workspace root. Candidate outputs sit under `output/`. The command never sees `expected` or the rubric.
- `hybrid`: runs the `command` gate first. A failed gate scores 0, skips the judge, and records `scores.judge` as null.
- `audit`: scores findings against reviewed labels in `expected`. The `run` command does not support audit-graded kinds yet, and stops with `contract-audit-unsupported`.

A `command` or `hybrid` grader needs a nonempty `argv`.
A `judge` or `hybrid` grader needs a `rubric`.
A case of a `command` kind without `expected` stops with `expected-missing`.
A kind with the `judge`, `hybrid`, or `audit` grader reserves two invocations: one for the task and one for the judge.
A kind spends one invocation when the judge does not run.
This covers a failed hybrid gate, a failed activation, and an invalid audit report.
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
The `claude` adapter runs `claude --restricted -p` with `--tools Bash,Read,Skill` and `--strict-mcp-config`.
It applies the sandbox floor, denies host reads, and disables bundled skills.
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


## Frozen inspection helper contract

Run `python3 scripts/inspect_skill.py PATH`.
The helper uses only the standard library.
Its input is a UTF-8 file of at most 262144 bytes.
It requires opening and closing frontmatter delimiters.

Success returns exit zero and one JSON object:

- `schema_version`: `3`.
- `frontmatter_keys`: sorted unique, unindented lexical keys.
- `body_line_count`: lines after the closing frontmatter delimiter.
- `local_link_targets`: sorted local inline Markdown link paths.
- `long_sentences`: prose sentences over 25 words, each as `{line, words}`.
- `advisory_sentences`: prose sentences of 21 to 25 words, each as `{line, words}`.

The helper measures fence indent from list-item content, as CommonMark does.

The helper reports facts. It does not parse full YAML or compute task fitness.
It ignores external links and fragment-only links.
It rejects package escapes and symlinks without reading linked contents.
Failure returns exit two with `schema_version` and a descriptive `error`.

Independent positive and negative contract checks run inside the selected adapter's tool sandbox.
Candidate helper code never executes on the host outside that boundary.
Inspection grading requires exact JSON task results, candidate-load evidence, and an actual helper command.
Audit grading preserves the load and helper requirements, then checks evidence and uses the separate judge.
The output schema describes response shape only. It never includes the expected answer.

## Audit facts contract

Run `python3 scripts/skillz-experiment.pyz audit-facts DIRECTORY`.
The command reports the fixed rubric checks for one skill directory. It reports facts and never grades.
The `inspect_skill.py` output above does not change, and the command does not repeat its sentence checks.

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

A check that does not apply reports `not-applicable`. Examples are a model-invoked skill for the sidecar checks and a skill without references.
Registration and repo-local checks also report `not-applicable` outside a repository.
The command finds the repository root by walking up for `.git`.
A repo-local skill lives under `.agents/skills/` and needs no README row.
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
The `add` and `improve` tests also run `audit-facts` and `inspect_skill.py` on the same trees.
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
