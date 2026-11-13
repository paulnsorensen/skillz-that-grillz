"""`validate` runs each gate file on its pass and fail fixtures and on a null trace (gh-657)."""
from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from skillz_experiments import _gate_files
from skillz_experiments._seed import seed_cases
from skillz_experiments._validate import validate

ROOT = Path(__file__).resolve().parents[4]
JOURNEY_KIT = Path(__file__).parent / "fixtures/culture-journey"
GATE = "evals/gates/journey.py"
BASE = (ROOT / "packages/skillz/src/skillz_experiments/_journey_gate.py").read_text(encoding="utf-8")
RECEIPTS = 'RECEIPTS: list[Matcher] = [Matcher("checked", tool="Bash", input="validate")]'
ANSWER_GATE = ("import json\nimport pathlib\n\nanswer = pathlib.Path('output/answer.txt')\n"
               + "text = answer.read_text() if answer.is_file() else ''\nprint(json.dumps({'score': 1 if 'ok' in text else 0}))\n")
Finding = dict[str, object]


def _claude(command: str) -> list[str]:
    block = {"type": "tool_use", "name": "Bash", "input": {"command": command}}
    return [json.dumps({"type": "assistant", "message": {"content": [block]}})]


def _codex(command: str) -> list[str]:
    """A Codex agent turn is an `agentMessage`, so the flow opens with one."""
    items = [{"type": "agentMessage", "text": "start"},
             {"type": "commandExecution", "id": "c1", "exitCode": 0, "command": command}]
    return [json.dumps({"method": "item/completed", "params": {"item": item}}) for item in items]


def _journey(tmp_path: Path, receipts: bool = True, gate: str | None = None) -> Path:
    target = tmp_path / "culture"
    _ = shutil.copytree(JOURNEY_KIT, target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    (target / "evals/gates/journey.py.fixture").unlink()
    source = gate or (BASE.replace("RECEIPTS: list[Matcher] = []", RECEIPTS) if receipts else BASE)
    _ = (target / GATE).write_text(source, encoding="utf-8")
    return target


def _task(tmp_path: Path, source: str = ANSWER_GATE) -> Path:
    target = tmp_path / "toy"
    (target / "evals/gates").mkdir(parents=True)
    _ = (target / "SKILL.md").write_text("---\nname: toy\ndescription: A toy.\n---\n# Toy\n", encoding="utf-8")
    _ = (target / GATE).write_text(source, encoding="utf-8")
    contract: dict[str, object] = {"schema_version": 1, "status": "approved", "skill": "toy", "invocation": "$toy",
                                   "kinds": {"task": {"grader": "command", "gate": GATE}}, "editable": []}
    _ = (target / "evals/autoimprove.json").write_text(json.dumps(contract), encoding="utf-8")
    return target


def _fixture(target: Path, side: str, answer: str | None = None, trace: list[str] | None = None) -> None:
    base = target / "evals/gates/journey.fixtures" / side
    (base / "output").mkdir(parents=True)
    if answer is not None:
        _ = (base / "output/answer.txt").write_text(answer, encoding="utf-8")
    if trace is not None:
        (base / "trace").mkdir()
        _ = (base / "trace/events.jsonl").write_text("".join(line + "\n" for line in trace), encoding="utf-8")


def _gate_findings(target: Path) -> list[Finding]:
    return [item for item in cast(list[Finding], validate(target)["findings"]) if str(item["code"]).startswith("gate-")]


@pytest.mark.ac("gh-657:AC-1")
def test_a_pass_fixture_that_the_gate_does_not_score_one_is_unpassable(tmp_path: Path) -> None:
    target = _task(tmp_path)
    _fixture(target, "pass", answer="nothing")
    found = [item for item in _gate_findings(target) if item["code"] == "gate-unpassable"]
    assert len(found) == 1 and found[0]["severity"] == "error" and found[0]["file"] == GATE
    assert validate(target)["ok"] is False
    _fixture_ok = target / "evals/gates/journey.fixtures/pass/output/answer.txt"
    _ = _fixture_ok.write_text("ok", encoding="utf-8")
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-unpassable"]


@pytest.mark.ac("gh-657:AC-2")
def test_a_fail_fixture_with_a_score_above_zero_is_permissive(tmp_path: Path) -> None:
    target = _task(tmp_path)
    _fixture(target, "fail", answer="ok")
    found = [item for item in _gate_findings(target) if item["code"] == "gate-permissive"]
    assert len(found) == 1 and found[0]["severity"] == "error"
    _ = (target / "evals/gates/journey.fixtures/fail/output/answer.txt").write_text("bad", encoding="utf-8")
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-permissive"]


@pytest.mark.ac("gh-657:AC-2")
def test_a_fail_fixture_score_outside_zero_to_one_is_invalid_and_not_permissive(tmp_path: Path) -> None:
    target = _task(tmp_path, "print('{\"score\": 2}')\n")
    _fixture(target, "fail", answer="ok")
    assert [item["code"] for item in _gate_findings(target)] == ["gate-fixture-missing"]


@pytest.mark.ac("gh-657:AC-1")
def test_a_fixture_file_outside_trace_and_output_sits_at_the_workspace_root(tmp_path: Path) -> None:
    source = ("import json\nimport pathlib\n\nfacts = pathlib.Path('facts.txt')\n"
              + "print(json.dumps({'score': 1 if facts.is_file() and facts.read_text() == 'ok' else 0}))\n")
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item["code"] for item in _passing(target)] == ["gate-unpassable"]
    _ = (target / "evals/gates/journey.fixtures/pass/facts.txt").write_text("ok", encoding="utf-8")
    assert _passing(target) == []


