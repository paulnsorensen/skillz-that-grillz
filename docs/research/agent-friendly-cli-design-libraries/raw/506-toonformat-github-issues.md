URL: https://github.com/toon-format/toon/issues
Fetched: 2026-09-23 (gh search issues --repo toon-format/toon benchmark)

Issue #19 "Benchmark against Structured Output Endpoints" (open): a commenter asks the maintainer to benchmark TOON against providers' native structured-output/JSON-mode endpoints (fine-tuned for valid JSON) rather than plain-JSON prompting, implying the official benchmark did not do this.

Issue #2 "Retrieval Accuracy Benchmarks" (closed): "Any plans for testing the retrieval against different LLMs + control? Maybe LLMs are not fit for the format and may hurt retrieval (speculation)" -- raised before benchmarks existed.

Issue #207 "TOON benchmark for generation tasks" (closed): a community member describes an independent-style generation benchmark (21 models via Nebius API) comparing JSON vs JSON+Structured-Output(constrained decoding) vs TOON on one-shot accuracy, repair-cycle accuracy, and token budget; explicitly separates "TOON strength area" (uniform arrays) from a weak nested/arrays-within-arrays case. Results not summarized in the issue thread text captured.

Issue #278 "High-impact adoption opportunity: Anthropic's Claude Code CLI tool definitions" (closed, community-filed): community member filed a *feature request* on anthropics/claude-code (issue #24747) proposing TOON for tool-schema definitions to save ~39% of the ~16.5k tokens of JSON schemas loaded per conversation. This is a community ask, not an Anthropic commitment -- evidence that as of the fetch date, Claude Code still uses JSON for tool schemas and TOON support is not officially adopted.

Issue #154 "Comparison to CSV" (closed): "Toon seems like a slightly modified csv. Did you compare benchmarks to csv?" -- notes CSV was later added as a comparison track (see toonformat.dev benchmarks, flat-only track).

Issue #310 (reShapr team, closed): third-party team sharing their own token/perf benchmarking, cautioning TOON "is not a 'silver bullet'" and integrating it as an opt-in tag rather than default; flags dataset bias (toon-format's own test cases lean on deeply nested JSON, tokenizer-specific optimization for Claude/GPT).
