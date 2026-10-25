# Source capture: Agent Skills specification

Source: https://agentskills.io/specification
Fetched: 2026-09-28. Excerpts re-checked against the live page on 2026-10-01. The source line numbers in the report refer to an extraction the repository does not keep.

- "Metadata (~100 tokens): The name and description fields are loaded at startup for all skills"
- "Instructions (< 5000 tokens recommended): The full SKILL.md body is loaded when the skill is activated"
- "Resources (as needed): Files (e.g. those in scripts/, references/, or assets/) are loaded only when required"
- "When referencing other files in your skill, use relative paths from the skill root:"