@pytest.mark.ac("gh-657:AC-1")
@pytest.mark.parametrize("source", ["print('not json')\n", "import sys\nprint('{\"score\": 1}')\nsys.exit(1)\n",
                                    "print('{\"score\": true}')\n"])
def test_a_gate_that_prints_no_valid_score_or_exits_nonzero_is_unpassable(tmp_path: Path, source: str) -> None:
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item["code"] for item in _gate_findings(target)] == ["gate-unpassable", "gate-fixture-missing"]


@pytest.mark.ac("gh-657:AC-3")
@pytest.mark.ac("gh-657:AC-30")
def test_a_journey_gate_that_scores_a_null_trace_above_zero_warns(tmp_path: Path) -> None:
    target = _journey(tmp_path, receipts=False)
    found = [item for item in _gate_findings(target) if item["code"] == "gate-null-trace"]
    assert len(found) == 1 and found[0]["severity"] == "warning" and found[0]["file"] == GATE


@pytest.mark.ac("gh-657:AC-3")
def test_a_journey_gate_with_receipts_scores_a_null_trace_zero(tmp_path: Path) -> None:
    assert not [item for item in _gate_findings(_journey(tmp_path)) if item["code"] == "gate-null-trace"]


@pytest.mark.ac("gh-657:AC-3")
def test_a_task_kind_gets_no_null_trace_check(tmp_path: Path) -> None:
    target = _task(tmp_path, "import json\nprint(json.dumps({'score': 1}))\n")
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-null-trace"]


@pytest.mark.ac("gh-657:AC-4")
def test_a_gate_with_no_fixtures_warns_and_leaves_the_case_identity_alone(tmp_path: Path) -> None:
    target = _journey(tmp_path)
    bare, seed = validate(target), json.dumps(seed_cases(target), sort_keys=True)
    found = [item for item in cast(list[Finding], bare["findings"]) if item["code"] == "gate-fixture-missing"]
    assert len(found) == 1 and found[0]["severity"] == "warning" and found[0]["file"] == GATE
    _fixture(target, "pass", trace=_claude("just validate"))
    _fixture(target, "fail", trace=_claude("ls"))
    full = validate(target)
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-fixture-missing"]
    assert json.dumps(seed_cases(target), sort_keys=True) == seed
    assert full["cases"] == bare["cases"] and full["split"] == bare["split"] and full["contract"] == bare["contract"]


@pytest.mark.ac("gh-657:AC-5")
@pytest.mark.ac("gh-657:AC-30")
@pytest.mark.parametrize("encode", [_claude, _codex])
def test_a_fixture_trace_in_either_harness_encoding_runs_the_gate(tmp_path: Path, encode: Callable[[str], list[str]]) -> None:
    target = _journey(tmp_path)
    _fixture(target, "pass", trace=encode("just validate"))
    _fixture(target, "fail", trace=encode("ls"))
    assert [item for item in _gate_findings(target) if item["code"] != "gate-null-trace"] == []


@pytest.mark.ac("gh-657:AC-5")
def test_a_gate_runs_isolated_in_a_temporary_directory(tmp_path: Path) -> None:
    source = ("import json\nimport pathlib\nimport sys\npathlib.Path('marker').write_text('x')\n"
              + "print(json.dumps({'score': 1 if sys.flags.isolated else 0}))\n")
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item for item in _gate_findings(target) if item["severity"] == "error"] == []
    assert not list(Path.cwd().glob("marker")) and not list(tmp_path.rglob("marker"))


