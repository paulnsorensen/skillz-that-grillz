# Custom experiment harness

Read this reference when the user selects the `claude` adapter, runs their own command, or checks role files.
`run --harness` accepts `claude`, `codex`, or `command`.
`run --harness-config PATH` loads a role file. `run --harness command --command ARGV...` builds a one-role command configuration for all three roles.
`self-test --preflight-only --harness-config` checks the same configuration without a run.
Under `run`, the judge uses `--judge-model`, else `--model`.
`--judge-model` applies only to `--harness claude` or `codex`. A role file or a command sets the judge model itself.

## Select a command

Ask: "Which harness command and model do you want to check?"
Ask for an explicit argument array, not a shell command string.
A raw harness CLI does not automatically implement this protocol.
Use a trusted user-installed wrapper when the harness lacks these operations.
The skill also ships a tested Gemini CLI wrapper and recipes for Copilot CLI, Cursor CLI, OpenCode, and Pi.

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
A `claude` role accepts `adapter`, `model`, `command`, `effort`, `sandbox_read`, and `sandbox_seconds`. The command names only the executable.
`effort`, `sandbox_read`, and `sandbox_seconds` match the `run` options `--effort`, `--sandbox-read`, and `--sandbox-seconds`.
The role fingerprint includes them.
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

Pass `--harness-config /absolute/path/to/harness.json` to `self-test --preflight-only` or to `run`.
`export` makes no harness invocation.

## Run with your own command

`run --harness-config PATH` runs every role through the file. The file must sit outside the target skill.
The inline form takes the argument array as repeated `--command` options, one per argument:

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" run --target /path/to/skill --out "$OUT" --model MODEL --live \
  --harness command --command /usr/bin/python3 --command /abs/wrapper.py --command=--flag
