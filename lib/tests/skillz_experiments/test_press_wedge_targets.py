"""Adversarial press of curd C2: frozen bytes, own wedge targets, host rebuild, build-failed, and export.

The builds run offline: a fake closure replaces the network step, while shiv and the archive code run for real.
Each test states the expected behavior in its docstring or name.
"""
from __future__ import annotations

import importlib
import io
import json
import shutil
import subprocess
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast, final
from zipfile import ZipFile

import pytest

import wedge._build as wedge_build
from wedge._build import build_many
from wedge._config import WedgeConfig

from skillz_experiments._candidate import (FROZEN_FILE_LIMIT, FROZEN_TOTAL_LIMIT, PACKAGE_LIMIT, TEXT_FILE_LIMIT, Candidate,
                                           as_text)
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Contract, load_contract
from skillz_experiments._gate import DEFAULT_STATISTICS
from skillz_experiments._records import read, write
from skillz_experiments._runtime import Budget
from skillz_experiments._search import Edit
from skillz_experiments._wedge_targets import Rebuilder, plan_targets
from skillz_experiments._workflow import Stop, export, run

pytestmark = pytest.mark.usefixtures("host_login")

WORKFLOW = importlib.import_module("skillz_experiments._workflow")
MODEL = "local-test"
ALPHA = "@wedge/proj/alpha.py"
BETA = "@wedge/proj/beta/__init__.py"
ALPHA_SOURCE = 'def main() -> None:\n    print("alpha v1")\n'
BETA_SOURCE = 'def main() -> None:\n    print("beta v1")\n'
WEDGE_TOML = """project = "../proj"
repo = "o/n"

[[target]]
name = "alpha"
entry = "alpha:main"
source = "../proj/alpha.py"

[[target]]
name = "beta"
entry = "beta:main"
source = "../proj/beta"
"""


@dataclass
class World:
    """A repository with a project of two targets and a skill that owns both built `.pyz` files."""

    repo: Path
    proj: Path
    skill: Path

    def put(self, name: str, text: str) -> None:
        path = self.proj / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8", newline="")

    def build(self) -> None:
        built = self.repo / "built"
        shutil.rmtree(built, ignore_errors=True)
        for outcome in build_many([self.skill], built):
            assert outcome.error is None and outcome.value is not None, outcome.error
            _ = shutil.copy2(outcome.value.path, self.skill / "scripts" / f"{outcome.value.name}.pyz")

    def contract(self, helper: str | None = None) -> Contract:
        path = self.skill / "evals/autoimprove.json"
        document = cast(dict[str, object], json.loads(path.read_text()))
        if helper is None:
            _ = document.pop("helper", None)
        else:
            document["helper"] = {"path": helper}
        _ = path.write_text(json.dumps(document))
        return load_contract(self.skill)

    def mention(self, text: str) -> None:
        guide = self.skill / "SKILL.md"
        _ = guide.write_text(guide.read_text() + "\n" + text + "\n")

    def pyz(self, name: str) -> bytes:
        return (self.skill / "scripts" / f"{name}.pyz").read_bytes()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Callable[..., Path]) -> World:
    def closure(_project: Path, _groups: object) -> tuple[str, bool]:
        return "", False

    monkeypatch.setattr(wedge_build, "_closure_requirements", closure)
    repo = tmp_path / "repo"
    _ = subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = make_target(repo)
    proj = repo / "proj"
    proj.mkdir()
    _ = (proj / "pyproject.toml").write_text('[project]\nname = "proj"\nversion = "0"\n')
    _ = (proj / "uv.lock").write_text("lock\n")
    built = World(repo, proj, skill)
    built.put("alpha.py", ALPHA_SOURCE)
    built.put("beta/__init__.py", BETA_SOURCE)
    _ = (skill / "wedge.toml").write_text(WEDGE_TOML)
    (skill / "scripts").mkdir(exist_ok=True)
    built.build()
    return built


@final
class Recorder:
    """A provider factory whose provider scores 0 and keeps every candidate that it evaluates."""

    def __init__(self) -> None:
        self.seen: list[Candidate] = []

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> _Provider:
        return _Provider(self, budget, checkpoint)


