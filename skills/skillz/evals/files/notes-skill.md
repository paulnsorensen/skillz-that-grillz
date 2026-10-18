---
name: notes
description: Summarize a folder of Markdown notes.
---
# Notes

1. Read every `*.md` file in `notes/`.
2. For every note, count how often each `#tag` appears, then merge the counts and sort them by count.
3. Report the ten most frequent tags to the user.

## Offload candidates

1. Count the `#tag` uses in every note and merge the counts (step 2). The result is deterministic and the same on every run.
