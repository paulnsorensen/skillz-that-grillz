# Architecture gate decisions

These records explain the slice guard (`.github/scripts/check_slices.py`) and the depth ratchet (`.github/scripts/check_depth.py`).
Both tools read `slices.toml` through `.github/scripts/slice_map.py`.
They are Python ports of two prototypes in paulnsorensen/sliced-bread-architecture: sliced-bread-guard (PR 69) and sliced-bread-depth-ratchet (PR 71).

## Records

### ADR-001: Port the guard and the ratchet to a Python `ast` backend  [status: accepted]

- **Context:** Both upstream prototypes support TypeScript only. The guard uses dependency-cruiser, and the ratchet uses the TypeScript compiler API. This repository has only Python source.
- **Decision:** Keep the upstream rule IDs, metric keys, file formats, and commands. Replace the backend with the standard library `ast` and `tokenize` modules. The tools are scripts in `.github/scripts/`, like `check_architecture.py`, and `just build` runs them as the lanes `slice-guard`, `slice-guard-tests`, `depth-ratchet`, and `depth-ratchet-tests`.
- **Consequences:** No Node toolchain enters the gate. The guard has no `detect`, `generate`, `wire`, or `prove` command: the gate wiring is in `run_gate.py`, and the test suites plant one violation per rule, which replaces `prove`.

### ADR-002: The slice map covers the `src/` trees of every workspace package  [status: accepted]

- **Context:** `[policy] roots` decides the SB-04 inventory. AGENTS.md lets a test import slice internals.
- **Decision:** The roots are the four `packages/*/src` trees. Each workspace package is a slice with its `__init__.py` as the crust. The six `skillz_experiments` slices are slices. The `skillz_experiments` root modules are two spine nodes: `skillz-core` (the `KERNEL` modules of `test_layout.py` plus the root modules that they import) and `skillz-app` (the CLI modules that import the slices). Tests are outside the roots, so SB-05 has no Python counterpart.
- **Consequences:** A new root module fails SB-04 until a human maps it in `slices.toml`. The guard overlaps `test_layout.py` and `check_architecture.py`; those checks stay, and the overlap is intentional. The adoption baseline `sbguard-baseline.json` is empty, because the tree had no violation.

### ADR-003: The depth ratchet runs in `block` mode  [status: accepted]

- **Context:** sliced-bread-architecture ADR-003 makes numeric thresholds advisory. The upstream ratchet defaults to `report` and lets a repository opt in to `block` in its baseline.
- **Decision:** The owner opted in: `depth-ratchet.json` sets `"mode": "block"`. A block compares a slice only with its own baseline. The 200-statement and 50% size-aware rule stays advisory.
- **Consequences:** A change that makes a slice shallower fails `just build`. A human raises a cap only for a deliberate trade-off and says why in the PR. `check_depth.py diff-baseline --base <ref>` lists each loosening for the reviewer.

### ADR-004: Python metric definitions differ from the TypeScript definitions  [status: accepted]

- **Context:** The upstream metrics depend on TypeScript syntax and on a dead-code reachability graph.
- **Decision:** A logical statement is an `ast.stmt` inside a function body, without nested `def` or `class` lines and docstrings; a lambda counts 1. Complexity adds `if`, ternary, loops, `except`, non-wildcard `case` and its guard, each extra boolean operand, each comprehension `for` and `if`, and each parameter default. M09 counts the names of a literal `__all__`, else the public top-level names. M12 counts imports and attribute uses through the crust module from files in other nodes. Dead internal code stays in the denominators, because the Vulture lane removes dead code.
- **Consequences:** Measurement version 1 holds these definitions. A definition change increments `MEASUREMENT_VERSION`, and `check` then refuses the old baseline until a human re-baselines.

### ADR-005: SB-07 reads the baseline at the base commit  [status: accepted]

- **Context:** SB-07 fails when the guard baseline grows or is deleted. It needs the base commit.
- **Decision:** `check --base auto` uses `$GATE_BASE`, else `HEAD^1` in GitHub Actions, else the merge base with `origin/main`. The CI gate job checks out with `fetch-depth: 2`; in a pull request run, `HEAD^1` of the merge commit is the base branch tip.
- **Consequences:** A clone without `origin/main` must set `GATE_BASE`. The guard is an input error (exit 2) when it cannot find the base. A base without `slices.toml` marks the adoption change, which may add the baseline.
