# Monorepo composition: modules, imports, and the aggregated gate

A monorepo still has **one** canonical command. `just build` at the root must
mean "verify everything" — it composes each sub-module's gate rather than
inventing a parallel one. This file shows how to keep the single-gate posture
(see `agent-build.md`) when the repo is split across many justfiles.

Two mechanisms, different jobs:

| Mechanism | What it does | Reach for it when |
|---|---|---|
| `mod` | Loads a sub-justfile under a namespace (`sub::recipe`) | Each component owns its own gate and you want isolated, addressable recipes |
| `import` | Splices another justfile's recipes into *this* one's namespace | Several justfiles must share the *same* `_gate`/helper with no namespacing |

Chapters: [Modules](https://just.systems/man/en/modules.html) ·
[Imports](https://just.systems/man/en/imports.html).

## Modules — `mod`

`mod name` searches for the module file in `name.just`, `name/mod.just`,
`name/justfile`, and `name/.justfile`, loaded under the `name::` namespace. `mod`
statements were stabilized in just 1.31.0 — no `set unstable` needed on ≥1.31.

```just
mod api          # loads api/justfile     -> just api::build
mod web          # loads web/justfile     -> just web::build
mod? infra       # optional: no error if infra/ is absent
```

- `just api::build` runs the `build` recipe inside `api/justfile`.
- `just --list api` lists a module's recipes; `just --list` shows modules nested.
- `mod? name` is the optional form — the justfile still loads if the module's
  file is missing (good for an `infra/` or `local/` dir that not every checkout
  has).
- Module paths are relative to the **justfile**, not the working directory.

Each module is itself a full justfile, so each owns its own canonical gate —
`api/justfile` defines `api`'s `build`/`ci`/`_gate`, `web/justfile` defines
`web`'s. The root justfile doesn't duplicate that logic; it aggregates it.

## The aggregated root gate

The root `build` composes each module's gate as a dependency. `just build` at
the root still means "verify everything"; `just api::build` verifies one slice.

```just
mod api
mod web
mod? infra

# The one command to run after every change — verifies every module.
default: build

# Root gate — runs each module's gate. Compact output is per-module.
build: api::build web::build

# CI gate — each module's no-autofix twin. A clean run here == a clean CI.
ci: api::ci web::ci
```

Dependencies run first and fail-fast on the first error (see
[Dependencies](https://just.systems/man/en/dependencies.html)), so a red
`api::build` aborts the root `build` before `web` runs — exactly the
deterministic pass/fail the gate design depends on.

Notes:

- Keep the root gate's body empty — its *only* job is to depend on the module
  gates. Don't re-run lint/test at the root; that would double-run and split the
  definition.
- Module gates that are independent can run concurrently — see
  [Parallelism](https://just.systems/man/en/parallelism.html). Default is
  sequential, which keeps the `✓`/`✗` ledger readable; opt into parallel only
  when suites are slow and independent.
- Optional modules (`mod? infra`) can't be a hard dependency — if `infra` may be
  absent, gate it behind a guard recipe rather than listing `infra::build`
  directly, or keep it out of the aggregate and run it explicitly.

## Imports — `import`

`import 'path.just'` splices another justfile's recipes and assignments into the
current namespace — no `sub::` prefix, no `set unstable`. Use it to share one
`_gate` (or common variables) across sibling justfiles without the module
boundary.

```just
import 'shared.just'      # error if missing
import? 'local.just'      # optional — silently skipped if absent

build: (_gate "fix")      # _gate is defined in shared.just
ci: (_gate "check")
```

- Imported recipes live in *this* justfile's namespace, so `build` can depend on
  an imported `_gate` directly — no namespace prefix.
- A recipe defined in the importing file overrides one of the same name in the
  imported file (later definition wins).
- `import?` is the optional form.

### `mod` vs `import` — which one

| You want… | Use |
|---|---|
| Each component to own and run its own gate, addressable as `sub::build` | `mod` |
| One shared `_gate`/helper reused verbatim across several justfiles | `import` |
| To split a 200-line justfile by concern but keep one flat namespace | `import` |
| True monorepo where `just build` aggregates independent module gates | `mod` + aggregated root gate (above) |

Rule of thumb: **`mod` namespaces, `import` flattens.** Reach for `import` when
the goal is "share the gate"; reach for `mod` when the goal is "compose
independently-verifiable components into one root command."
