# fromargs-cli-library: fromargs public contract

Source: reconstructed from the module docstring of packages/fromargs/tests/e2e/test_acceptance.py (spec fromargs-cli-library is absent from the repository; ADR in docs/agents/decisions/fromargs-cli-library.md)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN argv names a registered top-level command, or a command inside a decorator-registered nested group, THE SYSTEM SHALL invoke exactly that handler once. |  |
| AC-2 | test | WHEN a handler returns a value other than None THE SYSTEM SHALL print that value as one JSON document on stdout and exit 0. |  |
| AC-3 | test | WHEN a handler returns None THE SYSTEM SHALL print nothing on stdout and exit 0. |  |
| AC-4 | test | WHEN a handler raises CliError, or argv fails to parse, THE SYSTEM SHALL print exactly one JSON line ``{"error": <message>, "exit_code": <n>}`` on stderr, print nothing on stdout, and exit with that error's code: 2 for a parse failure or a plain CliError, 3 for contract_error. |  |
| AC-5 | test | WHEN a bare --json token appears anywhere before the end-of-options marker THE SYSTEM SHALL drop it silently and SHALL NOT change the command's exit code, stdout, or call arguments. |  |
| AC-6 | test | WHEN a bare --full token appears anywhere before the end-of-options marker, including one revealed only after a verified quote split, THE SYSTEM SHALL drop it and turn off truncation of the handler's result. |  |
| AC-7 | test | WHEN a command is registered with a limit and its result is a sequence longer than limit, and --full is absent, THE SYSTEM SHALL truncate the printed JSON array to the first limit items and print one note line on stderr; WHEN --full is present THE SYSTEM SHALL print the whole sequence and no truncation note. |  |
| AC-8 | test | WHEN a command handler declares a parameter named json or full THE SYSTEM SHALL raise ValueError at registration time and SHALL NOT register the command. |  |
| AC-9 | test | THE SYSTEM SHALL export exactly App, CliError, contract_error, Group, and Parameter from fromargs.__all__. |  |
| AC-10 | test | THE SYSTEM SHALL provide no command-line flag and no stdin reader for JSON input; a run's behavior SHALL be identical whether or not garbage text is piped to stdin. |  |
| AC-11 | test | WHEN Cyclopts rejects an argv because one token merges several shell-quoted arguments THE SYSTEM SHALL retry only the one candidate split that parses on its own, printing one note: line; WHEN zero splits parse, WHEN more than one candidate parses, WHEN the mergeable token belongs to a free-text str option, or WHEN the token is after the end-of-options marker, THE SYSTEM SHALL leave argv unchanged and report the original error. |  |
| AC-12 | test | WHEN argv is rejected and a quote-split repair is probed THE SYSTEM SHALL NOT invoke any handler until the final, single-candidate parse. |  |
| AC-13 | test | WHEN argv names an unknown command or option close to a registered one THE SYSTEM SHALL include a "Did you mean" suggestion naming the closest match in the error message. |  |
| AC-14 | test | WHEN argv uses an underscore variant of a dashed flag name THE SYSTEM SHALL bind it to the same parameter as the dashed form (native Cyclopts behavior). |  |
| AC-15 | test | WHEN the caller does not pass version= to App, THE SYSTEM SHALL resolve the --version string from the calling module's installed distribution version, else its __version__ attribute, else `0.0.0`; WHEN the caller passes version= explicitly THE SYSTEM SHALL print it unchanged. |  |
| AC-16 | test | WHEN app.default registers a handler, bare or called, and argv names no subcommand THE SYSTEM SHALL invoke that handler exactly once, with the same reserved-parameter check and the same JSON result handling as app.command. |  |
| AC-17 | test | WHEN App.group(name, **cyclopts_kwargs) is called with extra keyword arguments THE SYSTEM SHALL pass them to the nested cyclopts.App. |  |
