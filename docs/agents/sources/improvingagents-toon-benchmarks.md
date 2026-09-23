# TOON Benchmarks

Improving Agents' independent benchmark blog post, published November 2025 and last verified 2026-09-23, supports the claim that LLMs read TOON less accurately than JSON in the tests it ran.
Canonical source: [TOON Benchmarks](https://www.improvingagents.com/blog/toon-benchmarks)

## TOON Benchmarks results for tabular and nested data

The Improving Agents TOON Benchmarks post tests how well LLMs retrieve facts from data encoded in TOON and in better-known formats.

- Tabular data, GPT-4.1-nano, 12 formats: TOON scores 47.5% (95% CI 44.4–50.6%) with 21,518 tokens. JSON scores 52.3% with 66,396 tokens. Markdown-KV is best at 60.7%.
- Nested data, GPT-5-nano, 5 formats: TOON scores 43.1%, the lowest. JSON scores 50.3%. YAML is best at 62.1%.
- The authors re-ran the TOON project's own benchmark, reviewed its code, and got results similar to the vendor's. They "failed to find circumstances where TOON was the best-performing format" in their own tests.

## Limitations of the TOON Benchmarks post

Each Improving Agents test uses one small model, so the results can differ on larger models. The conflict with the vendor benchmark appears to come from dataset and prompt differences, not from errors in either benchmark.

_Source: https://www.improvingagents.com/blog/toon-benchmarks · Updated: 2026-09-23_
