URL: https://arxiv.org/html/2605.29676v2
Fetched: 2026-09-23 (tavily search snippet)
Title: Notation Matters: A Benchmark Study of Token-Optimized Formats in Agentic AI Systems
Status: independent peer-style arXiv paper, four benchmarks (BFCL, MCPToolBenchPP, MCP-Universe, StableToolBench), five open-weight LLMs

> TRON behaves as a drop-in replacement for JSON under input-only compression, reducing total tokens by up to 27% with accuracy within 14 pp of the JSON baseline... TOON yields up to 18% reduction at a similar 9 pp accuracy cost. However, TOON loses accuracy further in multi-turn settings, where parsing failures cascade into additional reasoning iterations and erode per-call gains.

> TOON additionally cascades on multi-turn parsing failures and collapses parallel tool-call output for most models.

> structural-correctness rates degrade for models without native support (Masciari et al., 2026)

This decouples comprehension (input) vs generation (output) side of TOON use, in agentic tool-calling context (not the same as the toon-format retrieval benchmark).
