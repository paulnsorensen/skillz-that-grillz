# fromargs CLI library decisions

These records explain the design of `fromargs`, a Python library at `lib/fromargs/`. It holds the self-healing Cyclopts CLI helpers from easy-cheese PR 716. The approved spec is `fromargs-cli-library` in the durable cheese spec store. The research behind it is [Agent-friendly CLI design](../agent-friendly-cli-design.md).

## Records

### ADR-001: Report errors as one JSON line on stderr in `--json` mode  [status: accepted]

- **Context:** Agents read command failures and must retry with a correction. Plain `ERROR:` text makes an agent scrape the message.
- **Decision:** When argv has `--json`, `run()` writes each error as one stderr line: `{"error": <message>, "exit_code": <n>}`. Without `--json`, `run()` writes `ERROR: <message>`. Repair `note:` lines stay plain text in both modes.
- **Alternatives:** Plain text always, as easy-cheese does today. We rejected it because agents in JSON mode would still have to parse text.
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
- **Consequences:** A Cyclopts upgrade can change this behavior. The `cyclopts>=4.25.3,<5` pin and the tests catch the change. Leading `--json` or `--full` moves after a resolved leaf command with a `note:` line. When the path is unresolved, the flags move after leading non-option tokens without a note. This keeps Cyclopts' command suggestion and does not move flags past an option value.[^1]

### ADR-004: Honor the effective backend for async commands  [status: accepted]

- **Context:** Cyclopts can configure `backend` on the root app or a nested command app. The innermost configured value controls the command.[^2]
- **Decision:** `fromargs` reads the parsed command chain and runs async handlers only when its effective backend is `asyncio`. An unconfigured chain defaults to `asyncio`.[^2]
- **Consequences:** A nested `trio` setting raises `TypeError` before the coroutine runs. A nested `asyncio` setting overrides a root `trio` setting.[^3]

_Source: .cheese/age/fromargs-cli-library.md:18-31 and the Cure fixes · Updated: 2026-09-23_

[^1]: lib/fromargs/src/fromargs/_argv.py:24-46; lib/fromargs/tests/test_argv.py:92-122
[^2]: lib/fromargs/src/fromargs/_run.py:90-101
[^3]: lib/fromargs/tests/test_run.py:384-412
