# Microsoft Dev Blog: Don't rewrite your CLI for agents
URL: https://developer.microsoft.com/blog/dont-rewrite-your-cli-for-agents
Fetched: 2026-09-23

Counter-evidence to "rewrite with --json" thesis. Ran controlled experiment: same validation backend, two CLI variants (args-only vs --json-only), binary renamed to remove brand priors.
"With args, the --help text defines the exact set of valid inputs... With --json, the model gets a schema... must satisfy it. Each of those [json requirements] is a failure mode that args eliminate by design."
"If you're building a CLI that agents will use, the data points in a different direction than 'rewrite it with --json.' Keep your args. They work across the model capability spectrum and don't have environment-dependent failure modes. They also cost fewer tokens and money."
Conclusion: "plausible advice and measured outcomes are different things... when they disagree, the measurements win."
