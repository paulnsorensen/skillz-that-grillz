"""gh-707: `self-test --preflight-only --harness-config` takes each role model from the file, and `--model` is optional."""
from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments import _cli
from skillz_experiments.harness import Configuration

ROLES = {"task": {"adapter": "claude", "model": "sonnet"}, "reflection": {"adapter": "claude", "model": "opus"},
         "judge": {"adapter": "claude", "model": "opus"}}


def _config(tmp_path: Path, roles: dict[str, dict[str, str]]) -> Path:
    path = tmp_path / "harness.json"
    _ = path.write_text(json.dumps({"schema_version": 1, "roles": roles}))
    return path


def _error(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return cast(dict[str, object], json.loads(capsys.readouterr().err.strip().splitlines()[-1]))


@pytest.mark.ac("gh-707:AC-1")
def test_a_role_file_that_names_every_model_needs_no_model_flag(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: dict[str, str] = {}

    class Preflight:
        def preflight(self) -> dict[str, object]:
            return {"isolation": "passed"}

        def close(self) -> None:
            return None

    def create(self: Configuration, *args: object, **kwargs: object) -> Preflight:
        del args, kwargs
        seen.update({name: role.model for name, role in self.roles.items()})
        return Preflight()

    # Only the live preflight is replaced; the role file loads through the real `Configuration.load`.
    monkeypatch.setattr(Configuration, "create", create)
    code = _cli.main(["self-test", "--preflight-only", "--harness-config", str(_config(tmp_path, ROLES))])
    assert code == 0 and "traceback" not in capsys.readouterr().err
    assert seen == {"task": "sonnet", "reflection": "opus", "judge": "opus"}


@pytest.mark.ac("gh-707:AC-2")
def test_a_role_without_a_model_stops_with_usage_and_names_the_role(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roles = {**ROLES, "judge": {"adapter": "claude"}}
    assert _cli.main(["self-test", "--preflight-only", "--harness-config", str(_config(tmp_path, roles))]) == 1
    error = _error(capsys)
    assert error["code"] == "usage" and "traceback" not in error
    assert "judge" in str(error["error"]) and "--model" in str(error["error"])


@pytest.mark.ac("gh-707:AC-2")
def test_preflight_only_without_a_model_or_a_role_file_stops_with_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main(["self-test", "--preflight-only"]) == 1
    error = _error(capsys)
    assert error["code"] == "usage" and "traceback" not in error and "--model" in str(error["error"])
