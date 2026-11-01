# gh-596 CLI startup benchmark

Baseline: `05bc6c0c485f13d6d016a97620a863c2f87216e5`.
Candidate: the uncommitted issue #596 worktree after rebuilding the CLI archives.
Both revisions use their own `skills/skillz/scripts/*.pyz` archives.

The real inspect command uses `skills/skillz/SKILL.md`.
The analytics command uses `--help`.
The extracted mode runs `python3 -I archive.pyz args` with separate warm `SHIV_ROOT` directories.
The source-only mode inserts `archive.pyz/site-packages` on `sys.path` and calls the archive's actual entry function.
It does not extract the archive or load dependencies from an external `PYTHONPATH`.
This source-only mode is a benchmark runner, not a replacement launcher.

`hyperfine --shell=default --warmup 2 --runs 12` measured wall time for each command.
`python3 -I -X importtime` measured five runs per command.
The table reports the median sum of import self times and median import entry counts.
Importtime adds profiling overhead, so its times are not wall times.

| Command | Mode | Wall before | Wall after | Change | Import self before | Import self after | Import entries before/after |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| inspect-skill | extracted | 113.2 ± 12.8 ms | 96.6 ± 14.1 ms | -14.7% | 81.4 ms | 75.8 ms | 268 / 216 |
| inspect-skill | source-only ZIP | 257.2 ± 30.7 ms | 241.6 ± 31.2 ms | -6.1% | 221.1 ms | 198.6 ms | 248 / 175 |
| agent-session-analytics --help | extracted | 160.1 ± 11.8 ms | 152.6 ± 22.0 ms | -4.7% | 127.1 ms | 106.8 ms | 444 / 397 |
| agent-session-analytics --help | source-only ZIP | 602.6 ± 37.1 ms | 557.2 ± 15.6 ms | -7.5% | 540.9 ms | 519.4 ms | 427 / 380 |

Wall measurements vary, especially the extracted analytics candidate.
The repeated importtime samples show fewer import entries in every measured mode.

Parity checks compared baseline and candidate exit status, standard output, and standard error byte for byte.
They passed in both modes for the real inspect command, inspect with no arguments, analytics help, and wedge help.
The no-argument inspect command returned exit status 2 in both revisions.