```

Write an argument that starts with `-` as `--command=VALUE`. A bare `--command -x` fails.
The model comes from `--model`. The role identity is `command-` plus the executable name.
These combinations stop with the code `usage` before any work:

- `--harness-config` with `--harness` or `--command`.
- `--command` without `--harness command`.
- `--harness command` without `--command`.

The runner skips the `doctor` host checks for a command role. Built-in `claude` and `codex` roles in a file still get them.
Before any task call, the runner sends the command a `sandbox` probe. If the probe fails, the run stops with no fallback.
A resume with another file, another command, or a dropped `--harness-config` stops with `harness-changed`.

The runner resolves executable paths and existing file arguments before freezing each role.
The runner also resolves, fingerprints, and boundary-checks `--option=PATH` values, `NAME=/abs` values, and `-r/abs` values.
Pass script and configuration paths as separate arguments when you can.
The runner stops when an executable, script, file argument, model, or role changes.
Transitive imports and wrapper-managed resources remain the trusted wrapper's responsibility.
Reports contain adapter names, model names, and fingerprints, never command arguments.
The `run.json` adapter label is `config` for `--harness-config` and `command` for `--harness command`.

## The claude adapter

The `claude` role runs `claude -p --setting-sources project` with `--tools Bash,Read,Skill`.
It also passes `--strict-mcp-config`, `--no-session-persistence`, and a generated settings file.
The settings enable the sandbox floor and disable bundled skills.
On Linux, they deny host reads from `/` and allow only the workspace and the runtime roots that commands need.
On macOS, they deny a list of host roots instead, and other system paths stay readable (see the macOS section below).
They deny Bash writes to `.agents` and `.claude` in the workspace, so a task cannot change the candidate or plant project settings.
The runner places only the staged candidate under the workspace skill directory.
The runner does not use `--restricted`. On Claude Code 2.1.289 and later, `--restricted` drops project skills, so the candidate never loads.
`--setting-sources project` drops user settings, user skills, and plugins, and keeps the project skill.

Before any model call, a free skill inventory runs. It sends Claude Code one `initialize` control request, which starts no model turn.
The response lists every command. Claude Code marks its own commands with `builtin: true`.
The runner turns off each other name except the candidate with `skillOverrides: off`, then asks again.
A name that stays on, such as a plugin skill, stops the run with `preflight-leak`.
The init event checks ignore Claude Code's own commands and plugins with the path `builtin`. Docs keep those commands hidden from the model.
Any other skill, plugin, agent, or MCP server in an init event still stops the run.

The role reuses your Claude login. It asks for no key or token and forwards no token variable.
On Linux, `HOME` stays the isolated workspace home.
The runner sets `CLAUDE_CONFIG_DIR` to a fresh temporary directory outside the workspace.
The directory holds only a symlink named `.credentials.json` to your real login file.
The real file comes from `$CLAUDE_CONFIG_DIR/.credentials.json`, or else `~/.claude/.credentials.json`.
A settings rule denies the Read tool on that temporary directory.
Without a login file, the run stops with the code `login-missing`. Run `claude` once and log in.
All Claude roles of one run share this temporary directory and its link.
The runner deletes the directory once, after every role closes, even after an error.
Claude Code can refresh the login and write a refreshed login file in place of the link.
A refreshed login file has an OAuth entry with a nonempty access token and refresh token, and the same top-level keys as the real file.
When the real file is unchanged at close, the runner copies the refreshed login file back with mode 0600.
The close then records the warning `credential-refreshed`, and the run stays resumable.
If another process replaced the real file with a login file during the run, the link still resolves to it.
The close records the warning `credential-rotated`, and the run stays resumable.
In every other case, the run stops with the code `credential-changed`. Log in again, then start a new run.
The runner never copies a refreshed login file over a real file that also changed.
A run that completes records the warning code as `close_warning` in the summary.
A run that fails records the code as `close_failure` in `run.json` and in a note on the error.
On macOS, the login lives in the Keychain, so there is no file to link.
The runner uses your real config directory. You do not need a token or an API key.
Claude Code finds the Keychain login only for the real home, the login user, and the config directory that you use.
So the macOS child keeps your `HOME`, gets `USER` and `LOGNAME`, and gets `CLAUDE_CONFIG_DIR` only when you set it.
`--setting-sources project` makes Claude Code ignore the user settings files, user skills, and user memory in that directory.
The directory can still expose account skills, agents, or MCP servers.
When `CLAUDE.md` or a `rules` directory with `.md` files exists there, the runner runs `claude -p /context` once per role in each process.
That local command makes no model call. It lists the memory files that load.
The run stops with the code `preflight-leak` when the list names your memory, or when the command does not run.
A `User` memory row stops the run even when its path does not match, so a changed path format cannot pass.
Memory that appears during the run gets its listing before the next model call.
You do not need to move your `CLAUDE.md`.
The live preflight also stops with `preflight-leak` when the init event lists a user skill, plugin, agent, or MCP server.
The live preflight also asks the Read tool to read a sealed file outside the workspace.
The run stops with `preflight-leak` when the Read tool returns that file, or when the model never calls the Read tool.
The `--restricted` flag no longer confines the Read tool, so only this probe proves it.
Only the candidate skill, Claude Code's own commands and built-in plugins, and the built-in agents are allowed.

When the live preflight call returns and a preflight check then fails, the runner keeps its transcript in `RUN/preflight.events.json` (mode 0600).
A call that times out or fails to run writes no transcript. Each preflight first removes an old transcript.
The stop message names the file. `run.json` records the message in `failure`.
If the file cannot be written, the stop keeps its code and the message says so.
The file holds the role, the failure message, the final answer, and each tool use.
Each tool use keeps its input, its full result text, and its error flag.
Each string passes through the shared redaction in `skillz_experiments/_redact.py`, which removes URLs and credential-like words and values.
The whole value of a credential-named key, such as `api_key`, becomes `[redacted]`.
The Codex and Gemini diagnostics use the same redaction.
The redaction removes the URLs of the network probes, so the transcript does not show which probe URL a command used.
A `--preflight-only` check has no run directory, so it writes no transcript.

Before the first live task, a live isolation preflight runs. It runs once per run, not once per `run` command.
The preflight asks Claude Code to run `cat` on a sealed host file and on a workspace file.
It passes only when a Bash command read the sealed path, the host read failed, and the workspace read returned its token.
The same call also asks Claude to run five exact Bash commands. Three use a loopback listener that the runner owns. Two must fail.
One command opens a direct TCP connection to the listener. Two `curl` commands send an HTTP request to `127.0.0.1` and to `localhost`.
The `curl` commands use `--noproxy ''`, so a configured sandbox proxy carries them.
One command opens a direct TCP connection to a non-loopback address (`1.1.1.1:443`). One `curl --noproxy '*'` command looks up `example.com` without a proxy.
A connection that opens, or a lookup that resolves (any curl exit code except 6), fails the preflight.
A host that resolves names only through the sandbox proxy shows exit 6 for this direct lookup. That is the wanted denial.
The host must provide `/usr/bin/curl` and `/usr/bin/python3`. Without them, the preflight fails with no evidence.
The runner judges the listener, not the model reply. A client that sends the probe token fails the preflight.
A client that resets its connection also fails it. A stray client that sends nothing, or sends other bytes, does not count.
A command that differs from the generated command, a missing output, or a malformed output also fails it.
All these failures use the code `network-isolation-failed`.
The environment key carries the probe version, so a change of the probe set starts a new preflight.
The run record keeps the pass and its live calls under `preflight`.
Each Claude role makes one live preflight per run. Each one counts against the approved call budget.
A resumed `run` reuses the recorded pass and makes no new live call.
Every resume still runs the free helper sandbox probe, which makes no model call.
The reuse key joins the role fingerprint and the Claude environment hash.
The environment hash includes the network probe version.
The environment hash leaves out token variables and the run's temporary config directory, so it stays stable across processes.
A changed key stops the run with the code `environment-differs` and the text "runtime environment differs from the frozen record".
The run stays resumable. Resume it after you restore the first-run environment.
A runner upgrade that changes the sandbox settings or the network probe also changes the key. Restoring the environment cannot fix that case, so start a new run.
The check runs before any live call, so a changed key costs nothing.
A failed preflight stops the run. There is no fallback to an unsandboxed run.
The adapter runs its own sandbox commands in `bwrap` on Linux and `sandbox-exec` on macOS.
By default, those commands read only the system runtime and the workspace.
On macOS, the profile also allows a read of the root directory itself, but not its children, because every sandboxed process aborts without it.
The runner sets `DEVELOPER_DIR` to the Command Line Tools, when present, so the `/usr/bin/python3` shim stays inside the readable runtime.
The shim adds `CPATH`, `LIBRARY_PATH`, `MANPATH`, and `SDKROOT` to the environment.
The macOS isolation probe allows exactly those names and `DEVELOPER_DIR`. On other systems, it allows none of them.
The Gemini `sandbox` operation pins `DEVELOPER_DIR` the same way.
The runner mounts each `sandbox_read` root read-only at its own path.
A root must exist, and it must not be a symlink.
A root must not equal or hold the home directory.
A root must not equal, hold, or sit inside the Claude config directory, the temporary directory, or the run directory.
A root must not equal, hold, or sit inside `/proc`, `/sys`, `/dev`, `/run`, `/var/run`, `/tmp`, `/var/tmp`, `/var/snap`, `/var/lib`, or `$XDG_RUNTIME_DIR`.
A root must not equal, hold, or sit inside `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config`, `~/.docker`, `~/.kube`, `~/.netrc`, or `~/.claude.json`.
The same rule covers the shell files `~/.bashrc`, `~/.zshrc`, `~/.zshenv`, `~/.profile`, `~/.bash_profile`, `~/.bash_history`, and `~/.zsh_history`.
It also covers `~/.git-credentials`, `~/.npmrc`, `~/.pypirc`, `~/.password-store`, `~/.local/share/keyrings`, and the Claude login file.
A root must not hold a socket entry. The check walks the tree without following links, and it refuses a tree of more than 100000 entries.
Home caches such as `~/.cache/ms-playwright` stay allowed.
A refused root stops the run with the code `sandbox-read-invalid`.
Under `bwrap`, a command has its own network namespace. It can reach a server that it starts itself on loopback, but not the host loopback.
Under `sandbox-exec`, a command has no network, including loopback.
A sandbox command stops after `sandbox_seconds`, 20 by default.
A sandbox that cannot start stops the run before any task call, with the code `sandbox-unavailable`.
The message names the cause and the fix.
If `bwrap` is missing, install bubblewrap.
If `socat` is missing, install socat.
If the host restricts user namespaces for every process, run `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
Or add an AppArmor profile for `bwrap`.

