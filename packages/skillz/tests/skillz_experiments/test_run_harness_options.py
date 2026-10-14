"""`run --harness-config` and `run --harness command --command`: usage rules, host checks, probe, resume."""
from __future__ import annotations

import json
import shutil
import stat
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments.run import _run
from skillz_experiments._cases import CodedError
from skillz_experiments._cli import main
from skillz_experiments.harness import Configuration
from skillz_experiments._records import read
from skillz_experiments.run import Stop, run

FIXTURES = Path(__file__).parent / "fixtures"


def _wrapper(tmp_path: Path, mode: str = "normal") -> tuple[Path, Path, Path]:
    """Copy the protocol fixture. Return the wrapper script, its settings file, and its request log."""
    wrapper = tmp_path / "wrapper.py"
    _ = shutil.copyfile(FIXTURES / "command_wrapper.py", wrapper)
    settings = tmp_path / "settings.json"
    log = tmp_path / "requests.jsonl"
    _ = settings.write_text(json.dumps({"mode": mode, "log": str(log)}))
    return wrapper, settings, log


def _config(tmp_path: Path, mode: str = "normal") -> tuple[Path, Path, Path]:
    wrapper, settings, log = _wrapper(tmp_path, mode)
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "protocol-fixture",
                                      "command": [sys.executable, str(wrapper), str(settings)]}))
    return config, settings, log


def _inline(tmp_path: Path, mode: str = "normal") -> tuple[list[str], Path, Path]:
    wrapper, settings, log = _wrapper(tmp_path, mode)
    flags = [f"--command={part}" for part in (sys.executable, str(wrapper), str(settings))]
    return ["--harness", "command", *flags], settings, log


def _operations(log: Path) -> list[str]:
    return [cast(str, json.loads(line)["operation"]) for line in log.read_text().splitlines()]


def _fake_claude(directory: Path) -> Path:
    path = directory / "claude"
    directory.mkdir(parents=True, exist_ok=True)
    _ = path.write_text((FIXTURES / "fake_claude.py").read_text())
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@final
class _Driver:
    def __init__(self, capsys: pytest.CaptureFixture[str], target: Path, out: Path, harness: list[str]) -> None:
        self.capsys: pytest.CaptureFixture[str] = capsys
        self.target: Path = target
        self.out: Path = out
        self.harness: list[str] = harness

    def call(self, *extra: str) -> tuple[int, dict[str, object], dict[str, object]]:
        code = main(["run", "--target", str(self.target), "--out", str(self.out), "--model", "offline", "--edit", "prose",
                     *self.harness, "--live", *extra])
        captured = self.capsys.readouterr()
        err = cast(dict[str, object], json.loads(captured.err)) if captured.err else {}
        return code, cast(dict[str, object], json.loads(captured.out)) if captured.out else {}, err

    def approve(self) -> tuple[str, str]:
        _, cases, _ = self.call()
        case_hash = cast(str, cases["case_hash"])
        _, budget, _ = self.call("--approve-cases", case_hash)
        return case_hash, str(cast(dict[str, int], budget["estimate"])["calls"])

    def finish(self) -> tuple[int, dict[str, object], dict[str, object]]:
        case_hash, calls = self.approve()
        return self.call("--approve-cases", case_hash, "--approve-budget", calls)


@pytest.fixture
def target(tmp_path: Path, make_target: Callable[..., Path]) -> Path:
    return make_target(tmp_path / "source")


