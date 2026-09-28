# Raw-log Usage fallback

Use only when the DuckDB CLI is unavailable during `audit` or `self-update`.
This path is slower and sampled. It does not reproduce the analytics packs.

1. Use the shared protocol's **Read and classify** result to set target kind
   (`skill` or `agent`) and exact target name. Ask: "DuckDB is unavailable.
   Skip Usage, or run a slower sampled raw-log scan for this <kind> with up to
   three low-cost read-only subagents?" Do not dispatch until the user chooses
   the scan. If the user skips or the host has no subagents, omit Usage and
   continue the audit.
2. Read `engine/references/harness-coverage.md` for the supported local roots.
   Select at most three accessible harness roots. Prefer Claude for explicit
   `Skill` or `Agent` events and Cursor for `Task` agent events. Choose other
   roots only when they have recent logs. Do not read log bodies in the parent.
3. Dispatch one low-cost read-only subagent per selected harness. Pass its
   absolute root, exact target name, target kind, and this protocol. Forbid
   writes, network access, database creation, and raw-log quotations in replies.
   Each subagent selects at most ten most-recent JSONL files by modification
   time. It scans backward from each file's end with bounded reads. Stop after
   1 MiB or 2,000 lines across all files, whichever comes first. Ignore entries
   older than 30 days when a timestamp is parseable. Do not enter other roots.
4. Count only explicit events for the classified target kind. For a skill,
   match Claude `Skill` tool-use blocks with `input.skill` equal to the target.
   Another harness needs a native event that explicitly names an invoked skill.
   For an agent, match Claude `Agent` or Cursor `Task` tool-use blocks with
   `input.subagent_type` equal to the target. Another harness needs an explicit
   native agent-spawn event that names the target. If a harness lacks the event
   or field for that kind, report `unavailable`, not zero. Do not count prose
   mentions, file paths, or inferred invocations. Do not infer tool causation,
   error rates, or cross-harness totals from this sample.
5. Each subagent returns at most 1 KB with this digest, not raw log text:

   ```text
   harness: <name>
   files_scanned: <0-10>
   lines_scanned: <0-2000>
   time_window: <earliest/latest parseable date or unknown>
   explicit_target_invocations: <count or unavailable>
   omitted_metrics: <short list and reason>
   limits: sampled files; missing or incomplete events
   ```

6. Carry only these digests into Usage. Label every result **sampled raw-log
   evidence**, not a database estimate. State that counts are lower bounds,
   cross-harness comparisons are weaker, and metrics may be missing. If all
   workers find no explicit signal, mark Usage insufficient and continue.