On Ubuntu 24.04 and later, the AppArmor profile `bwrap-userns-restrict` lets `bwrap` start, but strips capabilities from every process that `bwrap` starts.
The sysctl does not lift that profile. Claude Code's Unix-socket filter needs a nested user namespace, so it fails there.
A free probe detects this case: it creates a user namespace inside a first-level `bwrap`.
When the probe fails, the settings set `sandbox.network.allowAllUnixSockets: true`.
A Claude Code maintainer verified that this setting turns off only Unix-socket filtering. File rules and domain rules stay on.
The live preflight then also probes a runner-owned host Unix socket. A Bash command that reaches it stops the run with `network-isolation-failed`.
The environment hash includes this setting, so a resume on a host with the other result stops with `environment-differs`.
The preflight evidence records `unix_socket_filter` as `on` or `off`.
The runner sets `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0` in the Claude child environment. On Claude Code 2.1.301, the value `1` replaced the sandbox file and socket rules on a host that blocks the nested user namespace. Bash then read host files and reached a host pathname Unix socket. The child environment holds no credential variable. The live preflight stays the detector if a later Claude Code version changes this.
A failure in the live preflight can cost its one call, but it never reaches a task call.
On the live Bash path, the network probes cover direct loopback TCP and the proxy HTTP path. They also cover a direct name lookup and a direct non-loopback TCP connection. They do not prove full network isolation.

