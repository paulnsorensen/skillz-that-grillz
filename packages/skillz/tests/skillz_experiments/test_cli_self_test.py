from __future__ import annotations

from pathlib import Path
from typing import final

import pytest

from skillz_experiments import _cli
from skillz_experiments._cases import CodedError


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


def _configuration(adapter: _Adapter) -> type:
    @final
    class Configuration:
        @classmethod
        def load(cls, path: Path | None, model: str) -> Configuration:
            del path, model
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