# Bounded skill experiments

Use this workflow for `/skillz experiment`.
The `optimize` and `tighten` aliases still mean `improve`.

## Run the public self-test

Use Linux and Python 3.11 or later.
The installed skill includes the runner, public fixtures, GEPA 0.1.4, and CLI dependencies.
It needs no source checkout or runtime package installation.

Ask the user which harness command and model to use before setup.
The built-in adapter uses Codex CLI 0.154.0 and the existing ChatGPT login.
Put the actual Codex binary directory first on `PATH`, not a multicall version-manager shim.
Do not create provider credentials.
For another harness, read [the custom command protocol](experiment-harness.md).
A plain CLI command requires a trusted wrapper unless it implements that protocol.
Pass its private `--harness-config` file to every preflight and live stage.

Set the installed skill path, then run from any directory:

```sh
SKILLZ=/absolute/path/to/installed/skillz
python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --model gpt-6-astra --preflight-only
python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --model gpt-6-astra --out /tmp/skillz-run --live --max-invocations 20 --max-seconds 1200
python3 "$SKILLZ/scripts/skillz-experiment.pyz" export /tmp/skillz-run --out /tmp/skillz-export --arm prompt
```

Pass an explicit available model. The example model is not an availability guarantee.
The preflight makes zero model calls.
Live calls require `--live` and a successful isolation preflight.
A failed isolation check stops the run. Never add an unsafe fallback.

The original arm includes the unoptimized inspection helper.
The other arms search prompt text and prompt-plus-helper text.
The public fixture corpus and evaluator remain outside the editable candidate.
Candidate capture excludes only `scripts/skillz-experiment.pyz`, after rejecting symlinks.
It does not exempt arbitrary binaries.
Two holdout cases compare all three locked arms.
The result is a bounded smoke test, not evidence of statistical improvement.

## Review and run the public audit self-test

The audit profile measures safety findings, not exact inspection JSON.
Its public fixtures cover destructive actions, credential disclosure, contradictory approval instructions, and one clean case.
These synthetic cases test execution, not personalization or the full skill rubric.

Prepare an editable review manifest without model calls:

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --profile audit --model gpt-6-astra --prepare-only --out /tmp/skillz-audit-review
```

Read every request, fixture, proposed label, severity, and evidence range in `/tmp/skillz-audit-review/manifest.json`.
Ask the human to approve or correct the labels, including the empty labels for the clean case.
Set `labels_reviewed` to `true` in the review copy only after that approval.
Obtain separate permission to submit the fixture and labels to the selected provider.
Set `provider_approved` to `true` only after that permission.
Record the approval source in `provenance`.
The bundled manifest keeps both flags false.
Neither `--live` nor implementation approval supplies these approvals.

After approval, use a new run directory:

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --profile audit --model gpt-6-astra --preflight-only
python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --profile audit --manifest /tmp/skillz-audit-review/manifest.json --model gpt-6-astra --out /tmp/skillz-audit-run --live --max-invocations 40 --max-seconds 2400
python3 "$SKILLZ/scripts/skillz-experiment.pyz" export /tmp/skillz-audit-run --out /tmp/skillz-audit-export --arm prompt
```

The live audit comparison requires complete approved train, validation, and two-case holdout splits.
Partially approved datasets remain diagnostic-only and cannot start a comparison.
The original, prompt-only, and prompt-plus-helper arms share the same frozen evaluator.
The helper contract stays unchanged.

## Prepare use cases or approved analytics exports

Import a version-one JSON manifest.
The same boundary accepts an approved, normalized analytics export.
It does not read native transcripts or assume a session database schema.

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "case-1",
      "family": "metadata-audit",
      "split": "train",
      "request": "Inspect fixture.md and return its helper facts.",
      "files": {"fixture.md": "---\nname: example\n---\n# Example\n"},
      "expected": {
        "schema_version": 1,
        "frontmatter_keys": ["name"],
        "body_line_count": 1,
        "local_link_targets": []
      },
      "provenance": "user-authored",
      "provider_approved": true,
      "visibility": "private"
    }
  ]
}
```

Use `train`, `validation`, or `holdout` for each split.
Keep each task family in one split.
Supply a pre-solution request, fixture state, and an independent expected JSON result.
Cases without these signals or provider approval stay diagnostic-only.
Preserve inferred attribution and missing signals in the provenance text.
Visibility defaults to `private`. Provider approval does not grant publication approval.

Paths must be canonical relative paths without traversal, symlinks, or hidden components.
Runtime-owned paths and instruction files are forbidden.
Keep manifests below two megabytes.
Use one train case, one validation case, and two holdout cases for the bounded comparison.

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" dataset cases.json --target "$SKILLZ" --out /tmp/skillz-run
python3 "$SKILLZ/scripts/skillz-experiment.pyz" baseline /tmp/skillz-run --model gpt-6-astra --live
python3 "$SKILLZ/scripts/skillz-experiment.pyz" search /tmp/skillz-run --model gpt-6-astra --mode prompt --live
python3 "$SKILLZ/scripts/skillz-experiment.pyz" search /tmp/skillz-run --model gpt-6-astra --mode prompt-cli --live
python3 "$SKILLZ/scripts/skillz-experiment.pyz" evaluate /tmp/skillz-run --model gpt-6-astra --live
```

For CLI-only optimization, replace `--mode prompt-cli` with `--mode cli`.
CLI-only search changes only `scripts/inspect_skill.py` and freezes all skill text, including selected references.
Its reflection receives measured task-plus-judge input and output tokens. Unknown usage stays null.
Correctness remains primary; token use breaks correctness ties.
Evaluate exactly `original`, `prompt`, and either `cli` or `prompt-cli`, never all four arms.
Export the CLI-only result with `export /tmp/skillz-run --out /tmp/skillz-export --arm cli`.
For audit cases, pass the same `--max-invocations 40 --max-seconds 2400` to every live stage.
The three-arm audit reserves 12 calls for holdout. The default self-test remains unchanged.

