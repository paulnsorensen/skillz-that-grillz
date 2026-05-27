# Deciding the version

[Semantic Versioning 2.0.0](https://semver.org) — `MAJOR.MINOR.PATCH`:

- **MAJOR** — incompatible API changes.
- **MINOR** — backwards-compatible functionality.
- **PATCH** — backwards-compatible bug fixes.

The job of this step is to read what changed since the last tag and pick the
smallest bump that honestly reflects it.

## Conventional Commits → bump

Scan the subject lines (and bodies, for footers) of every non-merge commit
since the last tag:

```bash
git log "${LAST_TAG:+$LAST_TAG..}HEAD" --no-merges --pretty='%h %s'
git log "${LAST_TAG:+$LAST_TAG..}HEAD" --no-merges --grep='BREAKING CHANGE' --pretty='%h %s'
```

Map the **highest-impact** change to the bump — one breaking change in a sea
of fixes still makes it a major release.

| Commit shape | Signals |
|---|---|
| `feat!:` or `fix!:` (bang) | breaking |
| `BREAKING CHANGE:` footer in the body | breaking |
| `feat:` | new feature |
| `fix:` | bug fix |
| `perf:` | performance fix (patch-level) |
| `refactor:` / `docs:` / `test:` / `chore:` / `ci:` / `build:` / `style:` | no user-visible change |

### At or above 1.0.0

| Highest-impact change | Bump |
|---|---|
| any breaking | **MAJOR** — `2.4.1 → 3.0.0` |
| `feat` (no breaking) | **MINOR** — `2.4.1 → 2.5.0` |
| `fix` / `perf` only | **PATCH** — `2.4.1 → 2.4.2` |
| only no-user-visible types | PATCH, or skip the release — ask the user |

### Below 1.0.0 (the `0.x` exception)

Pre-1.0 the public API is declared unstable, so the bump shifts down one level:

| Highest-impact change | Bump |
|---|---|
| any breaking | **MINOR** — `0.4.2 → 0.5.0` |
| `feat` | **PATCH** (or minor, by project convention) — `0.4.2 → 0.4.3` |
| `fix` / `perf` | **PATCH** — `0.4.2 → 0.4.3` |

Many `0.x` projects treat `0.MINOR` as the breaking axis and `0.x.PATCH` as
"everything else". Match the repo's established habit if it has one — look at
how past `0.x` tags moved.

## Match the existing tag scheme

Detect and reuse the repo's conventions rather than imposing yours:

```bash
git tag --sort=-v:refname | head -5     # recent tags reveal the scheme
```

- **`v` prefix** — if tags look like `v1.2.3`, keep the `v`. If they're bare
  `1.2.3`, stay bare. Don't switch mid-stream.
- **Prerelease identifiers** — `1.3.0-rc.1`, `1.3.0-beta.2`, `1.3.0-alpha`.
  Prereleases sort *below* the final release (`1.3.0-rc.1 < 1.3.0`). Increment
  the identifier (`-rc.1 → -rc.2`) for successive prereleases, drop it for the
  final.
- **Build metadata** — `+build.5` is ignored for precedence; rarely needed for
  a GitHub release tag.

## First release

No tags yet? The conventional first public version is `v1.0.0` for something
declaring a stable API, or `v0.1.0` for something still finding its shape. Ask
which the project intends — it sets the whole versioning contract going
forward. The notes range is the full history (`git log` with no `LAST_TAG..`).

## State the reasoning

Whatever you compute, show the user the one-line justification before tagging:

> 5 commits since `v1.2.3`: 1 `feat`, 3 `fix`, 1 `docs`. No breaking changes →
> **MINOR** → **`v1.3.0`**.

The user can override (e.g. they know an unmarked commit was actually
breaking). Never tag a number they haven't seen and approved.