@pytest.mark.ac("gh-657:AC-5")
def test_a_gate_that_outlives_the_timeout_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gate_files, "FIXTURE_SECONDS", 0.5)
    target = _task(tmp_path, "import time\ntime.sleep(30)\n")
    _fixture(target, "pass", answer="ok")
    found = [item for item in _gate_findings(target) if item["code"] == "gate-fixture-timeout"]
    assert len(found) == 1 and found[0]["severity"] == "error"
    assert not [item for item in _gate_findings(target) if item["code"] == "gate-unpassable"]


def _shipped_stdout(side: str, tmp_path: Path) -> str:
    fixture = ROOT / "skills/skillz/evals/gates/journey.fixtures" / side
    assert (fixture / "trace/events.jsonl").is_file() and (fixture / "output").is_dir()
    for part in ("trace", "output"):
        _ = shutil.copytree(fixture / part, tmp_path / part)
    source, _paths = _gate_files._bundle(ROOT / "skills/skillz", "evals/gates/journey.py")  # pyright: ignore[reportPrivateUsage]
    done = subprocess.run([sys.executable, "-I", "-c", source], cwd=tmp_path, capture_output=True, text=True, check=False)
    return done.stdout


@pytest.mark.ac("gh-657:AC-1")
@pytest.mark.ac("gh-657:AC-2")
def test_the_shipped_journey_gate_passes_and_fails_its_own_fixtures(tmp_path: Path) -> None:
    assert _gate_findings(ROOT / "skills/skillz") == []
    (tmp_path / "pass").mkdir()
    (tmp_path / "fail").mkdir()
    assert json.loads(_shipped_stdout("pass", tmp_path / "pass"))["score"] == 1
    failed = cast(dict[str, object], json.loads(_shipped_stdout("fail", tmp_path / "fail")))
    assert failed["score"] == 0 and failed["failed"] == ["out-inside-target"]


def _bare(tmp_path: Path) -> Path:
    target = tmp_path / "bare"
    target.mkdir()
    _ = (target / "SKILL.md").write_text("---\nname: bare\ndescription: A bare skill.\n---\n# Bare\n", encoding="utf-8")
    return target


def _passing(target: Path) -> list[Finding]:
    return [item for item in _gate_findings(target) if item["code"] != "gate-fixture-missing"]


@pytest.mark.ac("gh-657:AC-4")
def test_a_skill_with_no_contract_file_validates_without_a_gate_probe(tmp_path: Path) -> None:
    report = validate(_bare(tmp_path))
    assert cast(dict[str, object], report["contract"])["source"] == "intake"
    assert not [item for item in cast(list[Finding], report["findings"]) if str(item["code"]).startswith("gate-")]


@pytest.mark.ac("gh-657:AC-5")
def test_a_secret_in_the_parent_environment_does_not_reach_the_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATE_TEST_SECRET", "hunter2")
    source = "import json\nimport os\nprint(json.dumps({'score': 0 if 'GATE_TEST_SECRET' in os.environ else 1}))\n"
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert _passing(target) == []


@pytest.mark.ac("gh-657:AC-5")
def test_a_timed_out_gate_leaves_no_surviving_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gate_files, "FIXTURE_SECONDS", 0.5)
    pid_file = tmp_path / "child.pid"
    source = ("import pathlib\nimport subprocess\nimport time\n"
              + f"child = subprocess.Popen(['sleep', '60'])\npathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\ntime.sleep(60)\n")
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item["code"] for item in _gate_findings(target)] == ["gate-fixture-timeout", "gate-fixture-missing"]
    pid = int(pid_file.read_text(encoding="utf-8"))
    for _attempt in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    pytest.fail("the grandchild survived the gate timeout")


@pytest.mark.ac("gh-657:AC-5")
@pytest.mark.parametrize("source", ["import sys\nsys.stdout.buffer.write(b'\\xff\\xfe{\"score\": 1}')\n",
                                    "import sys\nsys.stdout.write('{\"score\": 1, \"pad\": \"' + 'x' * 70000 + '\"}')\n"])
def test_a_gate_with_undecodable_or_oversized_output_is_unpassable(tmp_path: Path, source: str) -> None:
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert [item["code"] for item in _passing(target)] == ["gate-unpassable"]


@pytest.mark.ac("gh-657:AC-5")
def test_a_gate_that_floods_stderr_still_scores(tmp_path: Path) -> None:
    source = "import json\nimport sys\nsys.stderr.write('e' * 500000)\nprint(json.dumps({'score': 1}))\n"
    target = _task(tmp_path, source)
    _fixture(target, "pass", answer="ok")
    assert _passing(target) == []


