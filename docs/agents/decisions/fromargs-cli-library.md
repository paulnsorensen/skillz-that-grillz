---
status: accepted
owner: paulnsorensen
last_verified: 2026-09-25
confidence: high
sources:
  - .cheese/age/fromargs-cli-library.md
---
# fromargs CLI library decisions

These records explain the design of `fromargs`, an independent Python distribution at `lib/fromargs/`, with import code at `lib/fromargs/src/fromargs/`. It holds the self-healing Cyclopts CLI helpers from easy-cheese PR 716. The approved spec is `fromargs-cli-library` in the durable cheese spec store. The research behind it is [Agent-friendly CLI design](../agent-friendly-cli-design.md).

## Records

### ADR-001: Report errors as one JSON line on stderr, always  [status: accepted]

- **Context:** Agents read command failures and must retry with a correction. Plain `ERROR:` text makes an agent scrape the message. A `--json` mode switch means two error shapes to test and document.
- **Decision:** `run()` always writes each error as one stderr line: `{"error": <message>, "exit_code": <n>}`. There is no plain-text error mode. Repair `note:` lines stay plain text on stderr. An exception a handler does not raise as `CliError`, or one raised while serializing its result, gets the same envelope with an added `traceback` field and exit code 1.
- **Alternatives:** Plain text always, as easy-cheese does today. We rejected it because an agent would still have to parse text. A `--json` mode switch, our first design. We retired it: one error shape is simpler to document and test, and an agent never has to guess which mode it is in.
- **Consequences:** The message keeps Cyclopts' "Did you mean" text, so suggestions stay visible. The suggestions are not a separate JSON field. A later change can add a `suggestions` list without breaking the envelope.

### ADR-002: No JSON input mode  [status: accepted]

- **Context:** Sources disagree on raw JSON payload input for agent CLIs. Microsoft's controlled test found that JSON input never improved correctness and always increased cost.
- **Decision:** `fromargs` has no `--json-input` flag and no stdin payload reader. Typed flat arguments are the interface.
- **Alternatives:** A raw `--json` payload mode, as Justin Poehnelt recommends. We rejected it on the Microsoft evidence.
- **Consequences:** Cyclopts still reads a JSON string into dataclass-style and `list[...]` parameters. Cyclopts 4.25.3 does **not** read a JSON string into a bare `dict` or `dict[str, int]` parameter. Use a dataclass when a command needs a mapping.

### ADR-003: Use native Cyclopts underscore flags and suggestions  [status: accepted]

- **Context:** easy-cheese ported `_standardize_flags` and a `difflib` "did you mean" layer for its own command dispatcher. A Cyclopts 4.25.3 prototype showed that `--max_count` already binds to `max_count`. It also showed that unknown commands and options already print `Did you mean ...?`.
- **Decision:** `fromargs` does not copy those helpers. Tests lock in the native behavior.
- **Alternatives:** Copy the easy-cheese helpers. We rejected this because it duplicates what the parser already does.
- **Consequences:** A Cyclopts upgrade can change this behavior. The `cyclopts>=4.25.3,<5` pin and the tests catch the change. The hoist-leading-flags repair is retired: a bare `--json` or `--full` token anywhere before the end-of-options marker is stripped before parsing, silently, with no `note:` line. `--json` is a no-op; `--full` turns off result truncation. Neither flag ever reaches a handler or Cyclopts.[^1] The quote-split repair can turn a merged value like `"3 --force"` into an extra flag; we accept this risk because the repair only fires on a verified single candidate and prints a `note:` line naming the split.

### ADR-004: Honor the effective backend for async commands  [status: accepted]

- **Context:** Cyclopts can configure `backend` on the root app or a nested command app. The innermost configured value controls the command.[^2]
- **Decision:** `fromargs` reads the parsed command chain and runs async handlers only when its effective backend is `asyncio`. An unconfigured chain defaults to `asyncio`.[^2]
- **Consequences:** A nested `trio` setting raises `TypeError` before the coroutine runs. A nested `asyncio` setting overrides a root `trio` setting.[^3]

### ADR-005: Decorators and forced JSON output  [status: accepted]