@final
class _Provider:
    def __init__(self, recorder: Recorder, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.recorder: Recorder = recorder
        self.budget: Budget = budget
        self.checkpoint: Callable[[], None] = checkpoint

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        self.recorder.seen.append(candidate)
        return {"score": 0.0, "candidate_hash": candidate.identity, "case_hash": case.identifier,
                "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case, holdout, schema
        raise AssertionError("no model call expected")


class Session(Protocol):
    """The public face of `_workflow._Session` that these tests touch."""

    seed: Candidate
    rebuilder: Rebuilder | None
    provider: _Provider


def prepared(world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
             approvals: Callable[..., tuple[str, int]], *, edit: str = "prose+cli") -> Path:
    """Give both approvals and stop at `live-required`. Return the run directory."""
    out = tmp_path / "run"
    _ = write_draft(out)
    approved_hash, calls = approvals(world.skill, out, edit=edit)
    with pytest.raises(Stop) as stopped:
        _ = run(world.skill, out, MODEL, edit=cast(Edit, edit), approve_cases=approved_hash, approve_budget=calls)
    assert stopped.value.code == "live-required"
    return out


def open_session(out: Path, recorder: Recorder) -> Session:
    opener = cast(Callable[..., Session], getattr(WORKFLOW, "_Session"))
    return opener(out, MODEL, "claude", recorder.factory, None, statistics=DEFAULT_STATISTICS)


def score(session: Session, components: dict[str, str]) -> tuple[float, dict[str, object]]:
    cases = cast(dict[str, Case], getattr(session, "_search_cases"))
    method = cast(Callable[[dict[str, str], str], tuple[float, dict[str, object]]], getattr(session, "_evaluate_example"))
    return method(components, next(iter(cases)))


def evaluate(out: Path, recorder: Recorder, change: Callable[[dict[str, str]], None]) -> tuple[float, dict[str, object]]:
    session = open_session(out, recorder)
    try:
        components = {name: session.seed.files[name] for name in session.seed.editable}
        change(components)
        return score(session, components)
    finally:
        session.provider.close()


def editable_names(files: dict[str, str], contract: Contract, frozen: Collection[str], plan: object) -> list[str]:
    method = cast(Callable[..., list[str]], getattr(WORKFLOW, "_editable"))
    return method(files, contract, "prose+cli", frozen, plan)


def archive_text(data: bytes, suffix: str) -> str:
    with ZipFile(io.BytesIO(data)) as archive:
        name = next(name for name in archive.namelist() if name.endswith(suffix))
        return archive.read(name).decode()


def record_of(out: Path) -> dict[str, object]:
    return read(out / "run.json")


def set_winner(out: Path, winner_extra: dict[str, str], *, drop: tuple[str, ...] = ()) -> None:
    record = record_of(out)
    seed = cast(dict[str, str], record["seed"])
    record["phase"] = "complete"
    record["winner"] = {name: text for name, text in seed.items() if name not in drop} | winner_extra
    write(out / "run.json", record)


def git_apply(patch: Path, cwd: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "apply", *flags, str(patch)], cwd=cwd, capture_output=True, text=True, check=False)


def count_builds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Wrap the host build so each call is recorded by target name."""
    calls: list[str] = []
    real = wedge_build.build_from_layer

    def counted(layer: wedge_build.SiteLayer, tree: Path, target: WedgeConfig, out_dir: Path) -> Path:
        calls.append(target.name)
        return real(layer, tree, target, out_dir)

    monkeypatch.setattr("skillz_experiments._wedge_targets.build_from_layer", counted)
    return calls


# --- AC-1/AC-2: capture boundaries -------------------------------------------------------------------------

def test_as_text_exactly_at_the_text_limit_stays_text_and_one_more_byte_freezes() -> None:
    assert as_text(b"a" * TEXT_FILE_LIMIT) == "a" * TEXT_FILE_LIMIT
    assert as_text(b"a" * (TEXT_FILE_LIMIT + 1)) is None


def test_as_text_counts_bytes_not_characters() -> None:
    assert as_text("é".encode() * (TEXT_FILE_LIMIT // 2)) is not None
    assert as_text("é".encode() * (TEXT_FILE_LIMIT // 2 + 1)) is None


@pytest.mark.parametrize("data", [b"ok\x00ok", b"\x00", b"\xef\xbb\xbf\x00", b"\xc3\x28", b"\xed\xa0\x80"])
def test_as_text_nul_and_invalid_utf8_freeze(data: bytes) -> None:
    assert as_text(data) is None


@pytest.mark.parametrize(("data", "text"), [(b"", ""), (b"\xef\xbb\xbfx", "﻿x"), ("\U0001f9c0".encode(), "\U0001f9c0")])
def test_as_text_empty_bom_and_astral_stay_text(data: bytes, text: str) -> None:
    assert as_text(data) == text


def test_capture_freezes_a_utf8_file_with_a_nul_and_keeps_the_exact_limit_file_as_text(world: World) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "data/nul.txt").write_bytes(b"valid utf8 \x00 tail")
    _ = (world.skill / "data/edge.txt").write_bytes(b"a" * TEXT_FILE_LIMIT)
    _ = (world.skill / "data/over.txt").write_bytes(b"a" * (TEXT_FILE_LIMIT + 1))
    skeleton = Candidate.capture(world.skill, [], world.contract())
    assert "data/nul.txt" in skeleton.frozen and "data/over.txt" in skeleton.frozen
    assert "data/edge.txt" in skeleton.files and "data/edge.txt" not in skeleton.frozen


def test_capture_accepts_a_frozen_file_of_exactly_the_per_file_limit(world: World) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "data/exact.bin").write_bytes(bytes(FROZEN_FILE_LIMIT))
    skeleton = Candidate.capture(world.skill, [], world.contract())
    assert len(skeleton.frozen["data/exact.bin"]) == FROZEN_FILE_LIMIT


def test_capture_total_of_exactly_64_mib_passes_and_one_more_byte_names_the_largest(world: World) -> None:
    (world.skill / "data").mkdir()
    base = sum(len(data) for data in Candidate.capture(world.skill, [], world.contract()).frozen.values())
    for index in range(3):
        _ = (world.skill / f"data/f{index}.bin").write_bytes(bytes(FROZEN_FILE_LIMIT))
    last = world.skill / "data/last.bin"
    _ = last.write_bytes(bytes(FROZEN_TOTAL_LIMIT - 3 * FROZEN_FILE_LIMIT - base))
    exact = Candidate.capture(world.skill, [], world.contract())
    assert sum(len(data) for data in exact.frozen.values()) == FROZEN_TOTAL_LIMIT
    _ = last.write_bytes(bytes(FROZEN_TOTAL_LIMIT - 3 * FROZEN_FILE_LIMIT - base + 1))
    with pytest.raises(CodedError) as caught:
        _ = Candidate.capture(world.skill, [], world.contract())
    assert caught.value.code == "frozen-file-too-large" and "f0.bin" in str(caught.value)


def test_candidate_per_file_limit_is_exact() -> None:
    assert Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.bin": bytes(FROZEN_FILE_LIMIT)})
    with pytest.raises(CodedError) as caught:
        _ = Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.bin": bytes(FROZEN_FILE_LIMIT + 1)})
    assert caught.value.code == "frozen-file-too-large" and "a.bin" in str(caught.value)


def test_capture_a_text_file_named_pyz_is_text_and_a_binary_one_is_frozen(world: World) -> None:
    _ = (world.skill / "scripts/note.pyz").write_text("just words\n")
    _ = (world.skill / "scripts/real.pyz").write_bytes(b"PK\x03\x04\x00")
    skeleton = Candidate.capture(world.skill, [], world.contract())
    assert skeleton.files["scripts/note.pyz"] == "just words\n"
    assert "scripts/real.pyz" in skeleton.frozen


@pytest.mark.parametrize("name", ["scripts/note.pyz", "scripts/uv.lock", "scripts/wedge.toml"])
def test_editable_text_pyz_lock_or_wedge_toml_under_scripts_is_never_editable(world: World, name: str) -> None:
    """The default editable set skips a `.pyz`, `uv.lock`, or `wedge.toml`; it is then the only code and none is left."""
    _ = (world.skill / name).write_text("just words\n")
    contract = world.contract()
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    with pytest.raises(CodedError) as caught:
        _ = editable_names(skeleton.files | plan.sources, contract, skeleton.frozen.keys(), plan)
    assert caught.value.code == "helper-missing"


def test_materialize_writes_frozen_bytes_with_the_exec_bit_and_never_writes_wedge_sources(world: World, tmp_path: Path) -> None:
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    candidate = Candidate(skeleton.files | plan.sources, ("SKILL.md",), contract, frozen=skeleton.frozen | plan.frozen)
    work = tmp_path / "work"
    work.mkdir()
    candidate.materialize(work)
    assert (work / "scripts/beta.pyz").read_bytes() == world.pyz("beta")
    assert (work / "scripts/beta.pyz").stat().st_mode & 0o111
    assert not (work / "@wedge").exists() and not (work / "proj").exists()


def test_a_symlinked_or_hidden_or_backslash_source_keeps_the_target_frozen(world: World, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    _ = outside.write_text("SECRET\n")
    (world.proj / "beta/link.py").symlink_to(outside)
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert plan.editable == () and "SECRET" not in "".join(plan.sources.values())
    (world.proj / "beta/link.py").unlink()
    world.put("beta/.hidden.py", "x = 1\n")
    assert plan_targets(world.skill, skeleton.files, contract, "prose+cli").editable == ()
    (world.proj / "beta/.hidden.py").unlink()
    world.put("beta/back\\slash.py", "x = 1\n")
    assert plan_targets(world.skill, skeleton.files, contract, "prose+cli").editable == ()


def test_a_helper_that_names_a_foreign_pyz_stops_with_helper_frozen(world: World) -> None:
    _ = (world.skill / "scripts/foreign.pyz").write_bytes(b"\xff\x00\xfe")
    world.mention("Run scripts/alpha.pyz for the cheese.")
    contract = world.contract("scripts/foreign.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert plan.helper_target(contract) is None
    with pytest.raises(CodedError) as caught:
        _ = editable_names(skeleton.files | plan.sources, contract, skeleton.frozen.keys(), plan)
    assert caught.value.code == "helper-frozen" and "scripts/foreign.pyz" in str(caught.value)


def test_a_name_that_only_looks_like_a_mention_does_not_make_a_target_editable(world: World) -> None:
    world.mention("See scripts/my-alpha.pyz and xalpha.pyz and alpha.py.")
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["beta"]


@pytest.mark.parametrize("text", ["See scripts/alpha.pyzz.", "Keep scripts/alpha.pyz.bak around."])
def test_a_longer_name_after_the_pyz_suffix_is_not_a_mention(world: World, text: str) -> None:
    world.mention(text)
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["beta"]

@pytest.mark.parametrize("over", [0, 1])
def test_package_limit_boundary_exact_fit_is_editable_and_one_over_is_frozen(world: World, over: int) -> None:
    world.mention("Run scripts/alpha.pyz.")
    contract = world.contract()
    base = sum(len(text) for text in Candidate.capture(world.skill, [], contract).files.values())
    room = PACKAGE_LIMIT - base - len(ALPHA_SOURCE) + over
    (world.skill / "references").mkdir(exist_ok=True)
    index = 0
    while room > 0:
        size = min(200_000, room)
        _ = (world.skill / f"references/pad{index}.md").write_text("p" * size)
        room -= size
        index += 1
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert (len(plan.editable) == 1) == (over == 0)
    assert "scripts/alpha.pyz" in plan.frozen
    if over == 0:
        assert Candidate(skeleton.files | plan.sources, ("SKILL.md",), contract, frozen=plan.frozen)


# --- AC-6/AC-7: proposals and host rebuild ---------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    "@wedge/proj/beta/new.py", "@wedge/../../etc/passwd", "@wedge/lib/src/other_pkg/x.py", "@wedge//etc/passwd",
    "@wedge/proj\\beta\\x.py", "scripts/beta.pyz", "scripts/alpha.pyz", "wedge.toml", "/etc/passwd", "@wedge/", "@new-cli"])
def test_a_proposal_with_an_extra_component_is_rejected_before_any_build_or_evaluation(
        extra: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]], monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    builds = count_builds(monkeypatch)
    recorder = Recorder()
    result, feedback = evaluate(out, recorder, lambda components: components.update({extra: "x = 1\n", BETA: BETA_SOURCE + "#\n"}))
    assert result == 0.0 and "rejected" in feedback
    assert recorder.seen == [] and builds == []


def test_a_proposal_that_drops_a_component_is_rejected(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    result, feedback = evaluate(out, recorder, lambda components: components.__delitem__(BETA))
    assert result == 0.0 and "rejected" in feedback and recorder.seen == []


def test_a_source_over_the_component_limit_is_rejected_without_a_build(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    builds = count_builds(monkeypatch)
    recorder = Recorder()
    _, feedback = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE + "#" * TEXT_FILE_LIMIT}))
    assert "rejected" in feedback and builds == [] and recorder.seen == []


def test_an_emptied_source_builds_and_changes_the_pyz(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({BETA: ""}))
    assert len(recorder.seen) == 1 and recorder.seen[0].frozen["scripts/beta.pyz"] != world.pyz("beta")


def test_identical_sources_share_one_build_and_different_sources_do_not(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    builds = count_builds(monkeypatch)
    session = open_session(out, Recorder())
    try:
        seed = {name: session.seed.files[name] for name in session.seed.editable}
        v2 = seed | {BETA: BETA_SOURCE.replace("v1", "v2")}
        _ = score(session, v2)
        _ = score(session, dict(v2))
        assert builds == ["beta"]
        _ = score(session, seed | {BETA: BETA_SOURCE.replace("v1", "v3")})
        assert builds == ["beta", "beta"]
        _ = score(session, v2 | {ALPHA: ALPHA_SOURCE.replace("v1", "v2")})
        assert sorted(builds) == ["alpha", "beta", "beta"] or builds[2:] == ["alpha"]
    finally:
        session.provider.close()


def test_a_candidate_that_reverts_to_the_seed_sources_keeps_the_seed_bytes_without_a_build(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    builds = count_builds(monkeypatch)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({"SKILL.md": c["SKILL.md"] + "\nprose only\n"}))
    assert builds == [] and recorder.seen[0].frozen == {"scripts/alpha.pyz": world.pyz("alpha"),
                                                          "scripts/beta.pyz": world.pyz("beta")}


def test_only_the_changed_target_is_rebuilt_and_the_other_keeps_its_seed_bytes(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    builds = count_builds(monkeypatch)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({ALPHA: ALPHA_SOURCE.replace("v1", "v2")}))
    assert builds == ["alpha"]
    assert recorder.seen[0].frozen["scripts/beta.pyz"] == world.pyz("beta")
    assert recorder.seen[0].frozen["scripts/alpha.pyz"] != world.pyz("alpha")


def test_the_host_build_is_byte_identical_to_a_fresh_wedge_build_of_the_edited_disk(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    v2 = BETA_SOURCE.replace("v1", "v2")
    _ = evaluate(out, recorder, lambda c: c.update({BETA: v2}))
    world.put("beta/__init__.py", v2)
    world.build()
    assert recorder.seen[0].frozen["scripts/beta.pyz"] == world.pyz("beta")


def test_two_sessions_build_the_same_bytes_for_the_same_sources(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    for _ in range(2):
        _ = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    first, second = recorder.seen
    assert first.frozen == second.frozen and first.identity == second.identity


def test_a_comment_only_edit_changes_the_pyz_and_the_identity(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: None)
    _ = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE + "# note\n"}))
    seeded, edited = recorder.seen
    assert seeded.frozen["scripts/beta.pyz"] != edited.frozen["scripts/beta.pyz"] and seeded.identity != edited.identity


def test_concurrent_applies_of_one_candidate_build_once_and_agree(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    builds = count_builds(monkeypatch)
    session = open_session(out, Recorder())
    try:
        rebuilder = session.rebuilder
        assert rebuilder is not None
        candidate = session.seed.changed({name: session.seed.files[name] for name in session.seed.editable}
                                         | {BETA: BETA_SOURCE.replace("v1", "v2")})
        results: list[Candidate] = []
        errors: list[BaseException] = []
        gate = threading.Barrier(8)

        def work() -> None:
            try:
                _ = gate.wait()
                results.append(rebuilder.apply(candidate))
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == [] and len(results) == 8
        assert builds == ["beta"]
        assert len({item.identity for item in results}) == 1
    finally:
        session.provider.close()


def test_a_failed_build_is_cached_and_raised_again_without_a_second_build(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    real = wedge_build.build_from_layer
    state = {"fail": True, "calls": 0}

    def flaky(layer: wedge_build.SiteLayer, tree: Path, target: WedgeConfig, out_dir: Path) -> Path:
        state["calls"] += 1
        if state["fail"]:
            raise subprocess.CalledProcessError(1, ["shiv"], stderr="boom é".encode())
        return real(layer, tree, target, out_dir)

    monkeypatch.setattr("skillz_experiments._wedge_targets.build_from_layer", flaky)
    recorder = Recorder()
    session = open_session(out, recorder)
    try:
        components = {name: session.seed.files[name] for name in session.seed.editable} | {BETA: BETA_SOURCE + "#\n"}
        first, feedback = score(session, components)
        assert first == 0.0 and feedback["reason"] == "build-failed" and "boom" in str(feedback["rejected"])
        state["fail"] = False
        second, again = score(session, components)
        assert second == 0.0 and again["reason"] == "build-failed" and "boom" in str(again["rejected"])
        assert state["calls"] == 1 and recorder.seen == []
        other = {name: session.seed.files[name] for name in session.seed.editable} | {BETA: BETA_SOURCE + "#\n#\n"}
        _ = score(session, other)
        assert state["calls"] == 2 and len(recorder.seen) == 1
    finally:
        session.provider.close()


def test_a_non_subprocess_build_error_is_also_build_failed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)

    def broken(*_args: object, **_kwargs: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr("skillz_experiments._wedge_targets.build_from_layer", broken)
    recorder = Recorder()
    result, feedback = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE + "#\n"}))
    assert result == 0.0 and feedback["reason"] == "build-failed" and "disk full" in str(feedback["rejected"])
    assert recorder.seen == []


def test_a_build_failure_message_is_bounded(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)

    def broken(*_args: object, **_kwargs: object) -> Path:
        raise subprocess.CalledProcessError(1, ["shiv"], stderr="x" * 1_000_000)

    monkeypatch.setattr("skillz_experiments._wedge_targets.build_from_layer", broken)
    _, feedback = evaluate(Path(out), Recorder(), lambda c: c.update({BETA: BETA_SOURCE + "#\n"}))
    assert len(str(feedback["rejected"])) < 2_000


# --- resume and disk drift ---------------------------------------------------------------------------------------

def test_a_resume_after_a_rebuilt_candidate_still_starts_from_the_seed_bytes(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    before = record_of(out)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    assert record_of(out)["seed_frozen"] == before["seed_frozen"]
    session = open_session(out, Recorder())
    try:
        assert session.seed.frozen["scripts/beta.pyz"] == world.pyz("beta")
        assert session.seed.identity == before["seed_hash"]
    finally:
        session.provider.close()


def test_a_frozen_file_that_changes_on_disk_mid_run_does_not_leak_into_later_candidates(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "data/blob.bin").write_bytes(b"\xff\x00original")
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    _ = (world.skill / "data/blob.bin").write_bytes(b"\xff\x00tampered")
    _ = (world.skill / "scripts/alpha.pyz").write_bytes(b"evil")
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    seen = recorder.seen[0]
    assert seen.frozen["data/blob.bin"] == b"\xff\x00original" and seen.frozen["scripts/alpha.pyz"] != b"evil"


def test_a_deleted_on_disk_pyz_does_not_block_a_resume(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    """The frozen seed bytes live in the run directory, so a resume reads no built `.pyz` from the skill."""
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    (world.skill / "scripts/alpha.pyz").unlink()
    (world.skill / "scripts/beta.pyz").unlink()
    session = open_session(out, Recorder())
    try:
        assert session.rebuilder is not None
        assert set(session.seed.frozen) == {"scripts/alpha.pyz", "scripts/beta.pyz"}
    finally:
        session.provider.close()


def test_resume_a_new_source_file_added_on_disk_after_prepare_does_not_enter_the_build(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    """The rebuilt archive holds the seed sources and the candidate edits, never a file added after prepare."""
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    world.put("beta/leak.py", "LEAK = 'not in the candidate'\n")
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    names = ZipFile(io.BytesIO(recorder.seen[0].frozen["scripts/beta.pyz"])).namelist()
    assert not any(name.endswith("leak.py") for name in names)


def test_resume_an_edited_on_disk_source_does_not_change_the_build_of_untouched_files(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    """The rebuild uses the seed text of an untouched source, not the live file."""
    world.put("beta/helper.py", "VALUE = 1\n")
    world.build()
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    world.put("beta/helper.py", "VALUE = 999\n")
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda c: c.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    assert "VALUE = 1\n" in archive_text(recorder.seen[0].frozen["scripts/beta.pyz"], "beta/helper.py")


def test_a_removed_target_in_wedge_toml_stops_a_resume_with_site_layer_drift(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    config = world.skill / "wedge.toml"
    _ = config.write_text(WEDGE_TOML.split("[[target]]\nname = \"beta\"")[0])
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "site-layer-drift"


def test_a_tampered_layer_key_in_the_record_stops_a_resume(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    record = record_of(out)
    owned = cast(dict[str, object], record["own_targets"])
    owned["layers"] = ["0" * 64]
    write(out / "run.json", record)
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "site-layer-drift"


def test_a_malformed_layer_list_stops_a_resume_as_tampered(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    record = record_of(out)
    cast(dict[str, object], record["own_targets"])["layers"] = [1, 2]
    write(out / "run.json", record)
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "run-record-tampered"


@pytest.mark.parametrize("digest", ["../../etc/passwd", "", "A" * 64, "g" * 64, 5])
def test_a_bad_frozen_digest_in_the_record_stops_a_resume_as_tampered(
        digest: object, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    record = record_of(out)
    cast(dict[str, object], record["seed_frozen"])["scripts/alpha.pyz"] = digest
    write(out / "run.json", record)
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "run-record-tampered"


def test_a_frozen_name_in_the_record_that_escapes_the_skill_is_refused_on_resume(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    record = record_of(out)
    frozen = cast(dict[str, str], record["seed_frozen"])
    frozen["../escape.bin"] = frozen["scripts/alpha.pyz"]
    write(out / "run.json", record)
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "run-record-tampered"


def test_prose_mode_never_stages_sources_or_layers(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    world.mention("Run scripts/alpha.pyz and scripts/beta.pyz.")
    _ = world.contract("scripts/beta.pyz")
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose")
    assert plan.editable == () and plan.sources == {} and set(plan.frozen) == {"scripts/alpha.pyz", "scripts/beta.pyz"}
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    record = record_of(out)
    assert not any(name.startswith("@wedge/") for name in cast(dict[str, str], record["seed"]))
    assert cast(dict[str, object], record["own_targets"])["editable"] == {} and not (out / "site").exists()


def test_a_skill_without_wedge_toml_has_no_own_targets_and_no_rebuilder(
        tmp_path: Path, make_target: Callable[..., Path], write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    skill = make_target(tmp_path / "plain")
    out = tmp_path / "run"
    _ = write_draft(out)
    approved_hash, calls = approvals(skill, out, edit="prose")
    with pytest.raises(Stop) as stopped:
        _ = run(skill, out, MODEL, edit="prose", approve_cases=approved_hash, approve_budget=calls)
    assert stopped.value.code == "live-required"
    session = open_session(out, Recorder())
    try:
        assert session.rebuilder is None and session.seed.frozen == {}
    finally:
        session.provider.close()


# --- AC-9: export -------------------------------------------------------------------------------------------------

def test_export_of_a_winner_equal_to_the_seed_writes_an_empty_skill_patch_and_no_wedge_patch(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    set_winner(out, {})
    destination = tmp_path / "export"
    _ = export(out, destination)
    assert (destination / "candidate.patch").read_text() == "" and not (destination / "wedge-sources.patch").exists()


def test_export_with_only_a_skill_change_writes_no_wedge_patch(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    seed = cast(dict[str, str], record_of(out)["seed"])
    set_winner(out, {"SKILL.md": seed["SKILL.md"] + "\nmore\n"})
    destination = tmp_path / "export"
    _ = export(out, destination)
    assert "+more" in (destination / "candidate.patch").read_text() and not (destination / "wedge-sources.patch").exists()


@pytest.mark.parametrize("tail", ["", "# no newline", "\n\n", "x = 1\r\ny = 2\r\n"])
def test_git_apply_of_the_wedge_patch_reproduces_the_winner_source(
        tail: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    winner = BETA_SOURCE.replace("v1", "v2") + tail
    set_winner(out, {BETA: winner})
    destination = tmp_path / "export"
    _ = export(out, destination)
    applied = git_apply(destination / "wedge-sources.patch", world.repo)
    assert applied.returncode == 0, applied.stderr
    assert (world.proj / "beta/__init__.py").read_bytes() == winner.encode()


@pytest.mark.parametrize("separator", ["\x0c", "\r", "\x0b", "\x1c", "\x85", chr(0x2028), chr(0x2029)])
def test_export_wedge_patch_survives_line_separators_that_splitlines_splits_on(
        separator: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """The patch splits lines on line feed only, so `git apply` reproduces the winner byte for byte."""
    world.put("beta/__init__.py", BETA_SOURCE + f"# part one{separator}part two\n" + "tail = 1\n")
    world.build()
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    seed = cast(dict[str, str], record_of(out)["seed"])[BETA]
    winner = seed.replace("tail = 1", "tail = 2")
    set_winner(out, {BETA: winner})
    destination = tmp_path / "export"
    _ = export(out, destination)
    applied = git_apply(destination / "wedge-sources.patch", world.repo)
    assert applied.returncode == 0, applied.stderr
    assert (world.proj / "beta/__init__.py").read_bytes() == winner.encode()


def test_export_of_a_new_wedge_file_creates_it_and_a_deleted_one_removes_it(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    set_winner(out, {"@wedge/proj/beta/extra.py": "EXTRA = 1\n"}, drop=(ALPHA,))
    destination = tmp_path / "export"
    _ = export(out, destination)
    patch = (destination / "wedge-sources.patch").read_text()
    assert "--- /dev/null" in patch and "+++ /dev/null" in patch
    applied = git_apply(destination / "wedge-sources.patch", world.repo)
    assert applied.returncode == 0, applied.stderr
    assert (world.proj / "beta/extra.py").read_text() == "EXTRA = 1\n" and not (world.proj / "alpha.py").exists()


def test_export_keeps_an_added_empty_wedge_file(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    """An added empty wedge file appears in the patch, and `git apply` creates it."""
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    set_winner(out, {"@wedge/proj/beta/empty.py": ""})
    destination = tmp_path / "export"
    _ = export(out, destination)
    patch = destination / "wedge-sources.patch"
    assert patch.exists()
    applied = git_apply(patch, world.repo)
    assert applied.returncode == 0, applied.stderr
    assert (world.proj / "beta/empty.py").exists()


@pytest.mark.parametrize("bad", [
    "@wedge/../x.py", "@wedge/proj/../../x.py", "@wedge//etc/passwd", "@wedge/proj\\beta\\x.py", "@wedge/.git/config",
    "@wedge/proj/.hidden/x.py", "@wedge/", "@wedge/proj/beta/", "@wedge/proj/./beta/x.py", "@wedge/proj//beta/x.py",
    "@wedge/proj/beta/x\x00.py"])
def test_export_refuses_a_malformed_wedge_path_with_a_named_error_and_writes_nothing(
        bad: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    """A malformed `@wedge/` name raises `export-path-escapes` and writes nothing."""
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    set_winner(out, {bad: "x = 1\n"})
    destination = tmp_path / "export"
    with pytest.raises(CodedError) as caught:
        _ = export(out, destination)
    assert caught.value.code == "export-path-escapes" and not destination.exists()


def test_export_refuses_a_symlink_that_leaves_the_repo_even_when_the_name_is_clean(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    outside = tmp_path / "outside"
    outside.mkdir()
    (world.repo / "escape").symlink_to(outside)
    set_winner(out, {"@wedge/escape/x.py": "x = 1\n"})
    destination = tmp_path / "export"
    with pytest.raises(CodedError) as caught:
        _ = export(out, destination)
    assert caught.value.code == "export-path-escapes" and not destination.exists()


def test_export_names_a_coded_error_when_one_good_and_one_bad_path_mix(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    """A bad wedge name raises `export-path-escapes` even next to a good one, and no export directory appears."""
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    set_winner(out, {BETA: BETA_SOURCE.replace("v1", "v2"), "@wedge/../x.py": "x = 1\n"})
    destination = tmp_path / "export"
    with pytest.raises(CodedError) as caught:
        _ = export(out, destination)
    assert caught.value.code == "export-path-escapes"
    assert not destination.exists()
