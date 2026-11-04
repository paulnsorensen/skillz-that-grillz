# autoimprove-seed-signals: a command gate keeps its output tails and records a gate fault

Source: spec autoimprove-seed-signals (C2), gh-588 item 1 (https://github.com/paulnsorensen/skillz-that-grillz/issues/588)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-7 | test | WHEN a command gate scores below 1, THE SYSTEM SHALL record a redacted stdout tail and a redacted stderr tail in `gate_diagnostic`, AND a clean zero verdict SHALL NOT read "exits with code 0" |  |
| AC-8 | test | WHEN `export` writes the report, THE SYSTEM SHALL include each outcome's `gate_diagnostic` and `gate_fault` |  |
| AC-20 | test | WHEN a command gate reaches the sandbox time limit, THE SYSTEM SHALL record `gate_fault: timeout` on the outcome; WHEN it exits 0 with stdout that is not a valid score, THE SYSTEM SHALL record `gate_fault: invalid-output`; any other gate result, including an exit code of 124 from the gate itself, SHALL record no `gate_fault` |  |
