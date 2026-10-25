URL: https://www.improvingagents.com/blog/toon-benchmarks
Fetched: 2026-09-23 (tavily_extract, advanced depth)
Status: independent third-party benchmark org (improvingagents.com), not affiliated with toon-format

Test 1 (Tabular data, GPT-4.1-nano, 12 formats, 95% CI):
| Format | Accuracy | 95% CI | Tokens |
|---|---|---|---|
| Markdown-KV | 60.7% | 57.6-63.7% | 52,104 |
| XML | 56.0% | 52.9-59.0% | 76,114 |
| INI | 55.7% | 52.6-58.8% | 48,100 |
| YAML | 54.7% | 51.6-57.8% | 55,395 |
| HTML | 53.6% | 50.5-56.7% | 75,204 |
| JSON | 52.3% | 49.2-55.4% | 66,396 |
| Markdown-Table | 51.9% | 48.8-55.0% | 25,140 |
| Natural-Language | 49.6% | 46.5-52.7% | 43,411 |
| TOON | 47.5% | 44.4-50.6% | 21,518 |
| JSONL | 45.0% | 41.9-48.1% | 54,407 |
| CSV | 44.3% | 41.2-47.4% | 19,524 |
| Pipe-Delimited | 41.1% | 38.1-44.2% | 43,098 |

Test 2 (Nested data, GPT-5-nano):
| Format | Accuracy | 95% CI | Tokens |
|---|---|---|---|
| YAML | 62.1% | 59.1-65.1% | 42,477 |
| Markdown | 54.3% | 51.2-57.4% | 38,357 |
| JSON | 50.3% | 47.2-53.4% | 57,933 |
| XML | 44.4% | 41.3-47.5% | 68,804 |
| TOON | 43.1% | 40.0-46.2% | 45,436 |

> On the one hand, in our tests, we failed to find circumstances where TOON was the best-performing format... On the other, in the tests provided in the TOON GitHub repo, TOON performed well. ... We have run those tests ourselves and found similar results. We've also reviewed the code for the tests and it looks good to us.
