# Docs guide

This directory holds working notes for the maintainers and their coding
agents. It is not user documentation. For install steps and usage, read the
[root README](../README.md).

Agents write and maintain most of these notes. Expect dense, terse, and
sometimes out-of-date text. The code and the tests are the source of truth.

Start at the [agent wiki index](agents/index.md). It lists every page.

| Path | What it holds |
| --- | --- |
| [`agents/decisions/`](agents/decisions/index.md) | Architecture decision records (ADRs). Each ADR states a design choice and its reason. Code and guards cite ADRs by id. |
| [`agents/criteria/`](agents/criteria/index.md) | Acceptance criteria for a ticket or feature. Each row maps to a test marked `@pytest.mark.ac(...)`. |
| [`agents/research/`](agents/research/index.md) | Research notes that back a design choice. |
| [`agents/strategy/`](agents/strategy/index.md) | Strategy and planning notes. |
| [`agents/ideas/`](agents/ideas/index.md) | Ideas that the project has not yet adopted. |
| [`agents/log.md`](agents/log.md) | The ingest log. It records what the wiki added, merged, or skipped. |
| [`research/`](research) | Longer research write-ups and prototype plans. |
