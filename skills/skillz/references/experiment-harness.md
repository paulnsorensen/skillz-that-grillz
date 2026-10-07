# Custom experiment harness

Read this reference when the user selects the `claude` adapter, checks a command adapter, or configures separate roles.
`run` accepts only `claude` or `codex` for `--harness`.
A command adapter or a role file applies only to `self-test --preflight-only --harness-config`.

## Select a command

Ask: "Which harness command and model do you want to check?"
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
The adapter is `codex`, `command`, or `claude`.
A Codex role accepts only `adapter` and `model`, without inherited command or identity fields.
A `claude` role accepts `adapter`, `model`, and `command`. The command names only the executable.
A `command` role sends the contract skill name and path to the wrapper.
For mixed adapters, omit the top-level adapter fields. Define each role completely:

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

Pass `--harness-config /absolute/path/to/harness.json` to each `self-test --preflight-only` check.
`run` does not read it. `export` makes no harness invocation.

The runner resolves executable paths and existing file arguments before freezing each role.
Pass script and configuration paths as separate arguments, not embedded `--config=PATH` strings.
The runner stops when an executable, script, file argument, model, or role changes.
Transitive imports and wrapper-managed resources remain the trusted wrapper's responsibility.
Reports contain adapter names, model names, and fingerprints, never command arguments.

## The claude adapter

The `claude` role runs `claude --restricted -p` with `--tools Bash,Read,Skill`.
It also passes `--strict-mcp-config` and a generated settings file.
The settings enable the sandbox floor and disable bundled skills.
They deny host reads from `/`. They allow only the workspace and the runtime roots that commands need.
The runner places only the staged candidate under the workspace skill directory.

The role reuses your Claude login. It asks for no key or token and forwards no token variable.
On Linux, `HOME` stays the isolated workspace home.
The runner sets `CLAUDE_CONFIG_DIR` to a fresh temporary directory outside the workspace.
The directory holds only a symlink named `.credentials.json` to your real login file.
The real file comes from `$CLAUDE_CONFIG_DIR/.credentials.json`, or else `~/.claude/.credentials.json`.
A settings rule denies the Read tool on that temporary directory.
Without a login file, the run stops with the code `login-missing`. Run `claude` once and log in.
The runner deletes the temporary directory when the role closes, even after an error.
If the link no longer points to the same file, the run stops with the code `credential-changed`.
This can happen, for example, if Claude Code replaces or moves the file. Log in again, then start a new run.
On macOS, the login lives in the Keychain, so there is no file to link.
The runner sets `CLAUDE_CONFIG_DIR` to your real config directory.
That directory can expose your own skills, plugins, agents, or MCP servers.
The live preflight stops with the code `preflight-leak` when the init event lists any of them.
Only the candidate skill and the built-in agents are allowed.

Before the first live task, a live isolation preflight runs. It runs once per run, not once per `run` command.
The preflight asks Claude Code to run `cat` on a sealed host file and on a workspace file.
It passes only when a Bash command read the sealed path, the host read failed, and the workspace read returned its token.
The same call also asks Claude to run three exact Bash commands against a loopback listener that the runner owns.
One command opens a TCP connection. Two `curl` commands send an HTTP request to `127.0.0.1` and to `localhost`.
The `curl` commands use `--noproxy ''`, so a configured sandbox proxy carries them.
The host must provide `/usr/bin/curl` and `/usr/bin/python3`. Without them, the preflight fails with no evidence.
The runner judges the listener, not the model reply. A client that sends the probe token fails the preflight.
A client that resets its connection also fails it. A stray client that sends nothing, or sends other bytes, does not count.
A command that differs from the generated command, a missing output, or a malformed output also fails it.
Both failures use the code `network-isolation-failed`.
The probe does not cover name lookup or a non-loopback address. Treat these as a residual gap.
The run record keeps the pass and its live calls under `preflight`.
Each Claude role makes one live preflight per run. Each one counts against the approved call budget.
A resumed `run` reuses the recorded pass and makes no new live call.
Every resume still runs the free helper sandbox probe, which makes no model call.
The reuse key joins the role fingerprint and the Claude environment hash.
The environment hash includes the network probe version.
The environment hash leaves out token variables and the per-process config directory, so it stays stable across processes.
A changed key stops the run with the code `environment-differs` and the text "runtime environment differs from the frozen record".
The run stays resumable. Resume it after you restore the first-run environment.
A runner upgrade that changes the sandbox settings or the network probe also changes the key. Restoring the environment cannot fix that case, so start a new run.
The check runs before any live call, so a changed key costs nothing.
A failed preflight stops the run. There is no fallback to an unsandboxed run.
The adapter runs its own sandbox commands in `bwrap` on Linux and `sandbox-exec` on macOS.
A sandbox that cannot start stops the run before any task call, with the code `sandbox-unavailable`.
The message names the cause and the fix. The runner never weakens the sandbox.
If `bwrap` is missing, install bubblewrap.
If `socat` is missing, install socat.
If the host restricts user namespaces, run `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
Or add an AppArmor profile for `bwrap`.
A failure in the live preflight can cost its one call, but it never reaches a task call.
The macOS path is unverified live. Verify it before you authorize private data.
The network probes cover only direct loopback TCP and the proxy HTTP path on the live Bash path. No live model run has exercised them yet. Run them on a macOS host before you trust the seatbelt profile.
Manual macOS checklist:

1. On a macOS host with `sandbox-exec`, run the full live preflight with a real model.
2. Confirm that the helper sandbox probe and the live network probe both pass.
3. Record the macOS version and the Claude Code version in the ADR.
4. Remove the "unverified" lines only after a recorded pass.

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
Run this command unchanged through the same sandbox that inference tools use.
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
The probe requires denied host reads, denied symlink escapes, and denied candidate writes.
It also requires a clean tool environment.
It requires denied network access: the host loopback connect fails, and a routed connect fails at once with an unreachable or denied error.
The routed check connects by UDP to `192.0.2.1`.
`ENETUNREACH`, `EPERM`, and `EACCES` pass. Any other error fails the probe.
A failed TCP `socket()` call skips both network checks.
The probe covers IPv4 only.
A loopback connect timeout passes. The routed UDP check cannot time out.
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

The runner rejects the check when a skill is missing, extra, or in the wrong location.
Disable other skill roots, hooks, plugins, apps, and external configuration.
The runner rejects a candidate whose SKILL.md frontmatter declares `hooks` or `user-invocable` as a top-level key.
The init-event skill check sees only user-invocable skills, because Claude Code omits skills with `user-invocable: false` from the init `skills` list.

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

## Trust and limits

The wrapper is trusted executable code, not a security boundary against its owner.
A dishonest wrapper can fabricate probe results and traces.
The probe detects accidental configuration failures; it does not attest sandbox integrity.
Protocol fixtures test runner behavior, not real operating-system isolation.
Verify the actual harness sandbox before authorizing private data or paid invocations.

Codex and command preflights make no inference invocation.
Each `claude` role makes one live preflight call per run.
Every attempted inference, including a failed response, consumes the shared invocation budget.
All roles share the same deadline and holdout reservation.
The runner terminates the process group at the deadline and does not retry.
No adapter installs a winning candidate.
