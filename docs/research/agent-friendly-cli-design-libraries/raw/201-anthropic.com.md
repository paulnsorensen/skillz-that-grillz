# Anthropic: Writing effective tools for agents (with agents)
URL: https://www.anthropic.com/engineering/writing-tools-for-agents
Fetched: 2026-09-23
Published: Sep 11, 2025

Key excerpts:
"Tools represent a fundamentally new software paradigm: contracts between deterministic systems and non-deterministic agents... agents may hallucinate, misunderstand purposes, or call tools incorrectly."
"If a tool call raises an error (for example, during input validation), you can prompt-engineer your error responses to clearly communicate specific and actionable improvements, rather than opaque error codes or tracebacks."
"tool implementations should take care to return only high signal information back to agents. They should prioritize contextual relevance over flexibility, and eschew low-level technical identifiers (uuid, 256px_image_url, mime_type)."
