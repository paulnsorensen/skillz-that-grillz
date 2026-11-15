"""Run every JavaScript example of hooks-catalog.md against its documented contract.

Each example starts with a marker line `// .claude/hooks/<name>.js`. The test extracts
the block by that marker, runs it with `node` and a JSON payload on stdin, and checks
stdout, the exit code, and written files. Every run happens under `tmp_path`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

CATALOG = Path(__file__).resolve().parents[4] / "skills/skillz/references/hooks-catalog.md"
BLOCK = re.compile(r"^```javascript\n(.*?)^```", re.DOTALL | re.MULTILINE)
MARKER = re.compile(r"^// (\.claude/hooks/[A-Za-z0-9._-]+\.js)$")
COUNTER = "/tmp/claude-prompt-counter.json"

COVERED: set[str] = set()


def covers(marker: str) -> Callable[[Callable[..., None]], Callable[..., None]]:
    def register(test: Callable[..., None]) -> Callable[..., None]:
        COVERED.add(marker)
        return test

    return register


def catalog_blocks() -> dict[str, str]:
    text = CATALOG.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for match in BLOCK.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        source = match.group(1)
        marker = MARKER.match(source.split("\n", 1)[0])
        assert marker, f"hooks-catalog.md:{line}: javascript block has no `// .claude/hooks/<name>.js` marker"
        assert marker.group(1) not in found, f"hooks-catalog.md:{line}: duplicate marker {marker.group(1)}"
        found[marker.group(1)] = source
    return found


def run_hook(
    marker: str, payload: object, tmp_path: Path, *, setup: Callable[[Path], None] | None = None
) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH; the hooks-catalog examples need it and this test never skips")
    work, tmp = tmp_path / "work", tmp_path / "tmp"
    hooks = tmp_path / "hooks"
    for folder in (work, tmp, hooks):
        folder.mkdir(exist_ok=True)
    source = catalog_blocks()[marker].replace(COUNTER, str(tmp_path / "counter.json"))
    script = hooks / Path(marker).name
    _ = script.write_text(source, encoding="utf-8")
    if setup:
        setup(work)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    env = {**os.environ, "TMPDIR": str(tmp)}
    return subprocess.run(
        [node, str(script)], input=stdin, capture_output=True, text=True, cwd=work, env=env, timeout=30, check=False
    )


def expect(
    proc: subprocess.CompletedProcess[str],
    marker: str,
    payload: object,
    *,
    code: int = 0,
    out: str | None = None,
    err: str | None = None,
) -> None:
    where = f"{marker} with payload {json.dumps(payload)}"
    assert proc.returncode == code, f"{where}: exit {proc.returncode}, want {code}\nstderr: {proc.stderr}"
    if out is not None:
        assert out in proc.stdout, f"{where}: stdout {proc.stdout!r} lacks {out!r}"
        assert bool(out) or not proc.stdout, f"{where}: stdout {proc.stdout!r} is not empty"
    if err is not None:
        assert err in proc.stderr, f"{where}: stderr {proc.stderr!r} lacks {err!r}"


def files(content: dict[str, str]) -> Callable[[Path], None]:
    def write(work: Path) -> None:
        for name, text in content.items():
            target = work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_text(text, encoding="utf-8")

    return write


def descend(value: object, *keys: str) -> object:
    for key in keys:
        assert isinstance(value, dict), f"{key}: not inside an object"
        value = cast("dict[str, object]", value)[key]
    return value


def parse(text: str) -> object:
    return cast("object", json.loads(text))


@pytest.mark.ac("gh-108:AC-2")
def test_every_block_has_a_marker_and_a_test_case() -> None:
    assert set(catalog_blocks()) == COVERED


@pytest.mark.ac("gh-108:AC-3")
def test_missing_node_fails_instead_of_skipping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(pytest.fail.Exception, match="node is not on PATH"):
        _ = run_hook(".claude/hooks/force-skill-eval.js", {}, tmp_path)


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/force-skill-eval.js")
def test_force_skill_eval_prints_the_reminder(tmp_path: Path) -> None:
    marker = ".claude/hooks/force-skill-eval.js"
    expect(run_hook(marker, {}, tmp_path), marker, {}, out="MANDATORY: Before responding, evaluate")


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/force-skill-eval-tuned.js")
@pytest.mark.parametrize(
    ("payload", "reminder"),
    [
        ({"prompt": "please review this"}, True),
        ({"prompt": "DEPLOY it"}, True),
        ({"prompt": "hello there"}, False),
        ({}, False),
    ],
)
def test_tuned_force_skill_eval_filters_by_keyword(tmp_path: Path, payload: dict[str, str], reminder: bool) -> None:
    marker = ".claude/hooks/force-skill-eval-tuned.js"
    out = "MANDATORY: Evaluate installed skills before proceeding." if reminder else ""
    expect(run_hook(marker, payload, tmp_path), marker, payload, out=out)


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/validate-output.js")
@pytest.mark.parametrize("key", ["path", "file_path"])
@pytest.mark.parametrize(
    ("name", "present", "code"),
    [
        ("x.ts", [], 2),
        ("x.tsx", [], 2),
        ("x.ts", ["x.test.ts"], 0),
        ("x.ts", ["__tests__/x.test.ts"], 0),
        ("x.test.ts", [], 0),
        ("x.py", [], 0),
    ],
)
def test_validate_output_warns_without_a_test_file(
    tmp_path: Path, key: str, name: str, present: list[str], code: int
) -> None:
    marker = ".claude/hooks/validate-output.js"
    payload = {"tool_input": {key: name}}
    proc = run_hook(marker, payload, tmp_path, setup=files(dict.fromkeys(present, "")))
    expect(proc, marker, payload, code=code, err=f"WARNING: {name} was written without a test file." if code else None)


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/validate-output.js")
@pytest.mark.parametrize("payload", [{}, {"tool_input": {}}, {"tool_input": {"path": ""}}])
def test_validate_output_ignores_a_payload_without_a_path(tmp_path: Path, payload: dict[str, object]) -> None:
    marker = ".claude/hooks/validate-output.js"
    expect(run_hook(marker, payload, tmp_path), marker, payload, out="")


def preprocess(tmp_path: Path, key: str) -> tuple[object, Path]:
    marker = ".claude/hooks/preprocess-context.js"
    log = "\n".join(["info line"] * 10 + ["ERROR boom"])
    payload = {"tool_input": {key: "app.log", "limit": 5}}
    proc = run_hook(marker, payload, tmp_path, setup=files({"app.log": log}))
    expect(proc, marker, payload)
    result = parse(proc.stdout)
    return result, Path(str(descend(result, "hookSpecificOutput", "updatedInput", key)))


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/preprocess-context.js")
@pytest.mark.parametrize("key", ["path", "file_path"])
def test_preprocess_context_replaces_the_log_path(tmp_path: Path, key: str) -> None:
    result, filtered = preprocess(tmp_path, key)
    assert descend(result, "hookSpecificOutput", "hookEventName") == "PreToolUse"
    assert descend(result, "hookSpecificOutput", "updatedInput", "limit") == 5
    assert filtered.parent.parent == tmp_path / "tmp"
    assert filtered.parent.name.startswith("claude-preprocess-")
    assert filtered.read_text(encoding="utf-8") == "[11 lines → 1]\n\nERROR boom"
    assert (filtered.parent / ".skillz-preprocessed").read_text(encoding="utf-8") == "skillz-preprocess-v1\n"


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/preprocess-context.js")
@pytest.mark.parametrize(
    ("name", "content"),
    [("app.txt", "ERROR x\n"), ("app.log", "ERROR a\nWARN b\n"), ("app.log", "ERROR a\nERROR b\nok")],
)
def test_preprocess_context_leaves_other_reads_alone(tmp_path: Path, name: str, content: str) -> None:
    marker = ".claude/hooks/preprocess-context.js"
    payload = {"tool_input": {"path": name}}
    proc = run_hook(marker, payload, tmp_path, setup=files({name: content}))
    expect(proc, marker, payload, out="")
    assert not list((tmp_path / "tmp").iterdir())


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/cleanup-preprocessed.js")
@pytest.mark.parametrize("key", ["path", "file_path"])
def test_cleanup_removes_the_directory_it_owns(tmp_path: Path, key: str) -> None:
    _, filtered = preprocess(tmp_path, key)
    marker = ".claude/hooks/cleanup-preprocessed.js"
    payload = {"tool_input": {key: str(filtered)}}
    expect(run_hook(marker, payload, tmp_path), marker, payload, out="")
    assert not filtered.parent.exists()


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/cleanup-preprocessed.js")
def test_cleanup_keeps_a_directory_that_holds_other_files(tmp_path: Path) -> None:
    _, filtered = preprocess(tmp_path, "path")
    _ = (filtered.parent / "keep.txt").write_text("keep", encoding="utf-8")
    marker = ".claude/hooks/cleanup-preprocessed.js"
    payload = {"tool_input": {"path": str(filtered)}}
    expect(run_hook(marker, payload, tmp_path), marker, payload)
    assert [p.name for p in filtered.parent.iterdir()] == ["keep.txt"]


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/cleanup-preprocessed.js")
@pytest.mark.parametrize(
    ("relative", "marker_text"),
    [
        ("claude-preprocess-a/filtered.txt", None),
        ("claude-preprocess-a/filtered.txt", "wrong\n"),
        ("other-dir/filtered.txt", "skillz-preprocess-v1\n"),
        ("claude-preprocess-a/other.txt", "skillz-preprocess-v1\n"),
    ],
)
def test_cleanup_ignores_a_path_it_does_not_own(tmp_path: Path, relative: str, marker_text: str | None) -> None:
    target = tmp_path / "tmp" / relative
    target.parent.mkdir(parents=True)
    _ = target.write_text("data", encoding="utf-8")
    if marker_text is not None:
        _ = (target.parent / ".skillz-preprocessed").write_text(marker_text, encoding="utf-8")
    marker = ".claude/hooks/cleanup-preprocessed.js"
    payload = {"tool_input": {"path": str(target)}}
    expect(run_hook(marker, payload, tmp_path), marker, payload, out="")
    assert target.read_text(encoding="utf-8") == "data"


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/cleanup-preprocessed.js")
def test_cleanup_ignores_a_payload_without_a_path(tmp_path: Path) -> None:
    marker = ".claude/hooks/cleanup-preprocessed.js"
    expect(run_hook(marker, {"tool_input": {}}, tmp_path), marker, {"tool_input": {}}, out="")


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/banned-patterns.js")
@pytest.mark.parametrize(
    ("name", "content", "violation"),
    [
        ("app.js", "console.log('x')\n", "Use project logger instead of console.log (1x)"),
        ("app.js", "console.log(1);console.log(2)\n", "Use project logger instead of console.log (2x)"),
        ("app.js", "// TODO later\n", "Resolve TODO/FIXME before committing (1x)"),
        ("app.test.js", "// HACK\n", "Resolve TODO/FIXME before committing (1x)"),
        ("app.test.js", "console.log(1)\n", None),
        ("scripts/run.js", "console.log(1)\n", None),
        ("clean.js", "const a = 1;\n", None),
    ],
)
def test_banned_patterns_reports_violations(
    tmp_path: Path, name: str, content: str, violation: str | None
) -> None:
    marker = ".claude/hooks/banned-patterns.js"
    payload = {"tool_input": {"path": name}}
    proc = run_hook(marker, payload, tmp_path, setup=files({name: content}))
    expect(proc, marker, payload, code=2 if violation else 0, err=f"Pattern violations in {name}:" if violation else None)
    if violation:
        assert violation in proc.stderr


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/banned-patterns.js")
def test_banned_patterns_reads_the_file_path_key(tmp_path: Path) -> None:
    marker = ".claude/hooks/banned-patterns.js"
    payload = {"tool_input": {"file_path": "app.js"}}
    proc = run_hook(marker, payload, tmp_path, setup=files({"app.js": "console.log(1)\n"}))
    expect(proc, marker, payload, code=2, err="Pattern violations in app.js:")


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/banned-patterns.js")
@pytest.mark.parametrize("payload", [{}, {"tool_input": {"path": "missing.js"}}])
def test_banned_patterns_ignores_a_missing_file(tmp_path: Path, payload: dict[str, object]) -> None:
    marker = ".claude/hooks/banned-patterns.js"
    expect(run_hook(marker, payload, tmp_path), marker, payload, out="")


def counter_run(tmp_path: Path, state: str | None) -> subprocess.CompletedProcess[str]:
    marker = ".claude/hooks/token-budget-check.js"
    if state is not None:
        _ = (tmp_path / "counter.json").write_text(state, encoding="utf-8")
    return run_hook(marker, {}, tmp_path)


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/token-budget-check.js")
@pytest.mark.parametrize(
    ("state", "count", "reminder"),
    [
        (None, 1, False),
        ('{"count":4,"lastCompact":1}', 5, False),
        ('{"count":19,"lastCompact":1}', 20, True),
        ('{"count":20,"lastCompact":1}', 21, False),
        ('{"count":39,"lastCompact":1}', 40, True),
    ],
)
def test_token_budget_counts_prompts(tmp_path: Path, state: str | None, count: int, reminder: bool) -> None:
    marker = ".claude/hooks/token-budget-check.js"
    out = f"Context check: {count} prompts. Consider /compact if responses feel imprecise." if reminder else ""
    expect(counter_run(tmp_path, state), marker, state, out=out)
    assert descend(parse((tmp_path / "counter.json").read_text(encoding="utf-8")), "count") == count


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/token-budget-check.js")
def test_token_budget_stops_on_malformed_state(tmp_path: Path) -> None:
    marker = ".claude/hooks/token-budget-check.js"
    expect(counter_run(tmp_path, "{not json"), marker, "{not json", code=1, err="SyntaxError")


@pytest.mark.ac("gh-108:AC-1")
@covers(".claude/hooks/token-budget-check.js")
def test_token_budget_stops_when_the_state_path_is_a_directory(tmp_path: Path) -> None:
    marker = ".claude/hooks/token-budget-check.js"
    (tmp_path / "counter.json").mkdir()
    expect(counter_run(tmp_path, None), marker, "directory", code=1, err="EISDIR")


@pytest.mark.ac("gh-108:AC-4")
def test_hook_runs_touch_only_tmp_path(tmp_path: Path) -> None:
    catalog = catalog_blocks()
    assert COUNTER in catalog[".claude/hooks/token-budget-check.js"], "the counter path moved; update COUNTER"
    before = Path(COUNTER).stat().st_mtime_ns if Path(COUNTER).exists() else None
    _ = counter_run(tmp_path, None)
    _, filtered = preprocess(tmp_path, "path")
    after = Path(COUNTER).stat().st_mtime_ns if Path(COUNTER).exists() else None
    assert before == after
    assert (tmp_path / "counter.json").is_file()
    assert filtered.is_relative_to(tmp_path)
