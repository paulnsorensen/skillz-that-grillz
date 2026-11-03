# skillz harness ergonomics decisions

These records explain how the skillz experiment runner at `packages/skillz/src/skillz_experiments/` supports harness versions, subscription logins, and user-supplied runners.
The spec is `specs/harness-sandbox-ergonomics.md` in the durable cheese corpus.
The user settled each decision in a Mold session on 2026-10-11.
Research: `research/harness-sandbox-ergonomics-subscriptions` in the durable cheese corpus.

## Records

### ADR-001: Gate Codex on a minimum version plus behavior probes  [status: accepted]
- **Context:** The runner pinned `codex-cli 0.154.0` exactly, so it refused 0.160.0, although the model-free preflight passes on it. The check now lives in `MINIMUM_VERSION` at `packages/skillz/src/skillz_experiments/harness/_codex.py:34`. Codex silently ignores unknown `-c` keys; a misspelled `filesystem` key let a sealed host file be read in a local probe. `--strict-config` rejects unknown top-level keys on `codex exec` and `codex app-server`, but `codex sandbox` has no strict mode.
- **Decision:** Require Codex 0.154.0 or later. Pass `--strict-config` to `exec` and `app-server`. Keep every behavior probe. Record the observed version as `codex_version` in `run.json`; it enters the preflight evidence, so a resume on another version stops with `environment-differs`.
- **Alternatives:** No version gate and probes only (a renamed `web_search` or `features.*` key could silently enable a model-side tool that no probe checks). An exact pin with an override flag (still a pin).
- **Consequences:** New Codex releases run without a code change. A Codex upgrade mid-run needs a new run. The skill-discovery isolation review from the `rust-v0.154.0` source stays the design basis; the per-run `skills/list` check is the runtime proof.

### ADR-002: Drop the nono backend  [status: accepted; supersedes skillz-autoimprove ADR-007]
- **Context:** `--isolation nono` billed an API key, added an install, still needed `bwrap` on Linux for the helper probe, and was refused on macOS. ADR-006's `allowAllUnixSockets` fallback already lets `--isolation claude` run on stock Ubuntu 24.04 and later. No paid live run used nono. A nono subscription mode (`oauth_capture`) exists but has an open concurrency bug (nolabs-ai/nono#2087), a plaintext token store, and an unsettled reading of Anthropic's terms.
- **Decision:** Remove `_nono.py`, its `_claude.py` branches, `validate_isolation`, the doctor checks, tests, docs, and the `--isolation` option. Native sandboxes stay the subscription path: Claude Code reuses the Claude login and Codex reuses the ChatGPT login. A resume of a recorded nono run stops with a coded error that names issue #211.
- **Alternatives:** Keep nono as an opt-in API-key hardening backend (OS-level confinement of the file tools, at the cost of maintenance). Build the nono subscription mode with serialized model calls.
- **Consequences:** Less sandbox code to maintain. The Read tool stays confined by settings rules plus the live preflight probe, not by an OS boundary. Issue https://github.com/paulnsorensen/skillz-that-grillz/issues/211 tracks a later inspection.

### ADR-003: Let `run` take a harness configuration or an inline command  [status: accepted]
- **Context:** The `command` adapter and its JSON protocol existed, but only `self-test --preflight-only --harness-config` reached them. `run()` already accepted `configuration=`. The `run` command in `packages/skillz/src/skillz_experiments/_cli.py` now exposes it through `--harness-config`, `--harness command`, and `--command`.
- **Decision:** `run --harness-config <file>` loads the existing configuration schema. `run --harness command --command <argv...>` builds a one-role configuration. The two forms are mutually exclusive with each other's flags. A command role skips the built-in doctor host checks and must pass the runner's sandbox probe before any task call. A changed configuration on resume stops with `harness-changed`.
- **Alternatives:** The file form only (no "just a command" path). The inline form only (no per-role models).
- **Consequences:** A user can run any harness through a wrapper without a new built-in adapter. The runner's probe, not trust in the wrapper, remains the isolation proof.

### ADR-004: Support other harnesses through one tested wrapper and recipes  [status: accepted]
- **Context:** Gemini CLI, Copilot CLI, and Cursor CLI have headless JSON modes, read `.agents/skills`, and ship native sandboxes. OpenCode and Pi have no sandbox. A first-class adapter needs its own skill-isolation preflight, which was the hard part for Claude Code (skillz-autoimprove ADR-006).
- **Decision:** Ship one tested reference wrapper, the fromargs subcommand `harness-wrapper gemini`, behind the command adapter. Document command-adapter recipes for Copilot CLI, Cursor CLI, OpenCode, and Pi.
- **Alternatives:** First-class adapters for Gemini, Copilot, and Cursor (large; each needs live verification). Docs-only recipes (untested).
- **Consequences:** On Linux, Gemini CLI's sandbox needs a container runtime. The wrapper's `discover` answer may rest on the documented workspace skill directory if Gemini CLI has no model-free skill inventory. First-class adapters stay possible follow-ups.

_Source: Mold session 2026-10-11 · Updated: 2026-11-02 (recovered from an uncommitted worktree)_
