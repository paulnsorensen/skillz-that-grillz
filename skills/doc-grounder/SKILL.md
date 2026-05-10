---
name: doc-grounder
model: haiku
description: >
  Ground a planning phase in current library documentation by combining Context7
  library lookup with Tavily search/extract of official docs, API references,
  changelogs, examples, and best practices. Use when the user says "research
  this library before we plan", "ground this plan with docs", "use Context7 and
  Tavily for docs", "what does the latest chezmoi API offer", "prepare a docs
  brief for using <library>", or asks for implementation planning that depends
  on unfamiliar or fast-moving library behavior. Do NOT use for implementing the
  change, generic web research unrelated to a library, or GitHub/CI operations.
allowed-tools: Write, WebSearch, mcp__context7__resolve-library-id, mcp__context7__query-docs, mcp__context7__get-library-docs, mcp__tavily__tavily-search, mcp__tavily__tavily-extract
context: fork
license: MIT
---

# doc-grounder

Prepare a planning-ready documentation brief for a library, framework, CLI, or
SDK before design or implementation starts.

## When to use

- The user wants to plan with current library docs instead of memory alone
- The target is version-sensitive, fast-moving, or unfamiliar
- The prompt names Context7, Tavily, `tavily-extract`, or asks to "ground" a plan
- The user asks what a library offers: APIs, commands, integrations, examples,
  best practices, migration notes, or gotchas

Do not start implementing. This skill stops after producing a concise docs brief
that another planning or coding step can use.

## Protocol

### 1. Pin the research target

Extract the library name, ecosystem, version constraint, and intended use case.
If the prompt is broad ("use this library"), infer a small set of likely topics
such as installation, core API/command surface, configuration, integration
points, examples, security caveats, and recommended workflows. Ask one clarifying question only when the target library
or use case is ambiguous enough that docs would fork in different directions.

### 2. Resolve with Context7 first

Use Context7 to find the canonical library ID and pull focused docs for the use
case. Query by topic instead of asking for everything at once:

- install / quickstart
- API or command reference
- configuration
- best practices
- migration / version notes
- security or operational caveats

Context7 gives structured library docs quickly. Treat it as the spine of the
brief, but verify freshness and official-source coverage with Tavily.

### 3. Use Tavily for source coverage

Use Tavily search to find official documentation, API reference pages, release
notes, changelogs, examples, and best-practice guides. Prefer sources in this
order:

1. Official docs site
2. Official repository docs
3. Official package registry page
4. Maintainer-authored blog or release announcement
5. Community examples only when official docs are missing

Then use `tavily-extract` on the selected URLs. Extract only pages that answer
the planning question; do not bulk-download an entire docs site unless the user
explicitly asks for a local docs corpus.

### 4. Reconcile the sources

Compare Context7 and Tavily findings before summarizing:

- Prefer newer official docs when Context7 appears stale
- Call out version mismatches, renamed APIs, deprecated commands, or missing docs
- Separate documented guarantees from examples or conventions
- Note anything important that remains unverified

This prevents a plan from inheriting outdated signatures or cargo-cult examples.

### 5. Produce a planning brief

Return a compact brief with these sections:

1. **Target and freshness** — library ID, latest/versioned docs found, and source
   dates or release markers when available
2. **What it offers** — relevant APIs, commands, modules, configuration knobs,
   or extension points
3. **Recommended usage** — current best practices for the stated use case
4. **Planning implications** — constraints, integration steps, decisions to make,
   and risks the plan should account for
5. **Citations** — Context7 library ID plus Tavily-extracted official URLs
6. **Open questions** — only the gaps that would change the plan

If the user asked for a downloadable artifact, write the brief to the requested
path. Otherwise, keep it in the response so the next planning phase can consume
it directly.

## Quality bar

- Keep the brief selective; planning needs the relevant surface area, not a docs
  mirror.
- Cite every claim that could change across versions.
- Prefer official current docs over model memory.
- Mark MCP failures explicitly and fall back to WebSearch only when Tavily or
  Context7 is unavailable.
- Do not invoke implementation, commit, PR, or CI workflows from this skill.