def test_a_config_file_runs_every_role_through_the_command_to_completion(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, _, log = _config(tmp_path)
    out = tmp_path / "run"
    _ = write_draft(out)
    code, result, _ = _Driver(capsys, target, out, ["--harness-config", str(config)]).finish()
    assert code == 0 and result["phase"] == "complete"
    assert {"sandbox", "discover", "infer"} <= set(_operations(log))
    roles = cast(dict[str, dict[str, object]], read(out / "run.json")["harness"])
    assert {name: role["adapter"] for name, role in roles.items()} == {
        "task": "command", "reflection": "command", "judge": "command"}


def test_a_completed_run_returns_its_summary_after_the_config_is_deleted(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, _, _ = _config(tmp_path)
    out = tmp_path / "run"
    _ = write_draft(out)
    driver = _Driver(capsys, target, out, ["--harness-config", str(config)])
    code, _, _ = driver.finish()
    assert code == 0
    config.unlink()
    code, again, err = driver.call()
    assert code == 0 and not err and again["phase"] == "complete"


def test_an_inline_command_builds_one_command_role_for_all_three_roles(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    harness, _, log = _inline(tmp_path)
    out = tmp_path / "run"
    _ = write_draft(out)
    code, result, _ = _Driver(capsys, target, out, harness).finish()
    assert code == 0 and result["phase"] == "complete"
    assert "infer" in _operations(log)
    record = read(out / "run.json")
    assert record["adapter"] == "command"
    roles = cast(dict[str, dict[str, object]], record["harness"])
    assert {role["fingerprint"] for role in roles.values()} and len({role["fingerprint"] for role in roles.values()}) == 1
    assert {role["adapter"] for role in roles.values()} == {"command"}


@pytest.mark.parametrize("case", ["config+harness", "config+command", "command-alone", "harness-command-alone",
                                  "config+harness-command"])
def test_each_conflicting_harness_option_stops_before_any_work(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], case: str) -> None:
    config, _, log = _config(tmp_path)
    options = {"config+harness": ["--harness-config", str(config), "--harness", "claude"],
               "config+command": ["--harness-config", str(config), "--command=python3"],
               "command-alone": ["--command=python3"],
               "harness-command-alone": ["--harness", "command"],
               "config+harness-command": ["--harness-config", str(config), "--harness", "command"]}[case]
    out = tmp_path / "run"
    code = main(["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose", "--live", *options])
    err = cast(dict[str, object], json.loads(capsys.readouterr().err))
    expected = {"config+harness": "--harness-config cannot combine with --harness or --command",
                "config+command": "--harness-config cannot combine with --harness or --command",
                "command-alone": "--command requires --harness command",
                "harness-command-alone": "--harness command requires --command ARGV...",
                "config+harness-command": "--harness-config cannot combine with --harness or --command"}[case]
    assert code != 0 and err["code"] == "usage" and err["error"] == expected
    assert not out.exists() and not log.exists()


def test_a_default_harness_with_a_config_is_not_a_conflict(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    """Omitting `--harness` keeps the old default: a plain run needs no flag, and a config needs none either."""
    config, _, _ = _config(tmp_path)
    out = tmp_path / "run"
    _ = write_draft(out)
    code, stop, _ = _Driver(capsys, target, out, ["--harness-config", str(config)]).call()
    assert code == 1 and "case_hash" in stop


@pytest.mark.parametrize("inside", ["configuration", "script"])
def test_a_config_or_script_inside_the_target_stops_before_work(
        tmp_path: Path, target: Path, capsys: pytest.CaptureFixture[str], inside: str) -> None:
    config, _, log = _config(tmp_path)
    if inside == "configuration":
        config = Path(shutil.copyfile(config, target / "harness.json"))
    else:
        script = Path(shutil.copyfile(tmp_path / "wrapper.py", target / "wrapper.py"))
        document = read(config)
        command = cast(list[str], document["command"])
        command[1] = str(script)
        _ = config.write_text(json.dumps(document))
    out = tmp_path / "run"
    code = main(["run", "--target", str(target), "--out", str(out), "--model", "offline", "--edit", "prose", "--live",
                 "--harness-config", str(config)])
    assert code != 0 and "outside the candidate" in json.loads(capsys.readouterr().err)["error"]
    assert not out.exists() and not log.exists()


def test_a_command_role_skips_doctor_and_a_builtin_role_in_a_config_runs_it(
        tmp_path: Path, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[tuple[str, str | None]] = []

    def fake_report(selections: Sequence[tuple[str, str | None]]) -> dict[str, object]:
        checked.extend(selections)
        return {"ok": True, "harness": "claude", "checks": [], "live_calls": 0}

    monkeypatch.setattr(_run, "host_report", fake_report)
    config, _, _ = _config(tmp_path)
    with pytest.raises(Stop) as command_only:
        _ = run(target, tmp_path / "command-run", "offline", live=True, configuration=Configuration.load(config, "offline"))
    assert command_only.value.code == "cases-missing" and checked == []

    claude = _fake_claude(tmp_path / "bin")
    document = read(config)
    command_role = {key: value for key, value in document.items() if key != "schema_version"}
    _ = config.write_text(json.dumps({"schema_version": 1, "roles": {
        "task": {"adapter": "claude", "command": [str(claude)]}, "reflection": command_role, "judge": command_role}}))
    with pytest.raises(Stop) as mixed:
        _ = run(target, tmp_path / "mixed-run", "offline", live=True, configuration=Configuration.load(config, "offline"))
    assert mixed.value.code == "cases-missing" and checked == [("claude", str(claude))]


def test_a_failed_host_check_of_a_builtin_role_in_a_config_stops_the_run(
        tmp_path: Path, target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_report(selections: Sequence[tuple[str, str | None]]) -> dict[str, object]:
        del selections
        return {"ok": False, "harness": "claude", "checks": [], "live_calls": 0}

    monkeypatch.setattr(_run, "host_report", failing_report)
    claude = _fake_claude(tmp_path / "bin")
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(claude)]}))
    with pytest.raises(Stop) as stop:
        _ = run(target, tmp_path / "run", "offline", live=True, configuration=Configuration.load(config, "offline"))
    assert stop.value.code == "host-not-ready"


def test_a_failed_sandbox_probe_stops_the_run_before_any_task_call(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    harness, _, log = _inline(tmp_path, "writable-candidate")
    out = tmp_path / "run"
    _ = write_draft(out)
    code, _, err = _Driver(capsys, target, out, harness).finish()
    assert code != 0 and "error" in err
    assert _operations(log) == ["sandbox"]
    assert read(out / "run.json")["phase"] == "prepared"


def _failed_first_run(tmp_path: Path, target: Path, write_draft: Callable[..., list[str]],
                      capsys: pytest.CaptureFixture[str], harness: list[str]) -> Path:
    out = tmp_path / "run"
    _ = write_draft(out)
    code, _, _ = _Driver(capsys, target, out, harness).finish()
    assert code != 0 and (out / "run.json").is_file()
    return out


def test_a_changed_config_on_resume_stops_with_harness_changed(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    config, settings, log = _config(tmp_path, "fail")
    harness = ["--harness-config", str(config)]
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    before = log.read_text()
    with settings.open("a") as stream:
        _ = stream.write("\n")
    code, _, err = _Driver(capsys, target, out, harness).call()
    assert code != 0 and err["code"] == "harness-changed"
    assert log.read_text() == before


def test_a_changed_inline_command_on_resume_stops_with_harness_changed(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    harness, _, log = _inline(tmp_path, "fail")
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    before = log.read_text()
    with (tmp_path / "wrapper.py").open("a") as script:
        _ = script.write("\n# changed\n")
    code, _, err = _Driver(capsys, target, out, harness).call()
    assert code != 0 and err["code"] == "harness-changed"
    assert log.read_text() == before


def test_a_resume_that_drops_the_config_stops_with_harness_changed_not_a_single_harness_fallback(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch) -> None:
    config, _, log = _config(tmp_path, "fail")
    out = _failed_first_run(tmp_path, target, write_draft, capsys, ["--harness-config", str(config)])
    before = log.read_text()
    claude = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{claude.parent}:/usr/bin:/bin")
    with pytest.raises(CodedError) as dropped:
        _ = run(target, out, "offline", live=True)
    assert dropped.value.code == "harness-changed"
    assert log.read_text() == before


def test_a_resume_that_drops_the_inline_command_stops_with_harness_changed(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    harness, _, _ = _inline(tmp_path, "fail")
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    code, _, err = _Driver(capsys, target, out, []).call()
    assert code != 0 and err["code"] == "harness-changed"


def test_a_record_without_its_frozen_harness_stops_with_run_record_tampered(
        tmp_path: Path, target: Path, write_draft: Callable[..., list[str]], capsys: pytest.CaptureFixture[str]) -> None:
    from skillz_experiments._records import write

    config, _, log = _config(tmp_path, "fail")
    harness = ["--harness-config", str(config)]
    out = _failed_first_run(tmp_path, target, write_draft, capsys, harness)
    record = read(out / "run.json")
    del record["harness"]
    write(out / "run.json", record)
    before = log.read_text()
    code, _, err = _Driver(capsys, target, out, harness).call()
    assert code != 0 and err["code"] == "run-record-tampered"
    assert log.read_text() == before
