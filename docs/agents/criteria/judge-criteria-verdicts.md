# judge-criteria-verdicts: the journey gate fails a case approval ask without the hash

Source: judge-criteria-verdicts (Mold spec in the durable cheese corpus)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-15 | test | WHEN a tool result in a skillz journey trace holds the runner `cases-unapproved` stop, and the assistant text after the last such stop of that turn lacks its 64-character `case_hash` as a hex run not inside a longer hex run of either case, or is absent, THE skillz journey gate SHALL print a score of 0 and name the failed rule |  |
