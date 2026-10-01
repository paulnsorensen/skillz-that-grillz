# Codex 0.154 skill discovery and isolation

OpenAI's Codex source files at `rust-v0.154.0`, verified 2026-09-28, distinguish skill discovery from generated-command sandboxing.
Canonical source: [host_roots.rs](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/skills/src/host_roots.rs).
The exact source title is `host_roots.rs`; the publisher is OpenAI.

## Host discovery does not follow command permissions

Codex 0.154 discovers user skills through configuration layers before generated commands run.
The `--ignore-user-config` option retains an empty User layer.
That layer still contributes `CODEX_HOME/skills` and the user's `.agents/skills` directory.[^loader][^roots]
A command sandbox that denies those directories does not itself prove host discovery isolation.

The `skills.bundled.enabled=false` setting excludes bundled System skills, not Admin skills.
The `project_root_markers=[]` setting confines repository skill-root scanning to the working directory.[^service][^roots]
Exact `skills.config` disable rules run after discovery.
They cannot prove that discovery never reads host skill metadata.[^rules][^service]

## Permission selection differs between commands

Codex 0.154 sandbox accepts `-P`, but `codex exec` rejects that argument.
Define `default_permissions="skillz"` with the named permission profile for exec and app-server.
A model-free `exec --help` parser check catches unsupported execution arguments before a charged invocation.[^local]

## Verification requirements for skill experiments

Skillz experiments need a separate host-discovery check and generated-command isolation check.
Codex's model-free `skills/list` API can report skill paths, scopes, and enabled states.
A separate app-server check proves only its own configuration snapshot, not an arbitrary later `exec` process.[^tests]
The `skip_host_skill_discovery` feature is conditional and under development.
Its name alone does not establish an isolation guarantee.[^feature]

An isolated home and isolated Codex configuration directory avoid inherited user skill roots.
Any reuse of an existing login must keep credentials outside the candidate command sandbox.
This is an implementation requirement, not evidence that a live self-test succeeds.
See [session-driven skill prompt and CLI optimization](../ideas/session-driven-skill-optimization.md) for the evaluation contract.

[^local]: Installed Codex CLI 0.154.0 help and parser probes, 2026-09-28; shared configuration and parser preflight in `lib/src/skillz_experiments/_codex.py:24-109`.

[^roots]: https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/skills/src/host_roots.rs#L74-L199
[^loader]: https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/config/src/loader/mod.rs#L501-L517
[^service]: https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/skills/src/host_service.rs#L199-L349
[^rules]: https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/config/src/skills_config.rs#L82-L115
[^tests]: https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/app-server/tests/suite/v2/skills_list.rs#L133-L180
[^feature]: https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/features/src/lib.rs#L222-L225

_Source: OpenAI Codex rust-v0.154.0 primary source files · Updated: 2026-09-28_
