# Research: Agent skill CLI packaging

## Finding

Primary guidance separates reusable code from task instructions. Anthropic identifies deterministic operations as suitable for code execution.[^anthropic]
The Agent Skills specification defines staged loading and skill-root-relative resource paths.[^spec]
Its script guide covers concise help, bounded structured output, diagnostic separation, explicit inputs, and safeguards.[^scripts]
These sources support interface principles. They do not establish wedge or fromargs behavior.

## Plan and scope

- Decision: Supply evidence for a skill that teaches CLI packaging.
- Constraints: Primary sources only; no source or skill edits.
- Subqueries: Code boundaries; discovery and relocation; output and errors; unattended safety.
- Stop criterion: Obtain inspected primary guidance for each topic.
- Freshness: Sources checked on 2026-09-28.
- Parent owns local package research and repository decisions.

## Routing

| Capability | Required | Provider |
| --- | --- | --- |
| Library/API documentation | Yes | Native web, official Python documentation |
| Current-web discovery/extraction | Yes | Native web search and open |
| Repository knowledge/wiki | No | Parent owns this work |
| Local code intelligence | Yes | Tilth, existing research and layout only |
| Git hosting/examples | No | Not needed |

Python documentation returned a page header without the needed body. No Python API claim uses that result.

## Evidence

| Claim | Evidence | Source type | Freshness | Confidence | Caveat |
| --- | --- | --- | --- | --- | --- |
| Deterministic operations can run as bundled code rather than generated prose. | Code execution discussion, source lines 49-54; raw/03-anthropic.md:5 [^anthropic] | Vendor engineering | 2026-09-28 | `certain` | This describes the vendor's rationale, not a universal reliability guarantee. |
| Skill metadata, instructions, and resources load in stages. | Specification, source lines 262-268; raw/01-specification.md:5 [^spec] | Format specification | 2026-09-28 | `certain` | A host must implement the format. |
| Resource paths are relative to the skill root. | Specification, source lines 273-285; raw/01-specification.md:6 [^spec] | Format specification | 2026-09-28 | `certain` | This does not prove a particular host's shell working directory. |
| Concise help exposes flags and usage examples. | Script guide, source lines 257-273; raw/02-using-scripts.md:5 [^scripts] | Maintainer guidance | 2026-09-28 | `certain` | The guide does not prescribe a command schema. |
| Structured data belongs on stdout; diagnostics belong on stderr. | Script guide, source lines 289-300; raw/02-using-scripts.md:6 [^scripts] | Maintainer guidance | 2026-09-28 | `certain` | JSON is one supported format. |
| Large responses need a default bound, pagination, or explicit file output. | Script guide, source lines 311-312; raw/02-using-scripts.md:7 [^scripts] | Maintainer guidance | 2026-09-28 | `certain` | No universal size limit follows. |
| Scripts accept explicit inputs instead of interactive prompts. | Script guide, source lines 241-251; raw/02-using-scripts.md:8 [^scripts] | Maintainer guidance | 2026-09-28 | `certain` | This concerns unattended execution. |
| Errors explain the problem, expected value, and repair; exit meanings need documentation. | Script guide, source lines 278-284 and 309; raw/02-using-scripts.md:9 [^scripts] | Maintainer guidance | 2026-09-28 | `certain` | Numeric exit assignments remain application-specific. |
| State changes need explicit safeguards; dry-run and retry-safe behavior are considerations. | Script guide, source lines 305-310; raw/02-using-scripts.md:10 [^scripts] | Maintainer guidance | 2026-09-28 | `certain` | A confirmation flag does not establish authorization. |

## Open questions

- Which wedge and fromargs versions define the intended local contract? `don't know`
- Which commands produce bounded JSON, and which produce files? `speculating`
- Which state changes require approval rather than ordinary command flags? `speculating`
- Which host resolves skill paths without changing the target repository directory? `speculating`

## Confidence

`certain` for the cited sources' stated guidance.
`don't know` for wedge/fromargs behavior; the parent owns that evidence.
No provider substitution weakens the inspected primary sources.

## Capture and limits

The ledger records two search queries and six retrievals: eight operations against a budget of twelve.
Three retrieved bodies support claims. Three other retrievals returned headers or irrelevant page sections.
No claim uses those partial retrievals.
Raw files contain concise capture notes, not complete copyrighted page copies.
The prior local research report provides leads only; this report does not inherit its claims.

The requested directory is `docs/research/skill-cli-packaging/`.
The layout helper rejects the three-word slug.
The valid slug `agent-skill-cli-packaging` resolves outside the writable roots.
This report follows the parent's explicit repository path and records that deviation.
The resolved durable report path is
`/home/paul/.local/share/cheese/paulnsorensen-skillz-that-grillz/research/agent-skill-cli-packaging/agent-skill-cli-packaging.md`.

## Agent resolution

```yaml
agent_resolution:
  request:
    work: Inspect primary guidance for agent skill CLI packaging
    preferred_types: [researcher]
    required_tools: [native-web, tilth]
    permissions: write
    isolation: fresh-context
    minimum_power: default
    effort: medium
  attempts:
    - type: researcher
      model: gpt-6-sol
      power: default
      result: rejected
      reason: Parent reports that the configured model is unsupported.
    - type: default
      model: unknown
      power: unknown
      result: accepted
      reason: Final fallback has required tools and scoped artifact write access.
  resolved:
    type: default
    model: unknown
    power: unknown
    effort: medium
    topology: parallel
  fallback_reason: Named researcher cannot start with its configured model.
  degraded: true
  permission_enforcement: tool-restricted
```

Artifact scope is limited by the task; filesystem permissions enforce the outer workspace boundary.

## Next step

Return this evidence to the parent for integration.
The parent runs the repository gate after integration.

## References

[^spec]: https://agentskills.io/specification (fetched 2026-09-28).
[^scripts]: https://agentskills.io/skill-creation/using-scripts (fetched 2026-09-28).
[^anthropic]: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills (fetched 2026-09-28).
