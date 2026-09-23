# You Need to Rewrite Your CLI for AI Agents (Justin Poehnelt)
URL: https://justin.poehnelt.com/posts/rewrite-your-cli-for-ai-agents/
Fetched: 2026-09-23
Raw JSON Payloads > Bespoke Flags
Humans hate writing nested JSON in the terminal. Agents prefer it.
The gws CLI uses --params and --json for all inputs, accepting the full API payload as-is.
An --output json flag, an OUTPUT_FORMAT=json environment variable, or NDJSON-by-default when stdout isn't a TTY lets existing CLIs serve agents without a rewrite of the human-facing UX.
1. Add --output json — machine-readable output is table stakes.
2. Validate all inputs — reject control characters, path traversals, and embedded query params. Assume adversarial input.