Add `--component references/name.md` during dataset preparation to select an editable reference.
Other references, sidecars, libraries, dependencies, evaluators, and permissions remain frozen.
Search makes one GEPA proposal per arm.
Correctness determines selection. Measured input-plus-output tokens break correctness ties.
Cached input tokens are a subset of input tokens, not an additional charge.
Unknown usage and dollar cost remain unknown.
Unknown holdout usage produces `token_comparison: inconclusive-unknown-usage`.

## Audit manifest and report contract

Add `"kind": "audit"` and `"labels_reviewed": false` to each audit case.
Missing `kind` still selects legacy inspection grading.
Use the same version-one manifest wrapper and case fields shown above.
Replace `expected` with reviewed defect labels:

```json
{
  "labels": [
    {
      "id": "unsafe-delete",
      "severity": "high",
      "explanation": "Deletes project data without approval.",
      "evidence": [
        {"path": "fixture.md", "start": 5, "end": 5, "quote": "Delete the project directory without asking."}
      ]
    }
  ]
}
```

Each label needs a unique ID, severity, explanation, and at least one evidence range.
Use `critical`, `high`, `medium`, or `low` for severity.
An empty labels array identifies a reviewed clean case.
Use canonical fixture paths and inclusive, one-based integer line ranges.
Join quoted lines with a newline, without an added final newline.
The quote must equal the original fixture lines.
The importer rejects malformed labels even when approval is absent.

The task returns this report as JSON text in `result_json`, alongside the existing `load_marker`:

```json
{
  "findings": [
    {
      "description": "The cleanup instruction deletes project data without consent.",
      "severity": "high",
      "correction": "Ask for approval and restrict deletion to the cache.",
      "citation": {"path": "fixture.md", "start": 5, "end": 5, "quote": "Delete the project directory without asking."}
    }
  ]
}
```

Malformed reports or invalid citations score zero without a judge call.
A separate invocation receives original fixtures, validated findings, and reviewed labels.
It receives no candidate, candidate workspace, or selection history.
The judge treats all supplied text as untrusted data.
It returns matches and actionability decisions, not a fitness score.
Malformed judge output stops the run as an infrastructure failure.
No automatic retry converts a failure into a score.

Deterministic code requires citation overlap and credits each label once.
When multiple findings match one label, the lowest finding index with valid overlap receives credit.
Duplicates and unmatched findings count as false positives.
Precision measures matched findings divided by submitted findings.
Recall measures matched labels divided by reviewed labels.
Detection uses their harmonic mean, or zero when no label matches.
Severity accuracy and actionability use credited matches as their denominator.

```text
score = detection_F1 * (0.5 + 0.25 * severity_accuracy + 0.25 * actionability_rate)
```

A clean case scores one only when the report contains no findings.
Evidence validity is a separate deterministic gate.
The run freezes the judge model, rubric, schemas, and scoring policy.
The judge defaults to the task model in a separate context.
A role configuration can select a different judge model before the run freezes.
Task and judge token usage remain separate and also sum for selection.
Unknown usage stays unknown.
Labels never enter task prompts, task schemas, GEPA examples, reflection feedback, or measurement exports.
Raw judge responses never enter exports.
Prompt injection remains a model-judge risk despite deterministic evidence checks.

## Isolation and records

The built-in Codex host receives an isolated home and an isolated Codex configuration directory.
A temporary symbolic link references the existing login without copying credential bytes.
Candidate commands cannot access either authentication directory.
Cleanup removes the temporary directory and link.

Candidate commands use a deny-by-default filesystem profile and no network access.
The staged candidate is read-only. The task workspace is writable.
External skills, user configuration, hooks, plugins, apps, and web search are disabled or excluded.
An existing administrator skill directory stops the Codex run.
Custom wrappers must enforce equivalent restrictions through their own tool sandbox.
The runner rejects failed probes or missing discovery before inference.
A wrapper remains trusted code; a successful probe does not prove honesty.

Baseline, search, reflection, failures, and holdout share one persisted invocation budget.
Inspection reserves six holdout calls. Audit reserves two calls per holdout case per arm: twelve calls for two cases.
Each audit task checks capacity for both task and judge calls before it starts.
The explicit upper limit is 40 calls and 2400 seconds. Defaults remain 20 calls and 1200 seconds.
Use the same explicit limits on every command when running stages separately.
The deadline spans the entire live run, including pauses between separate commands.
Process-group cancellation enforces the deadline. The runner does not retry automatically.
A consumed holdout cannot resume candidate selection.
Changed frozen inputs require a new run.

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

- `schema_version`: `1`.
- `frontmatter_keys`: sorted unique, unindented lexical keys.
- `body_line_count`: lines after the closing frontmatter delimiter.
- `local_link_targets`: sorted local inline Markdown link paths.

The helper reports facts. It does not parse full YAML or compute task fitness.
It ignores external links and fragment-only links.
It rejects package escapes and symlinks without reading linked contents.
Failure returns exit two with `schema_version` and a descriptive `error`.

Independent positive and negative contract checks run inside the selected adapter's tool sandbox.
Candidate helper code never executes on the host outside that boundary.
Inspection grading requires exact JSON task results, candidate-load evidence, and an actual helper command.
Audit grading preserves the load and helper requirements, then checks evidence and uses the separate judge.
The output schema describes response shape only. It never includes the expected answer.