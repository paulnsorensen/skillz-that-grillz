# Command adapter recipes

Read this reference when the user wants to run experiments through Gemini CLI, Copilot CLI, Cursor CLI, OpenCode, or Pi.
Each recipe uses the `command` adapter. The harness reference describes the protocol.
Facts come from vendor documentation read on 2026-10-11. Flags change between releases.
Check each flag with `--help` before you rely on it.

## Gemini CLI (tested reference wrapper)

The runner bundle ships a wrapper: `skillz-experiment harness-wrapper gemini`.
It reads one protocol request on stdin and writes one response on stdout.

Create a private configuration file outside the skill:

```json
{
  "schema_version": 1,
  "adapter": "command",
  "identity": "gemini-wrapper-v1",
  "command": ["/usr/bin/python3", "/abs/path/skillz-experiment.pyz", "harness-wrapper", "gemini"]
}
```

Check the recipe without a model call:

```sh
python3 "$SKILLZ/scripts/skillz-experiment.pyz" self-test --preflight-only --model MODEL \
  --harness-config /abs/path/gemini-harness.json
```

The wrapper options are `--bin-dir DIR` (searched first for `gemini` and the runtime), `--runtime docker|podman|runsc|sandbox-exec`, and `--image IMAGE`.
The runner gives the wrapper only `PATH=/usr/bin:/bin`.
The wrapper also searches `/usr/local/bin`, `/opt/homebrew/bin`, `~/.local/bin`, and `~/.npm-global/bin`.
If `gemini` lives elsewhere, add `"--bin-dir", "/dir/with/gemini"` to the command.
A `node` binary must sit next to `gemini` or in a searched directory.

- Headless flag: `gemini -p TEXT`. The wrapper sends the prompt on stdin and adds `-s`, `--approval-mode=yolo`, `-o stream-json`, and `-m MODEL`.
- Sandbox: Gemini CLI native sandbox, selected with `GEMINI_SANDBOX`.
- Skill directory: workspace `.agents/skills/`.
- Login reuse: Gemini CLI reuses its cached Google login, the file `~/.gemini/oauth_creds.json`. The wrapper links this one file into a temporary home and copies no credential.

### Sandbox runtime dependency

On Linux the Gemini CLI sandbox needs Docker, Podman, runsc (gVisor with Docker), or LXC.
The wrapper drives Docker, Podman, and runsc. It does not drive LXC.
On macOS the wrapper uses Seatbelt (`sandbox-exec`), which ships with the system.
With no runtime, the wrapper stops and names the missing runtime and its install fix.
The Linux container needs Docker or Podman running, plus the Gemini sandbox image (default `ghcr.io/google/gemini-cli:latest`, unverified) with `python3` at `/usr/bin/python3` for the runner probe.
This dependency is more setup than the `claude` and `codex` adapters need.

### How the wrapper answers

The wrapper accepts only a runner workspace: a directory named `workspace` inside a `skillz-*` directory that already holds `.agents`.
The wrapper refuses any other directory, so a wrong call cannot mount your home directory.
The runner also gives the wrapper no `TMPDIR`, so the wrapper checks the directory names and not the temporary directory.

- `sandbox`: Gemini CLI has no run-this-command mode. The wrapper runs the argv in its own container, built from the Gemini sandbox image. The container has a random name `skillz-<hex>`, no network, `--cap-drop ALL`, `no-new-privileges`, a 256-process limit, and a 2 GB memory limit. It has an empty environment. The workspace is writable, but `.agents` is a read-only mount. An in-container `/usr/bin/timeout` limit stops the command after the sandbox limit plus 10 seconds. After 300 seconds, or on SIGTERM, the wrapper runs `docker kill NAME`. The runner sends SIGTERM to the process group, waits 10 seconds, then sends SIGKILL. On macOS, the wrapper runs the argv under the Seatbelt profile that the Claude adapter uses. The runner probe checks this isolation.
- `discover`: the wrapper runs `gemini skills list` in the workspace, under a temporary home (see below). This command starts no model turn. The wrapper answers the requested skill only when `<workspace>/.agents/skills/NAME/SKILL.md` exists and Gemini lists the name. It also answers every other skill name that Gemini lists, so the runner rejects an extra skill. The output format is not documented. The wrapper reads the first word of each unindented line. This parse is unverified on a live host.
- `infer`: the wrapper runs `gemini -s --approval-mode=yolo -o stream-json -m MODEL -p TEXT` in the workspace, under the temporary home. It sends the prompt and the schema on stdin. Gemini appends the `-p` text to stdin. The wrapper joins the assistant message chunks, parses one JSON object, and sums the token counts of the final `result` event as usage. It returns `usage: null` when they are absent. After 900 seconds, or on SIGTERM, the wrapper kills the container that carries its label. The wrapper sets `GOOGLE_GENAI_USE_GCA=true` so Gemini uses the cached Google login. This is unverified on a live host. The wrapper also blanks the keys that a workspace `.env` file can set. Before `discover` and `infer`, it writes an empty `.env` in the runner directory that holds the workspace. Gemini loads only the first `.env` that it finds, so a `.env` in a shared directory such as `/tmp` never loads. The `infer` trace is self-reported by Gemini.

