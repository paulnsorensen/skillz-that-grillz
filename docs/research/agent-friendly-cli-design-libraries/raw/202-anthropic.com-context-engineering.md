# Anthropic: Effective context engineering for AI agents
URL: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
Fetched: 2026-09-23
"tools should be self-contained, robust to error, and extremely clear with respect to their intended use... it's extremely important that tools promote efficiency, both by returning information that is token efficient and by encouraging efficient agent behaviors."
"just in time" context approach: maintain lightweight identifiers (file paths, stored queries, web links) and dynamically load data at runtime. Claude Code uses head/tail Bash commands for this.
