---
status: draft
owner: paulnsorensen
last_verified: 2026-09-23
confidence: medium
sources:
  - https://www.improvingagents.com/blog/toon-benchmarks
  - https://arxiv.org/abs/2605.29676
  - https://github.com/toon-format/toon
---
# TOON versus JSON for LLMs

Independent benchmarks show that LLMs read TOON less accurately than JSON, although TOON uses fewer tokens. No evidence shows that model vendors train on TOON or support it in constrained decoding; their structured-output features use JSON Schema. Only the TOON project's own benchmark shows TOON matching JSON.

## Vendor claim: TOON matches JSON with fewer tokens

The toon-format benchmark reports 72.2% accuracy for TOON and 71.4% for JSON, with 42.6% fewer tokens. It uses plain JSON prompts, not provider JSON mode. See [Token-Oriented Object Notation (TOON)](sources/toon-format-spec.md).

## Independent evidence: TOON accuracy is lower than JSON

Independent TOON benchmarks disagree with the vendor claim. Improving Agents' "TOON Benchmarks" post measured TOON at 47.5% and JSON at 52.3% on tabular data (GPT-4.1-nano). On nested data (GPT-5-nano), TOON scored 43.1%, the lowest of 5 formats, and JSON scored 50.3%. The same authors reproduced the vendor's results with the vendor's own benchmark, so the outcome depends on dataset and prompt. See [TOON Benchmarks](sources/improvingagents-toon-benchmarks.md).

## Generating TOON in agent tool calls

TOON output from models is less reliable than JSON output in agent tool calling. The "Notation Matters" preprint (arXiv:2605.29676) reports up to 18% token savings at about a 9-point accuracy cost. Parse failures cascade in multi-turn runs, and parallel tool-call output collapses for most models. See [Notation Matters](sources/notation-matters-token-optimized-formats.md).

## Why models handle JSON better than TOON

Models see far more JSON than TOON in training. OpenAI, Anthropic, and Google offer JSON Schema constrained decoding, and none of the sources examined describe a TOON equivalent. This absence claim covers only the structured-output guides checked on 2026-09-23. Format choice matters most for small models: one 2024 study (arXiv:2411.10541) found swings up to 40% on GPT-3.5 and more robustness on GPT-4.

## When TOON output is reasonable

TOON output fits a CLI best as an opt-in format for large, uniform lists, where token savings are largest. JSON stays the safer default for agent-facing output, and TOON is a poor choice for data that the model must write back. The TOON README says compact JSON often wins on nested or non-uniform data.

_Source: docs/research/agent-friendly-cli-design-libraries (/briesearch, 2026-09-23) · Updated: 2026-09-23_
