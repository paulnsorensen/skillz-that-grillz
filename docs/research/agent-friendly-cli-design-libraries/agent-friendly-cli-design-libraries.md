## Research: CLI libraries for agents, and what agents prefer in a CLI

### Finding

A small number of agent-first CLI frameworks exist. The most explicit is wevm `incur` (TypeScript). It defaults to TOON output, has `--llms`, `--schema`, and `--mcp` modes, and has token-limit flags[^incur]. The best-known agent-first CLI product is Google Workspace `gws` (Rust)[^gws]. It follows Justin Poehnelt's "rewrite your CLI for AI agents" design[^poehnelt]. Mainstream frameworks cover parts of this surface. oclif has an opt-in `enableJsonFlag`. Cobra and clap print "did you mean" suggestions by default[^fw].

Sources agree on JSON output. Poehnelt calls `--output json` "table stakes"[^poehnelt]. Speakeasy and an independent guide give the same advice[^speakeasy][^trevin]. Sources disagree on JSON input. Poehnelt says raw `--json` payloads beat bespoke flags[^poehnelt]. A controlled Microsoft experiment found that args-only input was never less correct than `--json`-only input, and that args-only input always cost less[^msft].

No source examined recommends silent auto-correction of arguments. Sources recommend strict validation with actionable errors instead. Anthropic says errors should give "specific and actionable improvements"[^anthropic]. Poehnelt says "assume adversarial input"[^poehnelt]. One guide says a good error "suggests valid values" so that the agent can "self-correct in one retry"[^trevin].

No Python framework examined bundles the incur feature set. Each feature exists only as a separate component. Typer suggests close command names by default (`suggest_commands=True`), and Click passes option `possibilities` to its errors[^pyfw]. argparse added opt-in `suggest_on_error` in Python 3.14[^argparse]. `click-to-mcp` wraps a Click or Typer CLI as an MCP server, but it has 7 stars[^click2mcp]. `toon-python` is a standalone TOON encoder[^toon]. FastMCP goes in the reverse direction: `fastmcp generate-cli` creates a typed CLI from an MCP server[^fastmcp].

### Evidence

| Claim | Evidence | Source type | Freshness | Confidence | Caveat |
| --- | --- | --- | --- | --- | --- |
| incur is a CLI framework for agents and humans with TOON default, `--llms`, `--mcp`, `--schema`, and `--token-limit` | raw/101-github.com-wevm-incur.md#L5-L18 [^incur] | Git host | 2026-09-23 | `certain` | 620 stars, incur@0.5.1 on 2026-08-14 |
| gws is built for humans and AI agents with structured JSON output and 40+ agent skills | raw/103-github.com-googleworkspace-cli.md#L2-L11 [^gws] | Git host | 2026-09-23 | `certain` | 31,113 stars |
| oclif supports opt-in `--json` output through `enableJsonFlag` | raw/303-github.com-oclif-cobra-clap.md#L2-L3 [^fw] | repo code | 2026-09-23 | `certain` | Off by default |
| Cobra and clap print typo suggestions by default (Levenshtein or strsim) | raw/303-github.com-oclif-cobra-clap.md#L4-L8 [^fw] | repo code | 2026-09-23 | `certain` | Suggestion only, no auto-correction |
| `--output json` is table stakes for agent use | raw/302-justin.poehnelt.com.md#L7-L8 [^poehnelt]; raw/205-speakeasy.com.md#L6 [^speakeasy]; raw/204-trevinsays.com.md#L5 [^trevin] | blog | 2026-09-23 | `certain` | Three independent origins |
| Raw JSON payload input is better than bespoke flags for agents | raw/302-justin.poehnelt.com.md#L4-L6 [^poehnelt] | blog | 2026-09-23 | `don't know` | Conflicts with the next row |
| JSON-only input never improved correctness and always increased cost compared to args-only input | raw/301-developer.microsoft.com.md#L4-L6 [^msft] | vendor experiment | 2026-09-23 | `don't know` | One experiment; conflicts with the previous row |
| Actionable errors let agents self-correct | raw/201-anthropic.com.md#L8 [^anthropic]; raw/204-trevinsays.com.md#L7 [^trevin] | vendor docs / blog | 2026-09-23 | `speculating` | Two origins |
| Strict validation of agent input is preferred (assume adversarial input) | raw/302-justin.poehnelt.com.md#L9 [^poehnelt] | blog | 2026-09-23 | `speculating` | Single origin |
| CLI plus skills uses much fewer tokens than direct MCP (1,365 compared to 44,026 in one task) | raw/207-scalekit.com.md#L5 [^scalekit] | vendor benchmark | 2026-09-23 | `speculating` | Vendor-run benchmark |
| Typer suggests close command names by default; Click passes option `possibilities` to `NoSuchOption` | raw/408-github.com-click-typer.md#L2-L6 [^pyfw] | repo code | 2026-09-23 | `certain` | Suggest only |
| argparse `suggest_on_error` is opt-in, new in Python 3.14 | raw/404-docs.python.org-argparse.md#L4-L5 [^argparse] | vendor docs | 2026-09-23 | `certain` | Default False |
| click-to-mcp wraps a Click or Typer CLI as an MCP server | raw/401-mcpservers.org.md#L4 [^click2mcp] | Git host | 2026-09-23 | `speculating` | 7 stars |
| toon-python is a standalone TOON encoder and decoder library | raw/403-github.com-toon-format-toon-python.md#L1-L4 [^toon] | Git host | 2026-09-23 | `certain` | 768 stars |
| `fastmcp generate-cli` creates a typed CLI from an MCP server's tool schemas | raw/407-gofastmcp.com.md#L4-L6 [^fastmcp] | vendor docs | 2026-09-23 | `certain` | Reverse direction from incur |
| No Python framework examined has a built-in `--llms`, `--mcp`, or TOON output mode | raw/405-github.com-dancardin-cappa.md#L13 [^cappa]; raw/406-cyclopts.readthedocs.io.md#L11 [^cyclopts] | Git host / docs | 2026-09-23 | `speculating` | Absence claim; scoped to Typer, Click, argparse, cyclopts, Cappa, tyro, Fire, Clipstick |
| cyclopts prints "Did you mean" for unknown options, invalid choices, unknown commands, and misplaced options | raw/409-github.com-cyclopts-exceptions.md#L2-L5 [^cycsrc] | repo code | 2026-09-23 | `certain` | Suggest only; corrects the earlier absence note for cyclopts |

