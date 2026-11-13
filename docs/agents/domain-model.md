# Domain model

## Case proof

**Case proof** is an optional pair of pass and fail output references on an authored case.
The validator checks the selected gate with actual case inputs.
It proves neither model solvability nor a judge verdict.

**Diagnostic proof** is proof metadata and files checked without changing case approval or frozen run data.
Validation checks malformed metadata on every loaded case.
Normal run loading ignores it while retaining whole-input bounds.
Only selected cases receive proof-file and command checks.

**Proof origin** is the internal author file and original JSON Pointer for the authored proof.
It adds no seed JSON field, prompt data, frozen content, or case identity.

**Bounded local probe** is a trusted local grader process with a clean environment, limits, and process-group cleanup.
The caller's PATH selects local tool versions.
These controls are not OS isolation; host-file and network access remain possible.
