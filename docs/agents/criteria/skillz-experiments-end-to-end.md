# Skillz experiments end-to-end prototype criteria

Source: spec skillz-experiments-end-to-end (docs/research/skillz-experiments-e2e-plan.md; prototype scope authorized 2026-10-12)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN the selected prototype matrix runs THE SYSTEM SHALL cover baseline and winner for P0, H0, W0, N0, J0, J1, and M0. W1 remains an explicit gap. | |
| AC-2 | test | WHEN a CLI scenario runs THE SYSTEM SHALL retain real adapter, evaluator, GEPA, gate, and export calls. Reflection SHALL receive current feedback and advisory run progress. | |
| AC-3 | test | WHEN a task receives a changed candidate THE SYSTEM SHALL grade actual output or executed helper behavior. | |
| AC-4 | test | WHEN a selected verdict scenario completes THE SYSTEM SHALL assert its exact verdict, reasons, delta, SE, and coverage. | |
| AC-5 | test | WHEN a positive patch is applied THE SYSTEM SHALL reproduce evaluated behavior without changing the original target. | |
| AC-6 | test | WHEN W0 or N0 completes THE SYSTEM SHALL build and execute patched source, preserve frozen paths, and match the evaluated archive content. | |
| AC-7 | test | WHEN a journey completes THE SYSTEM SHALL preserve role separation and score the real command gate and transcript. | |
| AC-8 | test | WHEN approval or process interruption occurs THE SYSTEM SHALL refuse unapproved inference and resume durable work without duplicate completed observations. | |
| AC-10 | test | WHEN the installed bundle runs outside the checkout THE SYSTEM SHALL match the source scenario's declared result. | |
| AC-11 | process | WHEN implementation ends THE SYSTEM SHALL record ownership, assumptions, verification, and remaining repair dependencies. | `docs/research/skillz-experiments-e2e-prototype.md` |
