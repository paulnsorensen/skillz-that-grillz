from __future__ import annotations

import json
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments import _cli
from skillz_experiments._cases import CodedError
from skillz_experiments.gate import simulate


@final
class _Adapter:
    def __init__(self, code: str) -> None:
        self.code: str = code
        self.closed: int = 0

    def preflight(self) -> dict[str, object]:
        return {"isolation": "passed"}

    def close(self) -> None:
        self.closed += 1
        raise CodedError(self.code, f"close says {self.code}")


def _configuration(adapter: _Adapter, chosen: list[str] | None = None) -> type:
    picks: list[str] = [] if chosen is None else chosen

    @final
    class Configuration:
        @classmethod
        def load(cls, path: Path | None, model: str) -> Configuration:
            picks.append(f"load {path} {model}")
            return cls()

        @classmethod
        def single(cls, adapter_name: str, model: str) -> Configuration:
            picks.append(f"single {adapter_name} {model}")
            return cls()

        def create(self, *args: object) -> _Adapter:
            del args
            return adapter

    return Configuration


@pytest.mark.parametrize("code", ["credential-refreshed", "credential-rotated"])
def test_a_notice_code_from_close_is_a_warning_on_a_passing_preflight(
        monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    monkeypatch.setattr(_cli, "Configuration", _configuration(_Adapter(code)))
    result = _cli.self_test(model="m", preflight_only=True)
    assert result == {"isolation": "passed", "close_warning": f"close says {code}"}


def test_a_terminal_code_from_close_still_fails_the_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _Adapter("credential-changed")
    monkeypatch.setattr(_cli, "Configuration", _configuration(adapter))
    with pytest.raises(_cli._Reported):  # pyright: ignore[reportPrivateUsage]
        _ = _cli.self_test(model="m", preflight_only=True)
    assert adapter.closed == 1


@pytest.mark.ac("gh-131:AC-16")
def test_preflight_only_runs_claude_unless_a_harness_config_names_the_roles(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    chosen: list[str] = []
    monkeypatch.setattr(_cli, "Configuration", _configuration(_Adapter("credential-refreshed"), chosen))
    config = tmp_path / "harness.json"
    _ = _cli.self_test(model="m", preflight_only=True)
    _ = _cli.self_test(model="m", preflight_only=True, harness_config=config)
    assert chosen == ["single claude m", f"load {config} m"]


@pytest.mark.parametrize("metric", ["false_promotions_2se", "false_promotions_one_holdout"])
def test_the_metric_selector_prints_only_that_count(capsys: pytest.CaptureFixture[str], metric: str) -> None:
    expected = cast(int, getattr(simulate(), metric))
    assert _cli.main(["self-test", "--simulate", "--metric", metric]) == 0
    assert capsys.readouterr().out == f"{expected}\n"


def test_the_rate_selector_prints_only_that_rate(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main(["self-test", "--simulate", "--metric", "rate_2se"]) == 0
    assert float(capsys.readouterr().out) == simulate().rate_2se


def test_the_output_without_a_selector_keeps_its_keys(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main(["self-test", "--simulate"]) == 0
    printed = cast(dict[str, object], json.loads(capsys.readouterr().out))
    assert list(printed) == ["trials", "cases", "false_promotions_2se", "false_promotions_one_holdout",
                             "rate_2se", "rate_one_holdout", "live_calls",
                             "true_promotions_effect_10", "power_effect_10", "true_promotions_effect_20",
                             "power_effect_20", "true_promotions_effect_30", "power_effect_30",
                             "cheaper_loss_5_margin_0", "cheaper_loss_5_margin_2", "cheaper_loss_5_margin_5",
                             "cheaper_loss_0_margin_5"]


def test_the_metric_selector_needs_simulate(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main(["self-test", "--preflight-only", "--model", "m", "--metric", "rate_2se"]) == 1
    assert "--metric requires --simulate" in capsys.readouterr().err


def test_an_unknown_metric_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert _cli.main(["self-test", "--simulate", "--metric", "nope"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--metric" in captured.err
