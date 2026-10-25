# Search snippets: oclif, mcp-cli, mcporter, mcp2cli, agent-friendly CLI principles (tavily search, 2026-09-23)

## oclif/core (github.com/oclif/core)
Node.js Open CLI Framework, built by Salesforce. Has custom flag/argument parser. gh: stars=316, last push 2026-09-22. No explicit agent-specific --json flag found on the core repo page itself in this search pass; oclif is referenced by trevinsays.com as one of the frameworks a CLI-linter tool is 'framework-aware' of (Click, argparse, Cobra, clap, Commander, yargs, oclif, Thor), not confirmed as itself shipping agent-native features.

## mcp-cli (philschmid.de/mcp-cli, github chrishayuk/mcp-cli per mcpservers.org)
"Introducing MCP CLI: A way to call MCP Servers Efficiently" (philschmid.de). Compiles to standalone binary (Bun). Works with stdio and HTTP MCP servers. Commands: `mcp-cli info`, `mcp-cli info <server>`, `mcp-cli info <server> <tool>` (schema), `mcp-cli grep "<pattern>"` (search tools). Designed for AI coding agents (Gemini CLI, Claude Code). Connection pooling with lazy-spawn daemon (60s idle timeout). Structured error messages with recovery suggestions. Separate chrishayuk/mcp-cli project (per mcpservers.org) adds `mcp-cli cmd --tool ... --output ...` and chat/dashboard modes; unclear if same project as philschmid's - likely two distinct tools sharing the name "mcp-cli".

## MCPorter
Converts MCP servers into CLI commands/tools; built by an ex-engineer (Peter, per firecrawl.dev blog) who was hired by OpenAI to work on personal agents after building MCPorter. TS-based, runtime dependency on Bun, huge dependency list, supports OAuth + basic token auth, has SDK/daemons/auto config discovery (per HN thread comparing it to a competing zero-dep-binary tool).

## mcp2cli
Per firecrawl.dev blog: "mcp2cli (2.2K stars): converts any MCP server to CLI commands, claiming 96 to 99% token savings." Not independently verified via gh in this pass (budget).

## Composio CLI
Per firecrawl.dev blog: "Composio CLI (29K stars): 1,000+ toolkits with lazy loading so you only load what you need." Not independently verified via gh in this pass.

## Agent-friendly CLI principles (pattern across blogs, not a single library)
- deployhq-cli (dev.to/martakar): --json flag, exit codes, self-documenting --help, stdout=data/stderr=decoration separation, SKILL.md + --json command catalog + --help as three discovery layers.
- speakeasy.com blog: --non-interactive flags to bypass prompts, --output json, --quiet to suppress progress/spinners; recommends writing "skills" that teach agents tool patterns.
- trevinsays.com "7 Principles for Agent-Friendly CLIs": --json on all data-bearing commands, exit code 0/non-zero, stdout=data/stderr=diagnostics, suppress ANSI color/spinners off-TTY; mentions a CLI-linter tool that is framework-aware across Click, argparse, Cobra, clap, Commander, yargs, oclif, Thor (tool name not captured, low confidence).

## Kraken CLI (kraken.com/kraken-cli)
"AI-native trading infrastructure for agents and developers." `kraken mcp -s all` exposes all commands via MCP; `kraken <command> -o json` for JSON output from any agent or script.

## opencli (skillsllm.com listing, 14.1k stars per site, unverified via gh)
Aggregates CLIs for many services (gh, obsidian, docker, lark-cli, dingtalk, wecom, vercel) into one "opencli" wrapper; ships an "opencli-operate" skill for browser control; designed to be auto-discovered via AGENT.md/.cursorrules.
