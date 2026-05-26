# Triage table expansion examples

For each row in the triage table, include a one-line expansion so the
user can decide on ASK items without re-opening the PR. Below are
representative shapes — adapt the prose, not the structure.

## FIX expansion

```text
### 1. Missing null check on token (92) — FIX
> copilot: `token` could be undefined when the auth header is malformed
Plan: add a null guard before decode and return 401 on missing token.
```

## FIX (review body) expansion

```text
### 3. Missing error handling in 3 endpoints (78) — FIX [review body]
> alice (CHANGES_REQUESTED): "The new endpoints in handler.rs don't
> propagate database errors — they silently return empty results."
Plan: add error propagation in the 3 endpoints alice flagged.
```

## ASK expansion

```text
### 4. Extract to shared helper (60) — ASK
> copilot: `formatDate()` is duplicated in 3 files
Real duplication, but extracting introduces a shared module and a
new import surface. Worth it now, or leave for a dedicated cleanup
pass?
```

## PUSH BACK expansion

```text
### 5. Add backward compat shim (35) — PUSH BACK
> bob: users on v1 will break without a migration path
Draft reply: "This project is pre-release with zero users and no
production data — backward compatibility isn't a concern yet per our
Early Development Stance. We'll add migration support when there is
something to migrate from."
```

## SKIP entry

```text
### 6. "Consider adding tests" (20) — SKIP
> copilot: general suggestion, no specific test target
Action: noted in the triage table; no reply posted.
```

## Posting these via the helper

When phase 4 posts a reply for a FIX or PUSH BACK item, use
`scripts/post-reply.sh` so the attribution suffix is applied
consistently. The body string passed to `--body` should be the **reply
prose only** — the helper adds the `---` and the `agent on behalf of;`
line. Example:

```bash
./scripts/post-reply.sh --thread \
  --pr 42 \
  --comment-id 1234567890 \
  --body "Fixed — added a null guard before decode and a 401 on missing token."
```

Renders to GitHub as:

```text
Fixed — added a null guard before decode and a 401 on missing token.

---
agent on behalf of; paulnsorensen
```
