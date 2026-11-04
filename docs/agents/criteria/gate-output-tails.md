# Gate output tails and gate faults

Source: gh-588 item 1 (https://github.com/paulnsorensen/skillz-that-grillz/issues/588); spec autoimprove-seed-signals C2

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN a command gate scores below 1, THE SYSTEM SHALL record a redacted stdout tail and a redacted stderr tail in `gate_diagnostic`, AND a clean zero verdict SHALL NOT read "exits with code 0" |  |
| AC-2 | test | WHEN `export` writes the report, THE SYSTEM SHALL include each outcome's `gate_diagnostic` and `gate_fault` |  |
| AC-3 | test | WHEN a command gate reaches the sandbox time limit, THE SYSTEM SHALL record `gate_fault: timeout` on the outcome; WHEN it exits 0 with stdout that is not a valid score, THE SYSTEM SHALL record `gate_fault: invalid-output`; any other gate result SHALL record no `gate_fault` |  |
