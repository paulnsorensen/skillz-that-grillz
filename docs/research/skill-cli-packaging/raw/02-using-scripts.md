# Source capture: Using scripts

Source: https://agentskills.io/skill-creation/using-scripts
Fetched: 2026-09-28. Excerpts re-checked against the live page on 2026-10-01. The source line numbers in the report refer to an extraction the repository does not keep.

Concise help:

- "--help output is the primary way an agent learns your script’s interface. Include a brief description, available flags, and usage examples:"
- "Keep it concise — the output enters the agent’s context window alongside everything else it’s working with."

Structured stdout, diagnostic stderr:

- "Separate data from diagnostics: send structured data to stdout and progress messages, warnings, and other diagnostics to stderr."

Output bounds or files:

- "If your script might produce large output, default to a summary or a reasonable limit, and support flags like --offset so the agent can request more information when needed."
- "Alternatively, if output is large and not amenable to pagination, require agents to pass an --output flag"

Explicit, unattended inputs:

- "Agents operate in non-interactive shells — they cannot respond to TTY prompts, password dialogs, or confirmation menus."

Actionable errors and exit codes:

- "say what went wrong, what was expected, and what to try"
- "Use distinct exit codes for different failure types (not found, invalid arguments, auth failure) and document them in your --help output"

Retry and state-change safeguards:

- "Agents may retry commands. “Create if not exists” is safer than “create and fail on duplicate.”"
- "For destructive or stateful operations, a --dry-run flag lets the agent preview what will happen."