The temporary home links only `~/.gemini/oauth_creds.json`. It never links `settings.json`, `GEMINI.md`, extensions, or skill directories.
The wrapper removes the home after each call. Gemini CLI reads its cached Google login from that file.
The runner passes no environment variables, so an API key in the environment does not reach Gemini. Do not store an API key in a file for this purpose.
The Gemini source mounts only a sanitized `settings.json` and the gcloud configuration into its container. It does not mount `oauth_creds.json`.
Whether a cached login reaches a sandboxed `gemini` is therefore unverified on a live host. With rootless Podman, the temporary home can also hide the image store. This is unverified as well.

#### What `infer` restricts

The probe does not run `infer`. Gemini CLI starts the `infer` sandbox itself, and the wrapper passes only the settings that Gemini documents:

- `GEMINI_SANDBOX_IMAGE` carries the `--image` value (Linux container runtimes).
- `SANDBOX_FLAGS` carries `--cap-drop ALL`, `--security-opt no-new-privileges`, `--pids-limit 256`, `--memory 2g`, and a `--label` that the wrapper uses to find the container.
- `SANDBOX_MOUNTS` mounts `.agents` read-only.
- `DOCKER_CONFIG` is passed so the container runtime finds its configuration.
- `TMPDIR` names a private temporary directory for each call. Gemini gives its sandbox write access to the temporary directory, so other trials under `/tmp` stay out of reach. Check this mount with `docker inspect` during the AC-16 run.
- On macOS, `SEATBELT_PROFILE` selects the custom profile `.gemini/sandbox-macos-skillz.sb`. It copies upstream `strict-open` and adds a write deny for `.agents`. This is unverified on a live host.

The wrapper cannot enforce these restrictions:

- Network. The model needs the network, so the `infer` sandbox has it. `strict-open` also allows the network.
- The container name, user, root file system, and the mounts that Gemini adds (the workspace, a sanitized `settings.json`, and the gcloud configuration).
- A proof that the settings take effect. No test shows that Gemini applies them. Check the live container with `docker inspect` during the AC-16 run.

The tool-event mapping is also unverified until AC-16 runs. The wrapper reads the `-o stream-json` events and keeps only completed calls:

- `run_shell_command` becomes `sh -c COMMAND`.
- `read_file` becomes `cat FILE_PATH`.
- The exit code comes from the last `Exit Code: N` line of the shell output. Whether the stream-json `output` field carries that line is unverified.
- A `tool_result` with status `success` and no such line gives exit code 0. Any other status gives exit code 1.
- A call without a result, a call of another tool, or an event of another shape adds nothing. With no usable event the trace is empty, and graders that need command traces score such runs as unloaded.

The macOS Seatbelt path and the token field names are unverified on a live host.
Record the live check from the preflight command above before you trust a run.
These facts come from the Gemini CLI documentation and source, read on 2026-10-11.
They cover the event and tool names, the `SANDBOX_*` variables, and the `SEATBELT_PROFILE` values.
They also cover the `-p` and stdin behavior and `skills list`.

- https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/sandbox.md
- https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/headless.md
- https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md
- https://github.com/google-gemini/gemini-cli/blob/main/packages/cli/src/utils/sandbox.ts
- https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/output/types.ts

## Other harnesses

These recipes need a trusted wrapper that you write. No bundled wrapper exists.
A wrapper must answer `sandbox`, `discover`, and `infer` as the harness reference defines them.
The runner probe checks the `sandbox` operation only. It does not check the sandbox that a wrapper gives to `infer`.

