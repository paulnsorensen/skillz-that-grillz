URL: https://gofastmcp.com/cli/generate-cli ; https://gofastmcp.com/cli/overview
Fetched: 2026-09-23

> fastmcp generate-cli goes further: it connects to a server, reads its tool schemas, and writes a standalone Python script where every tool is a proper subcommand with typed flags, help text, and tab completion. The result is a CLI that feels hand-written for that specific server. MCP tool schemas already contain everything a CLI framework needs.

> | generate-cli | Scaffold a standalone typed CLI from a server's tool schemas |

FastMCP (PrefectHQ/fastmcp, 27,876 stars, pushed 2026-09-22) is an MCP *server/client* framework, not a general CLI-argument-parsing framework like Typer/Click. Its CLI feature goes the opposite direction from the researched incur pattern: it generates a CLI FROM an MCP server's tool schemas (fastmcp generate-cli), rather than exposing an existing CLI's commands AS MCP tools.
