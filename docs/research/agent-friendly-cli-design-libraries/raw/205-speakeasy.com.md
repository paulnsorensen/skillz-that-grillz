# Speakeasy: Making your CLI agent-friendly
URL: https://www.speakeasy.com/blog/engineering-agent-friendly-cli
Fetched: 2026-09-23

"Add --non-interactive flags that bypass prompts entirely... When set, commands use sensible defaults or fail fast with clear error messages instead of waiting for input."
"Add --output json for structured responses."
"We also optimized the default output when agent flags are detected. If --non-interactive is set, we assume an automated context and reduce chattiness automatically."
Distinguishes MCP (typed, purpose-built for agents, best for greenfield) vs CLI+Skills (markdown teaching files, best for existing tools/shell access).
