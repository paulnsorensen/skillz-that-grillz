"""Adversarial attacks on the run compatibility fingerprint: resume backfill, an unjudged contract, and a lost scoring file."""
from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import skillz_experiments
from skillz_experiments._records import read, write
from skillz_experiments.harness._harness import Configuration
from skillz_experiments.run import compare
from skillz_experiments.run._resume import frozen_statistics
from skillz_experiments.run._session import Session, scoring_digest

PACKAGE = Path(skillz_experiments.__file__).parent


def _run(base: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]],
         *, judged: bool) -> tuple[Path, Configuration, dict[str, object]]:
    """Run once under the command harness. A judged run drops the contract file, so the intake contract judges."""
    base.mkdir()
    wrapper, settings, config = base / "wrapper.py", base / "settings.json", base / "harness.json"
    _ = shutil.copyfile(Path(__file__).parent / "fixtures/command_wrapper.py", wrapper)
    _ = settings.write_text(json.dumps({"mode": "normal", "log": str(base / "requests.jsonl")}))
    _ = config.write_text(json.dumps({
        "schema_version": 1, "adapter": "command", "identity": "protocol-fixture",
        "command": [sys.executable, str(wrapper), str(settings)], "roles": {"judge": {"model": "judge-a"}}}))
    target, out = make_target(base), base / "run"
    if judged:
        (target / "evals/autoimprove.json").unlink()
    configuration = Configuration.load(config, "task-model")
    _ = approved_run(target, out, model="task-model", configuration=configuration)
    return out, configuration, read(out / "run.json")


def _reopen(out: Path, configuration: Configuration, record: dict[str, object]) -> Session:
    return Session(out, "task-model", "claude", None, configuration, statistics=frozen_statistics(record))


def _frozen(record: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], record["compatibility"])


def test_a_frozen_fingerprint_survives_a_reopen_unchanged(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    out, configuration, record = _run(tmp_path / "a", make_target, approved_run, judged=True)
    frozen = {"fingerprint": "kept", "fields": {"edit": "kept"}}
    record["compatibility"] = frozen
    write(out / "run.json", record)
    session = _reopen(out, configuration, record)
    session.provider.close()
    assert session.record["compatibility"] == frozen


def test_an_unjudged_contract_freezes_a_null_judge_model_and_the_claude_defaults(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    _, _, record = _run(tmp_path / "a", make_target, approved_run, judged=False)
    fields = cast(dict[str, object], _frozen(record)["fields"])
    assert "judge_model" in fields and fields["judge_model"] is None
    assert fields["claude_options"] == {}


def test_a_judged_and_an_unjudged_run_differ_in_the_judge_model_and_the_contract(
        tmp_path: Path, make_target: Callable[..., Path], approved_run: Callable[..., dict[str, object]]) -> None:
    _, _, judged = _run(tmp_path / "a", make_target, approved_run, judged=True)
    _, _, plain = _run(tmp_path / "b", make_target, approved_run, judged=False)
    named = compare(judged, plain)
    assert "judge_model" in named and "contract_hash" in named
    assert compare(plain, judged) == named


def test_a_scoring_file_that_goes_missing_stops_the_digest_instead_of_keeping_history(tmp_path: Path) -> None:
    copy = tmp_path / "skillz_experiments"
    _ = shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "_audit.py").unlink()
    with pytest.raises(FileNotFoundError):
        _ = scoring_digest(copy)


def test_a_non_source_file_in_gate_keeps_the_scoring_digest(tmp_path: Path) -> None:
    copy = tmp_path / "skillz_experiments"
    _ = shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    before = scoring_digest(copy)
    _ = (copy / "gate" / "notes.txt").write_text("scratch\n")
    _ = (copy / "gate" / "_rules.pyc").write_bytes(b"\x00")
    assert scoring_digest(copy) == before
