# wedge-skill-packaging: wedge skill packaging

Source: reconstructed from the module docstrings of packages/wedge/tests/wedge/test_*.py (the spec is absent from the repository; ADR in docs/agents/decisions/wedge-skill-packaging.md)

| id | kind | criterion | verified by |
| --- | --- | --- | --- |
| AC-W1 | test | The content key is stable across checkout paths and reacts only to build-input bytes (test_key.py). |  |
| AC-W2 | test | build() is reproducible across independent checkouts, and build_many exports each skill's groups and splits sites on them (test_build.py, test_groups.py). |  |
| AC-W3 | test | The built .pyz runs standalone under a clean interpreter (test_pyz_runtime.py). |  |
| AC-W4 | test | The committed launcher downloads, verifies, caches, and execs (test_launcher.py). |  |
| AC-W5 | test | wedge check verifies a skill's lock and launcher without building (test_check.py, test_fanout.py). |  |
| AC-W6 | test | publish is idempotent and race-safe against a fake gh CLI (test_publish.py, test_build.py). |  |
| AC-W7 | test | The closure guard rejects a platform marker or a non-pure wheel, and the composite action preserves wedge output and failures (test_guard.py, test_action.py). |  |
| AC-W8 | test | wedge works in a consumer repository outside this uv workspace (test_consumer.py). |  |
| AC-W9 | test | One wedge command builds, locks, or checks every skill under a root; the CLI's directory resolution rejects a contradictory or bad command; a bad wedge.toml layout is a config error (test_fanout.py, test_cli.py, test_key.py). |  |
| AC-W10 | test | A wedge.toml in a discovery root supplies defaults for every skill beside it (test_defaults.py). |  |