### GitHub Copilot CLI

- Headless flag: `copilot -p PROMPT` (long form `--prompt`), which exits after the prompt.
- Sandbox: built-in sandbox with a proxy certificate authority (`copilot help sandbox`, `copilot sandbox ca`). The Linux platform list is unverified.
- Skill directory: project `.agents/skills/`.
- Login reuse: `copilot login` stores the token in the system credential store. Without one, it uses `~/.copilot/` (or `COPILOT_HOME`). A token in `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` also works. The runner passes no environment variables to the wrapper.

### Cursor CLI

- Headless flag: `agent -p` (print mode). Add `--force` to apply file changes. Use `--output-format` to choose the output shape.
- Sandbox: Landlock on Linux with a Bubblewrap fallback. It covers shell commands only. The agent can rerun a failed command outside the sandbox after review. Check the run mode before you trust it.
- Skill directory: project `.agents/skills/` and `.cursor/skills/`.
- Login reuse: the docs show `CURSOR_API_KEY` for scripts. Headless reuse of a subscription login is unverified.

### OpenCode

- Headless flag: `opencode run MESSAGE`, with `-m provider/model`.
- Sandbox: none found in the documentation. Run it inside an outer sandbox, such as a container with no network.
- Skill directory: project `.agents/skills/<name>/SKILL.md`. OpenCode also walks up to the git worktree root and loads user skills from `~/.agents/skills/`.
- Login reuse: `opencode auth login` stores credentials in `~/.local/share/opencode/auth.json`. Mount that file read-only into the outer sandbox only if you accept the exposure.

### Pi

- Headless flag: `pi --print PROMPT` (short `-p`). Use `--mode json` for structured output.
- Sandbox: none found in the documentation. Run it inside an outer sandbox. The Pi containerization guide warns that mounting `~/.pi/agent` exposes credentials.
- Skill directory: project `.agents/skills/`, found from the working directory up to the repository root.
- Login reuse: `/login` saves the credential in `auth.json` under `~/.pi/agent` (`PI_CODING_AGENT_DIR` moves it). The guide advises against a real credential inside the sandbox. Use a placeholder token where the sandbox tool supports one.

## Limits

A wrapper for a harness without a model-free skill inventory answers `discover` from its documented skill directory.
That answer proves the directory convention, not that the harness loaded the skill.
The runner still detects loading through the marker and traces, so a harness that returns no trace scores as unloaded.

A command harness reports its own trace, and the Gemini wrapper accepts any printed `{` line as a tool event.
For that reason the runner adds a helper witness to every task `infer`. It stages a stub at the helper path and keeps the real helper beside it under a dot name.
The stub runs the real helper, then appends one ledger line to the workspace root. The helper keeps its exit code and output. The line holds a per-call nonce, the argv, and the exit code.
Helper credit needs a ledger line that matches the trace entry. A forged trace entry earns no credit. A judged, hybrid, or command kind then reports `activation-rejected`. An `exact-json` kind scores 0.
The run record shows `helper_witnessed: true` or `false` for a command harness that has a helper. The key is absent for `claude` and `codex`.
The witness proves a real execution. It does not stop a model that forges ledger lines on purpose.
The Gemini sandbox must allow the helper to write the ledger in the workspace root. The runner has not verified this on a live host. If the write fails, the gate fails closed and the helper earns no credit.

The Gemini `discover` answer comes from `gemini skills list` under a temporary home. It names every skill that Gemini lists, so the runner rejects an extra skill.
The parse of that output is textual and unverified on a live host. A skill that Gemini loads from outside the listing, such as a built-in skill, stays invisible to the check.
The default Gemini sandbox image is `ghcr.io/google/gemini-cli:latest`. The sandbox documentation names this image, but its name and contents are unverified on a live host.
The tag is mutable, so a new image can change the probe result. Pass `--image` with a digest to pin the image.
The protocol has no field for evidence, so the wrapper cannot record the image ID or the `gemini --version` output.
Record both by hand next to the run: `docker image inspect --format '{{.Id}}' IMAGE` and `gemini --version`.
The runner rejects a sandbox response over 1 MB. A probe or helper that prints more output fails with that limit.
The login reuse is unverified (AC-16): a cached subscription login may not reach the sandboxed `gemini`.
