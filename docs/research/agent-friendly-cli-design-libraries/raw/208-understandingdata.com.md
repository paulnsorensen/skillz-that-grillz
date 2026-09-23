# Just Understanding Data: Rewrite Your CLI for AI Agents (summary of Justin Poehnelt's post)
URL: https://understandingdata.com/posts/rewrite-cli-for-agents
Fetched: 2026-09-23
Attributed source: Justin Poehnelt (Google), March 2026 original post (poehnelt.com; original URL not directly fetched, HN discussion at news.ycombinator.com/item?id=47252459).

Core thesis quoted: "Human DX optimizes for discoverability and forgiveness. Agent DX optimizes for predictability and defense-in-depth."
Idempotency: "Agents retry constantly... Running the same deploy twice should return 'already deployed, no-op', not create a duplicate."
--dry-run: "Agents should be able to preview what a deploy or deletion would do before committing."
Fail fast: "If a required flag is missing, don't hang. Error immediately and show the correct invocation. Agents are good at self-correcting when you give them something to work with."
Input hardening validation table (defense-in-depth against hallucinated/malformed input):
  File paths -> canonicalize and sandbox to CWD
  Control characters -> reject anything below ASCII 0x20
  Resource IDs -> reject ? and # characters
  URL encoding -> reject % to prevent double-encoding
  Path segments -> percent-encode at HTTP layer
"Core principle: The agent is not a trusted operator. Build CLI input validation like you'd build a web API, assuming adversarial input." -- i.e., strict rejection, not silent auto-correction, for hallucinated/malformed input.
Non-interactive: "If your CLI drops into a prompt mid-execution, an agent is stuck... Every input should be passable as a flag."
