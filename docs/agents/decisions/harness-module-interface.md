# Harness module interface decisions

These records explain the planned shape of the experiment harness code in `packages/skillz/src/skillz_experiments/`. The approved spec is `specs/harness-module-realign.md` in the durable cheese corpus. It refactors PR 217 (sandboxed experiment runs across harnesses).

## Records

### ADR-001: Land the realign, the interface, and the witness as three stacked PRs  [status: accepted]

- **Context:** PR 217 was built on base 432afa5. Main then moved `skillz_experiments` to `packages/skillz` (#213) and changed the bundle flow (#215). The PR already had two review rounds.
- **Decision:** Rebase PR 217 onto main and force-push it (layer 1). Put the interface refactor in a second PR on the PR 217 branch (layer 2). Put the Gemini trace witness in a third PR on the layer 2 branch (layer 3).
- **Consequences:** Each diff is reviewable by itself. Layer 2 does not change behavior. Layer 3 is the only contract change. The force-push makes the old PR 217 review comments outdated.

### ADR-002: The harness slice owns the adapter contract  [status: accepted]

- **Context:** The `Transport` protocol and the credential kinds lived in the grading module `_evaluator`. `_doctor` read `ClaudeCode` internals. About nine `adapter == "claude" | "codex"` and `isinstance` branches sat outside the adapters.
- **Decision:** Create `skillz_experiments/harness/` with an `__init__.py` barrel as the only import surface. Move `Transport` into the slice. The four credential kinds go to the leaf module `harness/_check.py`, beside `credential_row`, so the adapters and `_transport` import them without a cycle. Tests may import slice internals for monkeypatch handles and for types the barrel omits. The barrel exports no credential constants, `Check`, or `HarnessName`. Claude stop codes sit behind `stop_kind`, which the barrel exports. Each adapter (`ClaudeCode`, `Codex`, `Command`) owns its host checks, credential kind, and capabilities. Outside code has no adapter-name or `isinstance` branches. `_evaluator` declares its own narrow protocol for the members `evaluate` uses and imports nothing from `harness`, which keeps the dependency one-way (`harness` → `_evaluator`). The Gemini reference wrapper goes to its own `skillz_experiments/wrapper/` slice. The shared Seatbelt `command_profile` is sandbox policy, so it lives in `harness/_seatbelt.py`, and the wrapper imports it through the harness barrel. `harness` never imports `wrapper`. `_protocol` stays shared between the two slices.
- **Alternatives rejected:** A shared `_transport` kernel outside the slice (it contradicts the user's choice). An adapter registry (three static adapters do not need one). A Seatbelt kernel outside both slices (the profile is adapter sandbox policy, and the barrel already gives the wrapper a one-way path).

### ADR-003: A runner-side witness gates helper credit for command harnesses  [status: accepted]

- **Context:** A command harness reports its own trace. The Gemini wrapper accepts any printed `{` line as a tool event, so a model can earn helper credit without running the helper.
- **Decision:** For command harnesses, the runner stages a stub at each helper path. The stub writes a ledger line with a per-call nonce, the argv, and the exit code, then runs the real helper with `runpy`. Helper credit needs a matching ledger entry. Otherwise the run is `activation-rejected`. The run record gains `helper_witnessed`. `claude` and `codex` keep their CLI-stream traces with no stub.
- **Limits:** The stub runs inside the sandbox, so a model that deliberately forges ledger lines is not stopped. The witness stops accidental and lazy false credit. If the sandbox blocks the ledger write, the gate fails closed.

### ADR-004: The wrapper slice registers its own commands; the CLI names no vendor  [status: accepted]

- **Context:** `_cli.py` imported the Gemini wrapper by name and declared `harness-wrapper gemini` with the Gemini image in its signature. The wrapper is a reference program for the generic `command` harness, not an adapter. The core never depends on it. A sliced-bread review (issue 266) flagged the vendor name in the composition root.
- **Decision:** Choose option 2. The `wrapper` barrel exports `register(app)`. It adds the `harness-wrapper` group and one command for each reference wrapper. `_cli.py` calls `register` and contains no `gemini` name. A layout test fails when `_cli.py` imports anything from the wrapper slice other than the barrel and `register`, or names a vendor.
- **Alternatives rejected:** A second entry point or `.pyz` (option 1). It adds a tracked 5 MB bundle, a wedge target, and a second command to ship. It also ends the one-bundle-is-its-own-wrapper recipe (`harness-recipes.md`). No change (option 3). It leaves the vendor name in the composition root.
- **Consequences:** The command line is unchanged: `skillz-experiment harness-wrapper gemini`. The CLI still lists the group. A user who wants no wrapper command needs option 1 later; the `register` seam makes that move small.