### macOS live check

On macOS, the Bash sandbox denies reads of a root list, not of `/`.
Claude Code 2.1.309 does not reopen the workspace inside `denyRead: ["/"]` on macOS, and its own temp files then fail.
The list holds the host roots (`/Users`, `/home`, and the others), `/Volumes`, `/var/folders`, `/private/var/folders`, your home directory, and the run paths.
Bash can still read other system paths, such as `/etc` and `/private/tmp`. Linux denies `/` and is stricter.
The last recorded pass: macOS 26.5.1, Claude Code 2.1.309, `claude-sonnet-4-6`, 2026-10-23.
The full live preflight passed for all three roles, with `network: denied`, and the helper sandbox probe passed.
Run this check again after a Claude Code or macOS upgrade:

1. Run `python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --preflight-only --model MODEL`. It makes one live call per role.
2. Confirm that each role shows `isolation: passed` and `network: denied`, and that the command exits 0.
3. Record the macOS version and the Claude Code version in the ADR.

## The Claude converse loop

A journey case runs one live process: `claude -p --input-format stream-json --output-format stream-json --no-session-persistence`.
The runner sends the request first.
After each `result` event, the runner sends one simulator reply.
The loop stops when the simulator answers `done`, at `max_turns`, or when the process exits.

The workspace and the config directory last for the whole journey. The runner removes both after the journey.
The `system` init event of each turn must list the same inventory as the first turn. A difference stops the run with `isolation-failed`.

A live probe on 2026-10-10 (Claude Code 2.1.296) showed per-turn `result.usage`, and the runner sums it.

The trace layout puts the raw stream events in `trace/events.jsonl`.
The journey grader gate reads that file.

The runner stages each companion skill under `.claude/skills` and each agent file under `.claude/agents`.
The runner passes the declared MCP file with `--mcp-config` and grants the permission `mcp__<server>` for each server.
MCP stdio servers run as children of Claude Code, outside the Bash sandbox.
They run unconfined on the host; only the seed-fixed MCP config limits them.

Codex and command transports raise `journey-unsupported`.
A declared surface raises `contract-surface-unsupported` first.

## Credential kind

