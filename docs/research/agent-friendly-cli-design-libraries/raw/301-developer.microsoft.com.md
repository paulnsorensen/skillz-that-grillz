# Don't rewrite your CLI for agents (Waldek Mastykarz, Microsoft)
URL: https://developer.microsoft.com/blog/dont-rewrite-your-cli-for-agents
Fetched: 2026-09-23
We used two separate CLIs as the test subjects. One accepts only individual args, the other accepts only a --json payload. Both share the same validation backend and normalize to the same canonical structure.
Keep your args. They work across the model capability spectrum and don't have environment-dependent failure modes. They also cost fewer tokens and money.
If you want to offer a --json option for programmatic use or batch operations, that's fine. But don't remove args in favor of JSON, and don't expect JSON to improve agent outcomes. In this experiment, JSON never improved correctness and always increased cost.