- **Context:** The easy-cheese prototype wrapped a raw `cyclopts.App` and called `fromargs.run(app, argv=...)`. Each command handler carried its own `json: bool` and `full: bool` parameters, and its return value doubled as the process exit code. This meant `fromargs` could not own the global flags or the output shape; each handler chose its own.
- **Decision:** `fromargs.App` composes a `cyclopts.App`. Register a command with `@app.command` and a nested group with `app.group(name)`. `App.command` rejects a command whose CLI carries an option named `--json` or `--full`, since `run()` owns both flags. The check runs on the registered command's assembled arguments. An inherited or explicitly passed `default_parameter` counts, because it can add a reserved flag name. `App(default_command=...)` goes through the same check. A rejected command is not left registered; `fromargs` removes it, then raises. A handler's return value is data, not an exit status: `None` means exit 0 with no stdout, and any other JSON-serializable value is printed as one JSON document. `run()` always exits 0 for a normal return and reports every error through ADR-001's envelope.
- **Alternatives:** Keep the raw `cyclopts.App` and a free `run()` function, with `--json` opt-in per ADR-001's rejected alternative. We rejected it: two output shapes and a `json`/`full` parameter on every handler cost more than one decorator layer. A text-by-default output with an opt-in `--json` flag, mirroring common CLI conventions. We rejected it: an agent would have to guess or discover the flag, and `fromargs` targets agents first.
- **Consequences:** A handler's return value is no longer its exit code; only a raised `CliError` (or `contract_error`) sets a non-zero exit. This is a breaking change from the easy-cheese prototype: an `int` or `bool` return value now serializes as JSON instead of setting the exit status. `App.command(limit=n)` truncates a sequence result and prints a `note:` line on stderr unless `--full` is passed. An unhandled exception from a handler, or one raised while serializing its result, is reported through ADR-001's envelope with exit 1 and an added `traceback` field pointing at a saved traceback file. If `fromargs` cannot save the traceback file, the envelope omits the `traceback` field.

### ADR-006: Publish fromargs to PyPI from `fromargs-v*` tags  [status: accepted]

- **Context:** `fromargs` lives in a monorepo alongside skills that release under plain `v[0-9]*` tags (`release.yml`). A PyPI package needs its own version, its own tag namespace, and its own build and publish steps, without changing the existing skill release flow.
- **Decision:** A tag matching `fromargs-v*` triggers `.github/workflows/publish-fromargs.yml`. The workflow verifies the tag is an ancestor of `origin/main`, verifies the tag's version matches `lib/fromargs/pyproject.toml`'s `version`, runs the fromargs test suite, builds the sdist and wheel with `uv build`, checks them with `twine check`, then publishes with `uv publish` through PyPI trusted publishing. Only the publish job holds `id-token: write`, scoped to the `pypi` GitHub environment. No PyPI token is stored as a secret.
- **Alternatives:** Reuse `release.yml`'s `v[0-9]*` trigger for fromargs too. We rejected it: `fromargs-v*` never matches `v[0-9]*`, and conflating the two triggers would fire a PyPI publish on every skill-only tag. A stored `PYPI_API_TOKEN` secret. We rejected it in favor of trusted publishing, which needs no long-lived credential in the repository.
- **Consequences:** The PyPI project must have a trusted publisher configured before the first `fromargs-v*` tag is pushed: owner `paulnsorensen`, repository `skillz-that-grillz`, workflow filename `publish-fromargs.yml`, environment name `pypi`. The `pypi` GitHub environment must also exist in repository settings. Until both are set up, a tag push fails at the `uv publish` step with an authentication error, not a partial publish. The workflow's own "Verify tag is on main" step is a mistake guard, not a trust boundary: it runs from the tagged commit's own copy of the workflow file, so a side-branch commit that deletes the check before being tagged would still run under `environment: pypi` with `id-token: write`. Before the first tag is pushed, the `pypi` GitHub environment must be configured with a deployment-tag policy restricted to `fromargs-v*` and a required reviewer, and a tag ruleset must restrict who can create a `fromargs-v*` tag. Those two settings, not the ancestor check, are the actual trust boundary.

- **Release lanes (2026-09-26):** `v[0-9]*` publishes the skills GitHub release and can later publish the `skillz-that-grillz` Wedge distribution when its PyPI workflow exists. No Wedge PyPI build or upload runs yet. `fromargs-v*` remains the independent `fromargs` PyPI lane. The GitHub `pypi` environment now requires approval from `paulnsorensen` and accepts only `fromargs-v*` tags. The active `release-tag-protection` ruleset limits creation, update, and deletion of both tag namespaces to `paulnsorensen`. The approved `fromargs-v0.1.0` workflow published the wheel and sdist to PyPI on 2026-09-26. The first upload created the `fromargs` project.[^4]

_Source: .cheese/age/fromargs-cli-library.md:18-31, the Cure fixes, and the 2026-09-26 release-lane decision · Updated: 2026-09-26_

[^1]: lib/fromargs/src/fromargs/_argv.py:27-38; lib/fromargs/tests/test_argv.py:33-49
[^2]: lib/fromargs/src/fromargs/_run.py:121-135
[^3]: lib/fromargs/tests/test_run.py:402-448

[^4]: GitHub environment `pypi` and [ruleset `24035194`](https://github.com/paulnsorensen/skillz-that-grillz/rules/24035194), read back through the GitHub API on 2026-09-26; [successful publish run](https://github.com/paulnsorensen/skillz-that-grillz/actions/runs/36233610069); [PyPI `fromargs` 0.1.0](https://pypi.org/project/fromargs/0.1.0/).
