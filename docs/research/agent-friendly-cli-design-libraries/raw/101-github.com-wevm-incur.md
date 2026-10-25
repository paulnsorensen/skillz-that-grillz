# wevm/incur README (tavily extract, 2026-09-23)

Session start: tokens consumed just by having the tool available. Traditional MCP servers inject all tool schemas into every turn; skills only load frontmatter (name + description).
Discovery: tokens to learn what commands exist and how to call them. incur splits by command group so only relevant commands are loaded.
Invocation/Response: incur defaults to TOON, which strips braces, quotes, and keys.

Features:
- Agent discovery: built-in Skills and MCP sync (`skills add`, `mcp add`)
- Session savings: up to 3x fewer tokens per session vs MCP or skill alternatives
- Call-to-actions: suggest next commands to agents and humans after a run
- TOON output default; JSON, YAML, Markdown, JSONL alternatives
- `--llms` flag: token-efficient command manifest (Markdown or JSON schema)
- Well-formed I/O: schemas for args, options, env vars, output
- Inferred types from schemas
- Global options: --format, --full-output, --help, --json, --update, --version

Flags table:
--filter-output, --format (toon/json/yaml/md), --full-output, --help/-h, --llms, --mcp (start as MCP stdio server), --json, --schema, --token-count, --token-limit, --token-offset, --update, --version

gh metadata (2026-09-23): stars=620, language=TypeScript, latest release incur@0.5.1 published 2026-08-14, last push 2026-09-11.
