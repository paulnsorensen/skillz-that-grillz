URL: https://mcpservers.org/servers/coding-dev-tools/click-to-mcp
Fetched: 2026-09-23

> Auto-wrap any Click/typer CLI as an MCP (Model Context Protocol) server. Introspects CLI commands at runtime and maps them to MCP tools.

> Add an MCP server entry point to any Click/typer CLI:
> # cli.py — add a subcommand to run as MCP server
> import typer
> from click_to_mcp import run
> app = typer.Typer(...)
> @app.command()
> def mcp():
>     """Run as an MCP server over stdio."""
>     from click_to_mcp import run
>     run(app)

> Then agents can use it as: `your-cli mcp`
