# dev.to: Zero Trust for AI Agents? Google Workspace CLI's Design Philosophy (secondary commentary on Poehnelt post)
URL: https://dev.to/akari_iku/zero-trust-for-ai-agents-google-workspace-clis-design-philosophy-46k1
Fetched: 2026-09-23
"Humans make typos. AI hallucinates. The failure modes are fundamentally different. A human won't type ../../.ssh by accident. An agent will hallucinate path traversals by confusing contexts. A human misspells a resource ID. An agent embeds query parameters inside an ID string."
"Modern LLMs are remarkably good at inferring human intent from typos. But intent inference creates its own class of conflicts... In multi-agent architectures, when Agent A passes a task to Agent B, A's hallucination becomes B's valid input... Hence the principle: validate at every interface boundary."
"a skill file is cheaper than one hallucination" (100+ SKILL.md files ship alongside CLI to encode invariants like 'always use dry-run').
