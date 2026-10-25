# Trevin's Notes: 7 Principles for Agent-Friendly CLIs
URL: https://trevinsays.com/p/7-principles-for-agent-friendly-clis
Fetched: 2026-09-23

"What to implement: support --json on data-bearing commands, use exit code 0 for success and non-zero for failure, write result data to stdout and diagnostics to stderr, return useful fields (names, URLs, IDs, status), and suppress color, spinners, and decorative output when not attached to a TTY."
"An agent parsing colored ANSI text is burning tokens on noise" (TTY detection must also cover piped output, not just prompts).
Fail-fast errors: "The good error does four things: names the specific problem, shows the correct invocation shape, suggests valid values, and includes an example. An agent that gets this error can self-correct in one retry."
"Vague or silent failures are a Blocker. Errors that name the problem but not the fix are Friction. Errors with the full correction path are the Optimization target."
"Agents have gotten more forgiving with every model release... But 'well enough' costs tokens, burns retries."
