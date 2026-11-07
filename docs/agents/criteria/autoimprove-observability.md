# autoimprove-observability: trace a run, and let the judge see the task's evidence

Source: reconstructed from the autoimprove observability task (Cook autoimprove runs of 2026-11, run outputs under /var/tmp/cook-ai-out.*)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-1 | test | WHEN an OTLP traces endpoint is set, `skillz-experiment run` SHALL export one run span, a span for each phase that it runs, a span for each case evaluation, and a model call span for each task, judge, and reflection call with its role, harness, model, case id, arm, latency, token usage, score, and verdict, AND the search phase span SHALL carry the stop trigger |  |
| AC-2 | test | THE exported span attributes SHALL hold no prompt, transcript, judge reason, or header value |  |
| AC-3 | test | WHEN no OTLP traces endpoint is set, OR `OTEL_SDK_DISABLED` is `true`, OR `OTEL_TRACES_EXPORTER` is `none`, THE SYSTEM SHALL export nothing and SHALL leave the provider unwrapped |  |
| AC-4 | test | WHEN the OTLP protocol is `grpc`, OR the endpoint is not an http or https URL, OR an export fails, THE SYSTEM SHALL print one warning that names no header value and SHALL continue the run |  |
| AC-5 | test | WHEN a journey task runs tools, THE judge SHALL receive a `trace` of the task's messages and tool calls with their output, by turn, AND a criterion that needs a command run SHALL pass only when that trace shows the run |  |
| AC-6 | test | THE judge trace SHALL be redacted and bounded per field and in total, AND the simulator SHALL NOT receive it |  |
| AC-7 | test | WHEN the code that shapes the judge trace changes, THE scoring digest SHALL change, AND a run that an earlier engine froze SHALL stop on resume with `evaluator differs from the frozen record; create a new run` before any model call |  |
| AC-8 | process | WHEN an outcome row has no transcript, THE reference SHALL say that it is a reflection row, so a reader does not count it as an empty task answer | PR review: the 4-character transcripts of the Cook runs are reflection rows (`split: reflection`, `transcript: null`) |
| AC-9 | process | THE criteria-drafting reference SHALL state that the Codex task role cannot start a sub-agent and that the Claude task role needs a declared `Agent` surface for one, so a case author does not write a criterion that needs an unavailable sub-agent | PR review: `features.multi_agent=false` in `harness/_codex.py`, `TOOLS = "Bash,Read,Skill"` in `harness/_claude/_transport.py` |
