# Hacker News: "You need to rewrite your CLI for AI agents" discussion
URL: https://news.ycombinator.com/item?id=47252459
Fetched: 2026-09-23
Disagreement captured: user mahendra0203 pushes back on the raw-JSON-over-flags argument: "Honestly? Don't think you don't need to rewrite your CLI. The whole 'raw JSON > flags' argument sounds clean in a blog but falls apart in practice. Agents are actually good at parsing messy human output. stderr, exit codes, even tables. I run AI agents against CLIs every day. The failures are almost never, 'the output wasn't [JSON]'..." (comment truncated in extract).
This directly conflicts with Microsoft's controlled-experiment conclusion (203) that args beat --json, and with Poehnelt's implied JSON/structured-output preference -- three-way disagreement on whether flags, JSON, or "agents tolerate messy text fine" wins.
