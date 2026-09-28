# Bounded skill experiments

Use this workflow for `/skillz experiment`.
The `optimize` and `tighten` aliases still mean `improve`.

## Run the public self-test

Use Linux, Python 3.11 or later, and Codex CLI 0.154.0.
Keep the existing ChatGPT login. Do not create provider credentials.
Install the optional GEPA 0.1.4 extra only when the user requests experiments.

Run from the repository root:

```sh
uv run --project lib --extra experiments skillz-experiment self-test --model gpt-6-astra --preflight-only
uv run --project lib --extra experiments skillz-experiment self-test --model gpt-6-astra --out /tmp/skillz-run --live --max-invocations 20 --max-seconds 1200
uv run --project lib --extra experiments skillz-experiment export /tmp/skillz-run --out /tmp/skillz-export --arm prompt
```

Pass an explicit available model. The example model is not an availability guarantee.
The preflight makes zero model calls.
Live calls require `--live` and a successful isolation preflight.
A failed isolation check stops the run. Never add an unsafe fallback.

The original arm includes the unoptimized inspection helper.
The other arms search prompt text and prompt-plus-helper text.
The public fixture corpus and evaluator remain outside the editable skill.
Two holdout cases compare all three locked arms.
The result is a bounded smoke test, not evidence of statistical improvement.

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
uv run --project lib --extra experiments skillz-experiment dataset cases.json --target skills/skillz --out /tmp/skillz-run
uv run --project lib --extra experiments skillz-experiment baseline /tmp/skillz-run --model gpt-6-astra --live
uv run --project lib --extra experiments skillz-experiment search /tmp/skillz-run --model gpt-6-astra --mode prompt --live
uv run --project lib --extra experiments skillz-experiment search /tmp/skillz-run --model gpt-6-astra --mode prompt-cli --live
uv run --project lib --extra experiments skillz-experiment evaluate /tmp/skillz-run --model gpt-6-astra --live
```

Add `--component references/name.md` during dataset preparation to select an editable reference.
Other references, sidecars, libraries, dependencies, evaluators, and permissions remain frozen.
Search makes one GEPA proposal per arm.
Correctness determines selection. Measured input-plus-output tokens break correctness ties.
Cached input tokens are a subset of input tokens, not an additional charge.
Unknown usage and dollar cost remain unknown.

## Isolation and records

The trusted Codex host receives an isolated home and an isolated Codex configuration directory.
A temporary symbolic link references the existing login without copying credential bytes.
Candidate commands cannot access either authentication directory.
Cleanup removes the temporary directory and link.

Candidate commands use a deny-by-default filesystem profile and no network access.
The staged candidate is read-only. The task workspace is writable.
External skills, user configuration, hooks, plugins, apps, and web search are disabled or excluded.
An existing administrator skill directory stops the run.

Baseline, search, reflection, failures, and holdout share one persisted invocation budget.
Six calls remain reserved for holdout.
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

Independent positive and negative contract checks run inside the same Codex sandbox.
Candidate helper code never executes on the host outside that boundary.
The evaluator requires exact JSON task results, candidate-load evidence, and an actual helper command.
The output schema describes response shape only. It never includes the expected answer.
