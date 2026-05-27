# GitHub Community Standards reference

GitHub computes a "community profile" for every public repo and
exposes it under **Insights → Community Standards**. The checklist
counts the presence of a small set of files. Source of truth:

<https://docs.github.com/communities/setting-up-your-project-for-healthy-contributions>.

The community profile is also queryable via the API:

```sh
gh api "repos/<owner>/<repo>/community/profile" --jq '{
  health_percentage,
  files: (.files | to_entries | map({key, present: (.value != null)}))
}'
```

## The checklist

| File | Detected at | Notes |
|---|---|---|
| Description | Repo `description` field | Not a file; set via `gh repo edit --description` |
| `README` | `README.md`, `README.rst`, `README.txt`, `docs/README.md`, `.github/README.md` | First file rendered on the repo home |
| `LICENSE` | `LICENSE`, `LICENSE.md`, `COPYING` | GitHub matches against [SPDX][spdx]; unknown licences show as "Other" |
| `Code of conduct` | `CODE_OF_CONDUCT.md`, `.github/CODE_OF_CONDUCT.md` | Surfaces in the "Add file" dropdown |
| `Contributing` | `CONTRIBUTING.md`, `.github/CONTRIBUTING.md` | Linked from the "New issue" / "New PR" pages |
| `Security policy` | `SECURITY.md`, `.github/SECURITY.md` | Linked from the Security tab |
| `Issue templates` | `.github/ISSUE_TEMPLATE/*.{md,yml}` | At least one for the checklist to pass |
| `Pull request template` | `.github/PULL_REQUEST_TEMPLATE.md`, `.github/PULL_REQUEST_TEMPLATE/*.md` | Single file or directory of variants |

[spdx]: https://spdx.org/licenses/

## Where files can live

GitHub looks in three places for community files, in priority order:

1. The repo root.
2. The repo's `.github/` directory.
3. The default community-health repo for the user/org — a special
   repo named `.github` at `https://github.com/<owner>/.github`.

A file in the default community-health repo applies to **every** repo
owned by that user/org that does not have its own copy. This is the
preferred place for an org with many similar repos: write
`CODE_OF_CONDUCT.md` once in `<org>/.github`, and every public repo
inherits it.

## Issue templates: legacy vs. forms

GitHub supports two issue-template formats:

- **Legacy** (`.md`): a markdown file with a YAML frontmatter
  `name`/`about`/`labels`. Easy to author, easy to ignore the prompt.
- **Forms** (`.yml`): structured fields with validation
  (textarea, input, dropdown, checkboxes). Better data hygiene.

The skill scaffolds **forms**. They produce more consistent reports
and validate `required` fields up front. The legacy format is fine
to keep if it's already in place — they coexist.

## `config.yml` for issues

`.github/ISSUE_TEMPLATE/config.yml` controls two things:

- `blank_issues_enabled`: when `false`, users must pick a template
  (recommended).
- `contact_links`: alternative places to file feedback (Discussions,
  forum, security advisory). These appear above the template list.

## Common gotchas

- **Files in subdirectories don't count**. `docs/CONTRIBUTING.md`
  doesn't satisfy the checklist; `CONTRIBUTING.md` or
  `.github/CONTRIBUTING.md` do.
- **Per-repo files take priority over the `.github` community-health
  defaults**. The `.github` repo is a fallback — it only applies when the
  individual repo has no copy of its own. If you updated the `.github` repo
  but the per-repo copy isn't changing, that's why.
- **`PULL_REQUEST_TEMPLATE.md` is auto-applied**; multiple templates
  require a directory and `?template=name.md` URL parameter to pick
  one. Most projects use a single template.
- **Templates can include front-matter**, but the body of an issue
  template form is literal — Markdown formatting renders.
- **Code of Conduct**: GitHub recognises the
  [Contributor Covenant][cc] as a "well-known" CoC and surfaces a
  badge. Custom CoCs are accepted but don't get the badge.

[cc]: https://www.contributor-covenant.org/