### TOON versus JSON

Independent evidence does not support the TOON vendor claim that models read TOON as well as JSON. The TOON benchmark reports 72.2% accuracy for TOON and 71.4% for JSON[^toonbench]. An independent group re-ran the vendor benchmark and got similar results. In its own tests, TOON scored below JSON on both tabular and nested data[^improving]. An independent arXiv paper on agent tool calling reports a 9-point accuracy cost for TOON, plus parse failures that cascade in multi-turn runs[^notation]. No source examined describes TOON training or TOON constrained decoding at OpenAI, Anthropic, or Google; their structured-output features use JSON Schema[^vendors].

| Claim | Evidence | Source type | Freshness | Confidence | Caveat |
| --- | --- | --- | --- | --- | --- |
| Vendor benchmark: TOON 72.2% compared to JSON 71.4%, with 42.6% fewer tokens | raw/501-toonformat.dev.md#L5 [^toonbench] | vendor benchmark | 2026-09-23 | `speculating` | Vendor-run; plain JSON prompts, not JSON mode |
| Independent tabular test (GPT-4.1-nano): TOON 47.5% compared to JSON 52.3% | raw/502-improvingagents.com.md#L13-L16 [^improving] | independent benchmark | 2026-09-23 | `speculating` | One model; single origin |
| Independent nested test (GPT-5-nano): TOON 43.1%, lowest of 5 formats; JSON 50.3% | raw/502-improvingagents.com.md#L26-L28 [^improving] | independent benchmark | 2026-09-23 | `speculating` | One model; single origin |
| The same group re-ran the vendor benchmark and found similar results | raw/502-improvingagents.com.md#L30 [^improving] | independent benchmark | 2026-09-23 | `speculating` | Results depend on dataset and prompt |
| In agent tool calling, TOON cuts tokens by up to 18% at about a 9-point accuracy cost; parse failures cascade in multi-turn runs | raw/503-arxiv-notation-matters.md#L6-L8 [^notation]; raw/508-arxiv.org-abs.md#L1-L6 [^notation] | paper | 2026-09-23 | `speculating` | Abstract only; open-weight models |
| Structured-output features at OpenAI, Anthropic, and Google use JSON Schema | raw/507-structured-outputs-vendors.md#L4-L8 [^vendors] | secondary summary | 2026-09-23 | `speculating` | TOON absence is scoped to the sources checked |
| Format choice changes accuracy by up to 40% on GPT-3.5; larger models are more robust | raw/504-arxiv-prompt-formatting.md#L5 [^fmt] | paper | 2026-09-23 | `certain` | Predates TOON |
| Strict output-format constraints reduce reasoning accuracy | raw/505-arxiv-let-me-speak-freely.md#L5 [^free] | paper | 2026-09-23 | `certain` | Covers JSON and XML, not TOON |
| An open TOON issue asks for a benchmark against JSON structured-output endpoints | raw/506-toonformat-github-issues.md#L4 [^toonissues] | Git host | 2026-09-23 | `certain` | Issue #19 |

