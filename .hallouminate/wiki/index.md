# skillz-that-grillz wiki

A skills-only repository of [Agent Skills](https://agentskills.io/specification) for
the everyday plumbing around a project — working a GitHub PR, cutting a release,
scaffolding a justfile, wiring prek hooks, and writing idiomatic Bash. Each skill is a
self-contained `SKILL.md` under `skills/<name>/`; there are no agents, no orchestration,
and no required MCP servers.

## Conventions

One page per durable decision or gotcha. Capture *why*, not *what* — the README's
`## Skills` table already documents what each skill does. Link pages with `[[stem]]`.

- [[quality-gate]] — why verification funnels through a single `just build` / `just ci`
  gate and the in-repo Python validators, not ad-hoc lint commands.

## Sections

To be filled as pages are added.
