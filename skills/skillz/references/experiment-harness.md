# Custom experiment harness

Read this reference when the user selects a command adapter or configures separate experiment roles.

## Select a command

Ask: "Which harness command and model do you want to use?"
Ask for an explicit argument array, not a shell command string.
A raw harness CLI does not automatically implement this protocol.
Use a trusted user-installed wrapper when the harness lacks these operations.

Keep the wrapper, its configuration, and authentication outside the candidate skill.
Do not place credentials in the JSON configuration or command arguments.
A wrapper can reference the user's existing authentication without copying it into the workspace.
The runner does not inherit environment variables into the wrapper process.
Its environment contains only `PATH=/usr/bin:/bin`.

Create a private configuration file outside the skill:

```json
{
  "schema_version": 1,
  "adapter": "command",
  "identity": "my-harness-wrapper-v1",
  "command": ["/absolute/path/to/wrapper", "--config", "/absolute/path/to/private-config.json"],
  "roles": {
    "judge": {"model": "chosen-judge-model"}
  }
}
```

Task, reflection, and judge roles inherit the top-level adapter, command, and CLI model.
Each role can override inherited fields.
A Codex role accepts only `adapter` and `model`, without inherited command or identity fields.
For mixed adapters, omit top-level adapter fields and define every role completely:

```json
{
  "schema_version": 1,
  "roles": {
    "task": {"adapter": "codex", "model": "task-model"},
    "reflection": {
      "adapter": "command",
      "identity": "wrapper-v1",
      "command": ["/absolute/path/to/wrapper"],
      "model": "reflection-model"
    },
    "judge": {"adapter": "codex", "model": "judge-model"}
  }
}
```

Use one command configuration for all roles unless the user requests separate settings.

Pass `--harness-config /absolute/path/to/harness.json` to every preflight and live stage.
The option applies to `self-test`, `baseline`, `search`, and `evaluate`.
Dataset preparation and export make no harness calls.

The runner resolves executable paths and existing file arguments before freezing each role.
Pass script and configuration paths as separate arguments, not embedded `--config=PATH` strings.
Executable, script, file-argument, model, or role changes reject continuation.
Transitive imports and wrapper-managed resources remain the trusted wrapper's responsibility.
Reports contain adapter names, model names, and fingerprints, never command arguments.

## JSON protocol, version one

The runner starts the configured argument array directly, without shell interpolation.
Each process reads one JSON request from stdin and writes one JSON response to stdout.
Send diagnostics to stderr.
Exit nonzero on transport failure.
Do not log prompts, private configuration, or authentication by default.

Every request includes:

- `schema_version`: integer `1`.
- `operation`: `sandbox`, `discover`, or `infer`.
- `workspace`: absolute task directory.
- `model`: selected role model.

Every response echoes `schema_version` and `operation`.
Return only the fields specified below.
Malformed responses stop the run.

### Sandbox

The request adds `argv`, an explicit command array.
Run this command unchanged through the same sandbox used by inference tools.
Use the requested workspace as its working directory.
Return the actual process result:

```json
{
  "schema_version": 1,
  "operation": "sandbox",
  "returncode": 0,
  "stdout": "actual command output",
  "stderr": ""
}
```

The runner supplies its own probe before inference.
The probe requires denied host reads, denied symlink escapes, denied candidate writes, denied network access, and a clean tool environment.
Only `PATH`, `HOME`, `TMPDIR`, `LANG`, and `LC_CTYPE` may reach tools.
The task workspace must remain writable.
The staged `.agents` tree must remain read-only.
Candidate helper contract checks use this same operation.

### Discovery

The request adds `skill_path` and `skill_name`.
Use the harness's actual skill-discovery mechanism.
Do not echo the supplied path without checking discovery.
Return exactly the one isolated candidate:

```json
{
  "schema_version": 1,
  "operation": "discover",
  "skills": [{"name": "skillz", "path": "/absolute/workspace/.agents/skills/skillz"}]
}
```

Missing, additional, or incorrectly located skills reject the check before inference.
Disable other skill roots, hooks, plugins, apps, and external configuration.

### Inference

The request adds `prompt` and `response_schema`.
Use the selected model and requested workspace in a fresh context.
Do not grant tools access to the request, evaluator, wrapper, configuration, or authentication files.
The prompt and schema travel through stdin outside candidate files.
Judge requests contain no candidate and use a separate workspace.

Return the structured answer and actual completed tool executions:

```json
{
  "schema_version": 1,
  "operation": "infer",
  "answer": {"result_json": "{}", "load_marker": "candidate-marker"},
  "trace": [
    {"argv": ["cat", ".agents/skills/skillz/SKILL.md"], "exit_code": 0},
    {"argv": ["python3", "-I", ".agents/skills/skillz/scripts/inspect_skill.py", "fixture.md"], "exit_code": 0}
  ],
  "usage": {"input_tokens": 100, "cached_input_tokens": null, "output_tokens": 20}
}
```

The answer must match the supplied schema, including exact object fields and value types.
Reflection and judge schemas differ from the task schema.
Trace entries report completed command executions, not model claims or planned commands.
Never synthesize a successful load or helper event.
The evaluator independently checks the marker and qualifying command traces.

Return `usage: null` when usage is unavailable.
Individual counts can also be absent or null.
Counts must be nonnegative integers when known.
Unknown usage never becomes zero.
Unknown holdout usage produces `token_comparison: inconclusive-unknown-usage`.

## Trust and limits

The wrapper is trusted executable code, not a security boundary against its owner.
A dishonest wrapper can fabricate probe results and traces.
The probe detects accidental configuration failures; it does not attest sandbox integrity.
Protocol fixtures test runner behavior, not real operating-system isolation.
Verify the actual harness sandbox before authorizing private data or paid calls.

Preflight makes no inference calls.
Every attempted inference, including a failed response, consumes the shared invocation budget.
All roles share the same deadline and holdout reservation.
The runner terminates the process group at the deadline and does not retry.
No adapter installs a winning candidate.
