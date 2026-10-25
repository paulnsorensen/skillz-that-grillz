# Notation Matters: A Benchmark Study of Token-Optimized Formats in Agentic AI Systems

An arXiv preprint (arXiv:2605.29676, v2, cs.AI), last verified 2026-09-23, supports the claim that TOON costs accuracy compared to JSON in agent tool calling.
Canonical source: [Notation Matters: A Benchmark Study of Token-Optimized Formats in Agentic AI Systems](https://arxiv.org/abs/2605.29676)

## Notation Matters findings on TOON in agent tool calling

The Notation Matters paper compares JSON with token-optimized formats (TOON and TRON) on four tool-calling benchmarks (BFCL, MCPToolBenchPP, MCP-Universe, StableToolBench) with five open-weight LLMs.

- TOON cuts tokens by up to 18% at about a 9-point accuracy cost compared to JSON.
- TOON "loses accuracy further in multi-turn settings, where parsing failures cascade into additional reasoning iterations and erode per-call gains."
- TOON "collapses parallel tool-call output for most models."
- Structural correctness degrades for models without native TOON support.

## Limitations of the Notation Matters evidence

The claims come from the Notation Matters abstract and HTML excerpt; the full PDF was not read. The paper tests open-weight models only, and it is a preprint, not a peer-reviewed publication.

_Source: https://arxiv.org/abs/2605.29676 · Updated: 2026-09-23_
