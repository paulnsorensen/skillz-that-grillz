"""The run ports: the replay applies patches through `PatchApplier`, and the search uses a `HistoryStore`."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._records import read
from skillz_experiments.run import run
from skillz_experiments.run._history_store import FileHistoryStore, Loaded
from skillz_experiments.run._patch import Patch
from skillz_experiments.run._ports import Ports
from skillz_experiments.run._replay import GitPatchApplier
from skillz_experiments.run._run import _default_ports  # pyright: ignore[reportPrivateUsage]
from test_run_search import MODEL, Search

pytestmark = pytest.mark.usefixtures("host_login", "headroom_off")

Maker = Callable[..., Path]
Drafter = Callable[..., list[str]]
Approver = Callable[..., tuple[str, int]]


@final
class FakeApplier:
    def __init__(self, failure: str | None) -> None:
        self.failure = failure
        self.calls: list[Path] = []

    def apply(self, directory: Path, patch: Patch) -> str | None:
        del patch
        self.calls.append(directory)
        return self.failure


@final
class MemoryHistory:
    def __init__(self, earlier: Loaded | None = None, warning: str | None = None) -> None:
        self.earlier = earlier or Loaded()
        self.warning = warning
        self.loads: list[tuple[str, str, str]] = []
        self.writes: list[tuple[str, str, str, list[Mapping[str, object]]]] = []

    def load(self, skill: str, target_root: str, record: Mapping[str, object], run_id: str) -> Loaded:
        del record
        self.loads.append((skill, target_root, run_id))
        return self.earlier

    def write(self, skill: str, target_root: str, record: Mapping[str, object], run_id: str,
              attempts: Sequence[Mapping[str, object]]) -> str | None:
        del record
        self.writes.append((skill, target_root, run_id, list(attempts)))
        return self.warning


def _start(tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver,
           ) -> tuple[Path, Path, Search, str, int]:
    target, out = make_target(tmp_path), tmp_path / "run"
    _ = write_draft(out)
    case_hash, calls = approvals(target, out)
    return target, out, Search(target), case_hash, calls


@pytest.mark.ac("gh-399:AC-1")
def test_the_replay_applies_the_patches_through_the_injected_applier(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    applier = FakeApplier("fake: patch refused")
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
                 ports=Ports(patches=applier, history=MemoryHistory()))
    record = read(out / "run.json")
    assert result["phase"] == "complete" and applier.calls == [out / "replay"]
    replay = cast(dict[str, object], record["replay"])
    assert replay["reasons"] == ["patch-apply-failed"]
    assert replay["detail"] == {"stderr": "fake: patch refused"}


@pytest.mark.ac("gh-399:AC-2")
def test_the_search_loads_and_records_through_the_injected_history_store(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    history = MemoryHistory(warning="history-unreadable")
    _ = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory,
            ports=Ports(patches=GitPatchApplier(), history=history))
    record = read(out / "run.json")
    run_id = cast(str, record["run_id"])
    assert history.loads == [(state.contract.skill, str(target.resolve()), run_id)]
    assert [write[:3] for write in history.writes] == history.loads
    attempts = cast(dict[str, object], record["search"])["attempts"]
    assert history.writes[0][3] == attempts
    block = cast(dict[str, object], cast(dict[str, object], record["search"])["history"])
    assert block["enabled"] is True and block["warnings"] == ["history-unreadable"]
    assert not (Path(os.environ["XDG_STATE_HOME"]) / "skillz").exists()


@pytest.mark.ac("gh-399:AC-3")
def test_run_without_an_override_uses_the_git_and_xdg_adapters(
        tmp_path: Path, make_target: Maker, write_draft: Drafter, approvals: Approver) -> None:
    ports = _default_ports()
    assert isinstance(ports.patches, GitPatchApplier) and isinstance(ports.history, FileHistoryStore)
    target, out, state, case_hash, calls = _start(tmp_path, make_target, write_draft, approvals)
    result = run(target, out, MODEL, live=True, approve_cases=case_hash, approve_budget=calls, factory=state.factory)
    record = read(out / "run.json")
    assert result["phase"] == "complete" and cast(dict[str, object], record["replay"])["reasons"] == []
    assert (out / "replay" / "candidate.patch").is_file()
    stores = list((Path(os.environ["XDG_STATE_HOME"]) / "skillz" / "history").glob("*.jsonl"))
    assert len(stores) == 1 and cast(str, record["run_id"]) in stores[0].read_text()