The runner uses the host subscription login for Claude and for Codex. It needs no API key.
`doctor` prints a `credential` row. Its detail is `subscription login`, `api key`, `unverified (macOS Keychain)`, or `missing`.
A Codex login file with an `OPENAI_API_KEY` value gives `api key`. The harness bills that key per token.
A macOS Keychain login has no file, so the runner cannot read it. The kind is `unverified (macOS Keychain)`.
A Codex role without a login stops with `login-missing`. Run `codex login`.
The `run.json` preflight evidence records the same `credential` value for each built-in `claude` and `codex` role.
A `missing` value fails the doctor. The row tells the user to log in with the harness CLI.

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
The checks compare `returncode`, `stdout`, and `stderr` with the fixtures.
Return the real `stderr` from the transport.
The checks compare only the last non-empty `stderr` line with the `error` of a fixture.
A command adapter must return the real command `stderr`, not an empty placeholder.
The runner kills the wrapper process group 20 seconds after it sends a helper-fixture or grader sandbox request.
This time includes the wrapper start-up, so start any container before the request or keep start-up short.
The isolation probe gets 300 seconds.
The request carries no time limit field.
A helper fixture that reaches this limit is a sandbox timeout, and a grader that reaches it scores as exit code 124.

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
The runner sends a JSON Schema subset in `response_schema`: `object`, `array`, `string`, `integer`, and `boolean`.
A property can have the type `["string", "null"]`. A key can start with `@`, for example `@hypothesis`.
The runner treats a missing nullable key as null. A missing key of any other type is an error.
Judge requests contain no candidate and use a separate workspace.

Return the structured answer and actual completed tool executions:

```json
{
  "schema_version": 1,
  "operation": "infer",
  "answer": {"result_json": "{}", "load_marker": "candidate-marker"},
  "trace": [
    {"argv": ["cat", ".agents/skills/skillz/SKILL.md"], "exit_code": 0},
    {"argv": ["python3", "-I", ".agents/skills/skillz/scripts/inspect-skill.pyz", "fixture.md"], "exit_code": 0}
  ],
  "usage": {"input_tokens": 100, "cached_input_tokens": null, "output_tokens": 20}
}
```

The answer must match the supplied schema, including exact object fields and value types.
Reflection and judge schemas differ from the task schema.
Trace entries report completed command executions, not model claims or planned commands.
Never synthesize a successful load or helper event.
The evaluator independently checks the marker and qualifying command traces.

### Helper witness

A command harness reports its own trace, so the trace alone does not prove that the helper ran.
For each task `infer`, the runner replaces the candidate helper with a stub in the workspace.
The real helper stays in the same directory under a dot name, so `__file__`-relative reads still work.
The stub runs the real helper, keeps its exit code and output, and appends one line to a ledger at the workspace root.
The line holds a per-call nonce, the stub argv, and the exit code. The ledger is not a task output.
The runner grants helper credit only for a trace entry that a ledger line of the same call backs.
The argv and the exit code must match. Without a matching line, the helper earns no credit. A judged, hybrid, or command kind then reports `activation-rejected`. An `exact-json` kind scores 0.
The `contract` check stages no stub. The `claude` and `codex` adapters stage no stub and grade as before.
The protocol stays at `schema_version` 1. The wrapper sees no change.

The run record of each outcome gains `helper_witnessed`: `true` when a ledger line backs the helper run, `false` when it does not.
The key is absent for `claude` and `codex` outcomes, and for a task that has no helper.
The sandbox must let the helper write the ledger in the workspace root. If it does not, the helper earns no credit.

The witness proves that a real execution of the helper took place.
It does not stop a model that writes ledger lines on purpose. The model can read the stub and forge a line.

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
The Codex preflight requires `codex --version` to report 0.154.0 or newer and records the observed string as `codex_version`.
A lower or unparsable version stops with `codex-version-unsupported`. A resume on a different recorded version stops with `environment-differs`.
A runner upgrade needs a new run. A run record from an older runner does not resume.
Each `claude` role makes one live preflight call per run.
Every attempted inference, including a failed response, consumes the shared invocation budget.
All roles share the same deadline and holdout reservation.
The reservation holds the gate calls and the gate's estimated time: the gate calls times 60 seconds, divided by 2 parallel calls.
Search and reflection calls stop when only the reserved time is left. The gate then gets the reserved time.
The runner terminates the process group at the deadline and does not retry.
No adapter installs a winning candidate.