### Open questions

- Input model: raw `--json` payloads (Poehnelt) or flat args (Microsoft)? `speculating`
- Output format: JSON, NDJSON, or TOON? Independent tests put TOON below JSON for model comprehension; TOON uses fewer tokens. `speculating`
- Typo handling: suggest-only (Cobra/clap default) or auto-correct? No source examined measured auto-correction for agents. `speculating`

### Confidence

`speculating` — library facts and JSON-output consensus are well supported; the input-format question has conflicting evidence, no source examined tested auto-correction, and the independent TOON results conflict with the vendor benchmark.

### Next step

Run `/mold` if you plan to build or change an agent-facing CLI.

### Searched, empty

- Current web via Tavily, "agent-friendly CLI design JSON output --json flag error messages did you mean" → no source about fuzzy auto-correction of command or flag names for agents.
- Current web via Tavily, "Cobra Click Typer clap CLI designed for AI agents llms.txt skill" → no maintainer documentation for agent-specific features in these frameworks.

## References

[^incur]: https://github.com/wevm/incur (fetched 2026-09-23).
[^gws]: https://github.com/googleworkspace/cli (fetched 2026-09-23).
[^fw]: https://github.com/oclif/core, https://github.com/spf13/cobra, https://github.com/clap-rs/clap (fetched 2026-09-23).
[^poehnelt]: https://justin.poehnelt.com/posts/rewrite-your-cli-for-ai-agents/ (fetched 2026-09-23).
[^msft]: https://developer.microsoft.com/blog/dont-rewrite-your-cli-for-agents (fetched 2026-09-23).
[^speakeasy]: https://www.speakeasy.com/blog/engineering-agent-friendly-cli (fetched 2026-09-23).
[^trevin]: https://trevinsays.com/p/7-principles-for-agent-friendly-clis (fetched 2026-09-23).
[^anthropic]: https://www.anthropic.com/engineering/writing-tools-for-agents (fetched 2026-09-23).
[^scalekit]: https://www.scalekit.com/blog/mcp-vs-cli-use (fetched 2026-09-23).
[^pyfw]: https://github.com/pallets/click, https://github.com/fastapi/typer (fetched 2026-09-23).
[^argparse]: https://docs.python.org/3/library/argparse.html (fetched 2026-09-23).
[^click2mcp]: https://github.com/Coding-Dev-Tools/click-to-mcp (fetched 2026-09-23).
[^toon]: https://github.com/toon-format/toon-python (fetched 2026-09-23).
[^fastmcp]: https://gofastmcp.com/cli/generate-cli (fetched 2026-09-23).
[^cappa]: https://github.com/DanCardin/cappa (fetched 2026-09-23).
[^cyclopts]: https://cyclopts.readthedocs.io (fetched 2026-09-23).
[^cycsrc]: https://github.com/BrianPugh/cyclopts (fetched 2026-09-23).
[^toonbench]: https://toonformat.dev/guide/benchmarks.html (fetched 2026-09-23).
[^improving]: https://www.improvingagents.com/blog/toon-benchmarks (fetched 2026-09-23).
[^notation]: https://arxiv.org/abs/2605.29676 (fetched 2026-09-23).
[^vendors]: https://niteagent.com/blog/2026-06-04-structured-outputs-across-providers (fetched 2026-09-23).
[^fmt]: https://arxiv.org/abs/2411.10541 (fetched 2026-09-23).
[^free]: https://arxiv.org/abs/2408.02442 (fetched 2026-09-23).
[^toonissues]: https://github.com/toon-format/toon/issues (fetched 2026-09-23).