@pytest.mark.ac("gh-657:AC-6")
@pytest.mark.parametrize("where", ["inside", "fixtures", "side"])
def test_a_fixture_with_a_symbolic_link_is_unsafe(tmp_path: Path, where: str) -> None:
    target = _task(tmp_path, "import json\nprint(json.dumps({'score': 1}))\n")
    _fixture(target, "pass", answer="ok")
    base = target / "evals/gates/journey.fixtures"
    if where == "inside":
        (base / "pass/output/link").symlink_to(tmp_path)
    elif where == "side":
        real = tmp_path / "real"
        _ = (base / "pass").rename(real)
        (base / "pass").symlink_to(real)
    else:
        real = tmp_path / "real"
        _ = base.rename(real)
        base.symlink_to(real)
    assert [item["code"] for item in _passing(target)] == ["gate-fixture-unsafe"]


@pytest.mark.ac("gh-657:AC-6")
def test_a_fixture_with_too_many_files_is_unsafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gate_files, "FIXTURE_FILES", 2)
    target = _task(tmp_path, "import json\nprint(json.dumps({'score': 1}))\n")
    _fixture(target, "pass", answer="ok")
    for number in range(3):
        _ = (target / f"evals/gates/journey.fixtures/pass/output/f{number}").write_text("x", encoding="utf-8")
    assert [item["code"] for item in _passing(target)] == ["gate-fixture-unsafe"]


@pytest.mark.ac("gh-657:AC-6")
def test_a_fixture_with_a_pipe_is_unsafe_and_does_not_crash_validate(tmp_path: Path) -> None:
    target = _task(tmp_path, "import json\nprint(json.dumps({'score': 1}))\n")
    _fixture(target, "pass", answer="ok")
    os.mkfifo(target / "evals/gates/journey.fixtures/pass/output/pipe")
    assert [item["code"] for item in _passing(target)] == ["gate-fixture-unsafe"]


@pytest.mark.ac("gh-657:AC-6")
def test_a_fixture_probe_that_raises_is_reported_as_unsafe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise subprocess.SubprocessError("Exception occurred in preexec_fn.")

    monkeypatch.setattr(_gate_files, "_limit_file_size", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    target = _task(tmp_path, "import json\nprint(json.dumps({'score': 1}))\n")
    _fixture(target, "pass", answer="ok")
    assert [item["code"] for item in _passing(target)] == ["gate-fixture-unsafe"]


@pytest.mark.ac("gh-657:AC-3")
def test_the_null_trace_is_one_finished_empty_turn() -> None:
    events = [cast(dict[str, object], json.loads(line)) for line in _gate_files.NULL_TRACE.splitlines()]
    assert [event["type"] for event in events] == ["assistant", "result"]


@pytest.mark.ac("gh-657:AC-5")
def test_a_journey_gate_that_outlives_the_timeout_on_the_null_trace_is_reported(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_gate_files, "FIXTURE_SECONDS", 0.5)
    found = [item for item in _gate_findings(_journey(tmp_path, gate="import time\ntime.sleep(30)\n"))
             if item["code"] == "gate-fixture-timeout"]
    assert len(found) == 1 and "a null trace" in str(found[0]["message"])


@pytest.mark.ac("gh-657:AC-6")
def test_a_null_trace_probe_that_raises_does_not_crash_validate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("no process slot")

    target = _journey(tmp_path, receipts=False)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert [item["code"] for item in _gate_findings(target)] == ["gate-fixture-missing"]


@pytest.mark.ac("gh-657:AC-5")
def test_the_file_size_limit_stops_a_write_past_the_fixture_byte_cap(tmp_path: Path) -> None:
    script = ("import resource, signal, sys\n"
              + "from skillz_experiments._gate_files import FIXTURE_BYTES, _limit_file_size\n"
              + "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n_limit_file_size()\n"
              + "assert resource.getrlimit(resource.RLIMIT_FSIZE) == (FIXTURE_BYTES, FIXTURE_BYTES)\n"
              + "out = open(sys.argv[1], 'wb', buffering=0)\n_ = out.write(b'x' * (FIXTURE_BYTES + 1))\n"
              + "try:\n    _ = out.write(b'x')\n"
              + "except OSError as error:\n    print(error.errno)\n")
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "big")], capture_output=True, text=True, check=False)
    assert (done.returncode, done.stdout.strip()) == (0, str(errno.EFBIG)), done.stderr
    assert (tmp_path / "big").stat().st_size == _gate_files.FIXTURE_BYTES
