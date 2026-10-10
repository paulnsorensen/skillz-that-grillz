"""A skill's own wedge targets: frozen bytes, editable sources, host rebuild, resume, and export (C2).

The builds run offline: a fake closure replaces the network step, while shiv and the archive code run for real.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import io
import json
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final
from zipfile import ZipFile

import pytest

import wedge._build as wedge_build
from wedge._build import build_many

from skillz_experiments import _workflow
from skillz_experiments._candidate import NEW_CLI, FROZEN_FILE_LIMIT, FROZEN_TOTAL_LIMIT, PACKAGE_LIMIT, Candidate, make_workspace
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import Contract, load_contract
from skillz_experiments._cases import repo_root
from skillz_experiments._gate import DEFAULT_STATISTICS
from skillz_experiments._graders import Sandbox
from skillz_experiments._harness import Configuration
from skillz_experiments._records import read, write
from skillz_experiments._runtime import Budget
from skillz_experiments._search import Edit
from skillz_experiments._wedge_targets import WEDGE_EDIT_LIMIT, plan_targets
from skillz_experiments._workflow import Stop, export, run

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"
FAKE_CLAUDE = Path(__file__).parent / "fixtures/fake_claude.py"
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
        _ = path.write_text(text, encoding="utf-8")

    def build(self) -> None:
        built = self.repo / "built"
        shutil.rmtree(built, ignore_errors=True)
        for outcome in build_many([self.skill], built):
            assert outcome.error is None and outcome.value is not None, outcome.error
            _ = shutil.copy2(outcome.value.path, self.skill / "scripts" / f"{outcome.value.name}.pyz")

    def contract(self, helper: str | None = None) -> Contract:
        """Write the contract with `helper` as its helper path, then load it."""
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
    """A provider that scores 0 and keeps every candidate that it evaluates."""

    def __init__(self) -> None:
        self.seen: list[Candidate] = []

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> _Provider:
        return _Provider(self, budget, checkpoint)


@final
class _Provider:
    def __init__(self, recorder: Recorder, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.recorder, self.budget, self.checkpoint = recorder, budget, checkpoint

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


def open_session(out: Path, recorder: Recorder) -> _workflow._Session:
    return _workflow._Session(out, MODEL, "claude", recorder.factory, None, statistics=DEFAULT_STATISTICS)


def evaluate(out: Path, recorder: Recorder, change: Callable[[dict[str, str]], None]) -> tuple[float, dict[str, object]]:
    """Evaluate one search case on a candidate whose components `change` edits."""
    session = open_session(out, recorder)
    try:
        components = {name: session.seed.files[name] for name in session.seed.editable}
        change(components)
        return session._evaluate_example(components, next(iter(session._search_cases)))
    finally:
        session.provider.close()


def archive_text(data: bytes, suffix: str) -> str:
    with ZipFile(io.BytesIO(data)) as archive:
        name = next(name for name in archive.namelist() if name.endswith(suffix))
        return archive.read(name).decode()


def record_of(out: Path) -> dict[str, object]:
    return read(out / "run.json")


# --- AC-1: foreign and non-text files stay frozen ------------------------------------------------------------

def test_foreign_pyz_non_utf8_and_large_text_are_frozen_and_never_editable(world: World) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "scripts/foreign.pyz").write_bytes(b"\xff\xfe" * 800_000)
    _ = (world.skill / "data/blob.bin").write_bytes(b"\xff\x00\x80")
    _ = (world.skill / "data/big.txt").write_text("x" * 262_145)
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    assert {"scripts/foreign.pyz", "data/blob.bin", "data/big.txt", "scripts/alpha.pyz", "scripts/beta.pyz"} <= skeleton.frozen.keys()
    assert not {"scripts/foreign.pyz", "data/blob.bin", "data/big.txt"} & skeleton.files.keys()
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    names = _workflow._editable(skeleton.files | plan.sources, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert BETA in names
    assert not {"scripts/foreign.pyz", "data/blob.bin", "data/big.txt"} & set(names)


def test_frozen_bytes_do_not_count_against_the_package_limit() -> None:
    assert Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.pyz": bytes(PACKAGE_LIMIT + 500_000)}).frozen
    with pytest.raises(ValueError, match="size limit"):
        _ = Candidate({"SKILL.md": "x" * (PACKAGE_LIMIT + 1)}, ("SKILL.md",))


def test_edit_prose_runs_with_a_non_utf8_file_and_records_the_frozen_bytes(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "data/blob.bin").write_bytes(b"\xff\xfe\x00")
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    record = record_of(out)
    frozen = cast(dict[str, str], record["seed_frozen"])
    assert {"data/blob.bin", "scripts/alpha.pyz", "scripts/beta.pyz"} <= frozen.keys()
    assert all((out / "frozen" / digest).is_file() for digest in frozen.values())
    editable = cast(list[str], record["editable"])
    assert not any(name.startswith("@wedge/") or name.endswith(".pyz") for name in editable)


# --- AC-2: frozen limits and identity --------------------------------------------------------------------------

def test_a_frozen_file_over_the_per_file_limit_stops_and_names_the_file(world: World) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "data/huge.bin").write_bytes(bytes(FROZEN_FILE_LIMIT + 1))
    with pytest.raises(CodedError) as caught:
        _ = Candidate.capture(world.skill, [], world.contract())
    assert caught.value.code == "frozen-file-too-large" and "data/huge.bin" in str(caught.value)


def test_frozen_files_over_the_total_limit_stop_and_name_the_largest() -> None:
    blob = bytes(FROZEN_FILE_LIMIT)
    frozen = {f"f{index}.bin": blob for index in range(FROZEN_TOTAL_LIMIT // FROZEN_FILE_LIMIT)}
    assert Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen=frozen)
    with pytest.raises(CodedError) as caught:
        _ = Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen=frozen | {"extra.bin": b"1"})
    assert caught.value.code == "frozen-file-too-large" and "f0.bin" in str(caught.value)


def test_a_frozen_byte_change_changes_the_identity_but_not_the_text_total() -> None:
    first = Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.pyz": b"1"})
    second = first.with_frozen({"a.pyz": b"2"})
    assert first.files == second.files and first.identity != second.identity
    assert first.identity == Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.pyz": b"1"}).identity


# --- AC-5: editable order and limits -----------------------------------------------------------------------------

def test_the_helper_target_comes_first_then_the_targets_that_skill_md_names(world: World) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["beta", "alpha"]
    assert list(plan.sources) == [BETA, ALPHA]


def test_a_target_that_skill_md_does_not_name_stays_frozen(world: World) -> None:
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["beta"]
    assert set(plan.frozen) == {"scripts/alpha.pyz", "scripts/beta.pyz"}


def test_a_package_limit_overflow_leaves_the_later_targets_frozen(world: World) -> None:
    world.mention("Run scripts/beta.pyz too.")
    world.put("alpha.py", "# " + "a" * 40_000 + "\n" + ALPHA_SOURCE)
    world.put("beta/__init__.py", "# " + "b" * 40_000 + "\n" + BETA_SOURCE)
    world.build()
    contract = world.contract("scripts/alpha.pyz")
    remaining = PACKAGE_LIMIT - 60_000 - sum(len(text) for text in Candidate.capture(world.skill, [], contract).files.values())
    (world.skill / "references").mkdir(exist_ok=True)
    for index in range(remaining // 250_000 + 1):
        size = min(250_000, remaining - index * 250_000)
        _ = (world.skill / f"references/pad{index}.md").write_text("p" * size)
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["alpha"]
    assert {target.name for target in plan.targets} == {"alpha", "beta"} and "scripts/beta.pyz" in plan.frozen
    assert Candidate(skeleton.files | plan.sources, ("SKILL.md",), contract, frozen=plan.frozen)


def test_pyz_wedge_toml_and_uv_lock_are_never_editable(world: World) -> None:
    world.put("beta/uv.lock", "inner lock\n")
    world.put("beta/wedge.toml", "inner = true\n")
    world.put("beta/data.pyz", "text that looks editable\n")
    world.build()
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert list(plan.sources) == [BETA]
    names = _workflow._editable(skeleton.files | plan.sources, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert not any(name.endswith(("wedge.toml", "uv.lock", ".pyz")) for name in names) and BETA in names
    for name in ("wedge.toml", "uv.lock", "scripts/beta.pyz"):
        _ = (world.skill / "evals/autoimprove.json").write_text(json.dumps({
            "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
            "kinds": {"echo": {"grader": "exact-json"}}, "editable": ["SKILL.md", name]}))
        with pytest.raises(CodedError) as caught:
            _ = _workflow._editable(skeleton.files, load_contract(world.skill), "prose+cli", skeleton.frozen.keys(), plan)
        assert caught.value.code == "editable-build-file"


def test_a_helper_that_names_an_own_pyz_is_not_a_missing_file(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    editable = cast(list[str], record_of(prepared(world, tmp_path, write_draft, approvals))["editable"])
    assert BETA in editable and not any(name.endswith(".pyz") for name in editable)
    contract = world.contract("scripts/nothing.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    with pytest.raises(CodedError) as caught:
        _ = _workflow._editable(skeleton.files, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert caught.value.code == "helper-file-missing"


def test_a_project_outside_the_repo_root_keeps_its_targets_frozen(world: World) -> None:
    shutil.rmtree(world.repo / ".git")
    (world.skill / ".git").mkdir()
    contract = world.contract("scripts/beta.pyz")
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert plan.editable == () and set(plan.frozen) == {"scripts/alpha.pyz", "scripts/beta.pyz"}


def test_a_missing_pyz_or_a_broken_wedge_toml_stops_with_a_code(world: World) -> None:
    contract = world.contract()
    skeleton = Candidate.capture(world.skill, [], contract)
    (world.skill / "scripts/beta.pyz").unlink()
    with pytest.raises(CodedError) as missing:
        _ = plan_targets(world.skill, skeleton.files, contract, "prose")
    assert missing.value.code == "target-not-built" and "scripts/beta.pyz" in str(missing.value)
    _ = (world.skill / "wedge.toml").write_text("name = [\n")
    with pytest.raises(CodedError) as broken:
        _ = plan_targets(world.skill, skeleton.files, contract, "prose")
    assert broken.value.code == "wedge-config-invalid"


# --- AC-6 to AC-8: host rebuild --------------------------------------------------------------------------------

def test_a_changed_source_builds_on_the_host_before_the_candidate_check(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    world.mention("Run scripts/alpha.pyz for the cheese.")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda components: components.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    _ = evaluate(out, recorder, lambda components: None)
    changed, unchanged = recorder.seen
    assert changed.frozen["scripts/beta.pyz"] != world.pyz("beta")
    assert 'print("beta v2")' in archive_text(changed.frozen["scripts/beta.pyz"], "beta/__init__.py")
    assert changed.frozen["scripts/alpha.pyz"] == world.pyz("alpha")
    assert unchanged.frozen == {"scripts/alpha.pyz": world.pyz("alpha"), "scripts/beta.pyz": world.pyz("beta")}
    assert changed.identity != unchanged.identity


def _sandbox_works() -> bool:
    tool = shutil.which("bwrap")
    if tool is None or not Path("/usr/bin/python3").exists():
        return False
    probe = subprocess.run([tool, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                            "/usr/bin/python3", "-c", "pass"], capture_output=True, check=False)
    return probe.returncode == 0


@pytest.mark.skipif(not _sandbox_works(), reason="bubblewrap is unavailable")
def test_the_rebuilt_pyz_runs_in_the_production_sandbox(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda components: components.update({BETA: BETA_SOURCE.replace("v1", "v2")}))
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(FAKE_CLAUDE)]}))
    session = Configuration.load(config, MODEL).create(MODEL, Budget(10, 120, 0), lambda: None)
    workspace = make_workspace(tmp_path / "workspace")
    root = workspace / ".agents/skills/echo-skill"
    root.mkdir(parents=True, exist_ok=True)
    recorder.seen[0].materialize(root)
    try:
        transport = cast(Sandbox, cast(object, session.transports["task"]))
        code, stdout, _ = transport.sandbox(workspace, ["/usr/bin/python3", "-I", ".agents/skills/echo-skill/scripts/beta.pyz"])
    finally:
        session.close()
    assert (code, stdout.strip()) == (0, "beta v2")

REAL_SKILL = Path(__file__).resolve().parents[3] / "skills/skillz"


def test_the_real_skill_plans_the_inspector_as_the_only_editable_target() -> None:
    contract = load_contract(REAL_SKILL)
    assert contract.helper is not None and contract.helper.path == "scripts/inspect-skill.pyz"
    skeleton = Candidate.capture(REAL_SKILL, [], contract)
    plan = plan_targets(REAL_SKILL, skeleton.files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["inspect-skill"]
    assert {name.rsplit("/", 1)[-1] for name in plan.sources} >= {"__init__.py", "_cli.py", "_inspect.py"}
    assert all(name.startswith("@wedge/lib/src/skillz_inspect/") for name in plan.sources)
    assert sum(len(text) for text in plan.sources.values()) <= WEDGE_EDIT_LIMIT
    fixed = {name for name in plan.frozen if name.startswith("@wedge/lib/fromargs/")}
    assert fixed and not fixed & set(plan.sources)
    assert {"scripts/skillz-experiment.pyz", "scripts/inspect-skill.pyz"} <= set(plan.frozen)
    assert "skillz-experiment" in plan.reasons
    assert plan.capable is not None
    names = _workflow._editable(skeleton.files | plan.sources | {NEW_CLI: ""}, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert set(plan.sources) | {NEW_CLI} <= set(names)
    assert not any(name.startswith(("@wedge/lib/fromargs/", "@wedge/lib/src/skillz_experiments/")) for name in names)



def test_a_broken_build_rejects_the_candidate_with_build_failed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["shiv"], stderr="shiv exploded")

    monkeypatch.setattr(wedge_build, "_shiv", broken)
    recorder = Recorder()
    score, feedback = evaluate(out, recorder, lambda components: components.update({BETA: BETA_SOURCE + "\n# edit\n"}))
    assert score == 0.0 and recorder.seen == []
    assert feedback["reason"] == "build-failed" and str(feedback["rejected"]).startswith("target beta: ")
    assert "shiv exploded" in str(feedback["rejected"])


def test_candidate_source_never_runs_on_the_host_during_the_build(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    marker = tmp_path / "host-marker"
    recorder = Recorder()
    source = BETA_SOURCE + f"\nopen({str(marker)!r}, 'w').write('ran')\n"
    _ = evaluate(out, recorder, lambda components: components.update({BETA: source}))
    assert not marker.exists()
    assert "host-marker" in archive_text(recorder.seen[0].frozen["scripts/beta.pyz"], "beta/__init__.py")


# --- resume ---------------------------------------------------------------------------------------------------

def test_a_resume_reopens_the_recorded_site_layers(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    keys = cast(dict[str, list[str]], record_of(out)["own_targets"])["layers"]
    session = open_session(out, Recorder())
    try:
        assert session.rebuilder is not None
        assert [layer.key for layer in session.rebuilder.layers.values()] == keys and len(keys) == 1
    finally:
        session.provider.close()


def test_a_prose_run_opens_no_rebuilder(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    session = open_session(out, Recorder())
    try:
        assert session.rebuilder is None and not (out / "site").exists()
    finally:
        session.provider.close()


@pytest.mark.parametrize("drift", ["uv.lock", "wedge.toml"])
def test_a_changed_lock_or_config_stops_a_resume_with_site_layer_drift(
        drift: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    path = world.proj / "uv.lock" if drift == "uv.lock" else world.skill / "wedge.toml"
    _ = path.write_text(path.read_text() + "\n# drift\n")
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "site-layer-drift"


@pytest.mark.parametrize("tamper", ["change", "delete"])
def test_a_tampered_frozen_blob_stops_a_resume(
        tamper: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    blob = out / "frozen" / cast(dict[str, str], record_of(out)["seed_frozen"])["scripts/alpha.pyz"]
    if tamper == "change":
        _ = blob.write_bytes(b"evil")
    else:
        blob.unlink()
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "run-record-tampered"


@pytest.mark.parametrize("names", ['scripts/none.py', 7, None])
def test_a_recorded_editable_name_that_is_not_a_seed_file_stops_a_resume(
        names: object, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    record = record_of(out)
    owned = cast(dict[str, object], record["own_targets"])
    cast(dict[str, object], owned["editable"])["beta"] = names if not isinstance(names, str) else [names]
    write(out / "run.json", record)
    with pytest.raises(Stop) as stopped:
        _ = open_session(out, Recorder())
    assert stopped.value.code == "run-record-tampered"


# --- AC-9: export ---------------------------------------------------------------------------------------------

def _completed(out: Path, winner_extra: dict[str, str], *, drop: tuple[str, ...] = ()) -> None:
    record = record_of(out)
    seed = cast(dict[str, str], record["seed"])
    winner = {name: text for name, text in seed.items() if name not in drop} | winner_extra
    record["phase"] = "complete"
    record["winner"] = winner
    write(out / "run.json", record)


def _exported(world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
              approvals: Callable[..., tuple[str, int]]) -> Path:
    """Export a winner that edits a wedge source and a skill file, adds a skill file, and deletes another."""
    (world.skill / "references").mkdir(exist_ok=True)
    _ = (world.skill / "references/old.md").write_text("old\n")
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    _completed(out, {BETA: BETA_SOURCE.replace("v1", "v2"), "references/added.md": "new file\n"}, drop=("references/old.md",))
    destination = tmp_path / "export"
    _ = export(out, destination)
    return destination


def test_export_patches_apply_for_real_to_a_skill_dir_in_a_repo_and_to_the_repo_root(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    destination = _exported(world, tmp_path, write_draft, approvals)
    wedge_patch = destination / "wedge-sources.patch"
    assert "--- a/proj/beta/__init__.py" in wedge_patch.read_text() and "@wedge" not in wedge_patch.read_text()
    assert wedge_patch.stat().st_mode & 0o077 == 0
    skill_patch = (destination / "candidate.patch").read_text()
    assert "diff --git" not in skill_patch and "--- /dev/null" in skill_patch and "+++ /dev/null" in skill_patch
    assert "proj/beta" not in skill_patch
    for patch, directory in ((wedge_patch, world.repo), (destination / "candidate.patch", world.skill)):
        applied = subprocess.run(["git", "apply", str(patch)], cwd=directory, capture_output=True, text=True)
        assert applied.returncode == 0, applied.stderr
    assert (world.skill / "references/added.md").read_text() == "new file\n"
    assert not (world.skill / "references/old.md").exists()
    assert 'print("beta v2")' in (world.proj / "beta/__init__.py").read_text()


@pytest.mark.skipif(shutil.which("patch") is None, reason="patch is unavailable")
def test_candidate_patch_applies_with_patch_p1_outside_a_repo(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    destination = _exported(world, tmp_path, write_draft, approvals)
    plain = tmp_path / "plain"
    _ = shutil.copytree(world.skill, plain)
    applied = subprocess.run(["patch", "-p1", "-i", str(destination / "candidate.patch")], cwd=plain,
                             capture_output=True, text=True)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert (plain / "references/added.md").read_text() == "new file\n"
    assert not (plain / "references/old.md").exists()


def test_export_without_a_wedge_change_writes_no_wedge_patch(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    _completed(out, {"references/added.md": "new file\n"})
    _ = export(out, tmp_path / "export")
    assert not (tmp_path / "export/wedge-sources.patch").exists()


@pytest.mark.parametrize("escape", ["symlink", "no-repo"])
def test_export_refuses_a_wedge_path_outside_the_repo_root(
        escape: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    if escape == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (world.proj / "link").symlink_to(outside)
        _completed(out, {"@wedge/proj/link/x.py": "x = 1\n"})
    else:
        shutil.rmtree(world.repo / ".git")
        if repo_root(world.repo) is not None:
            pytest.skip("an ancestor of tmp_path holds .git, so the no-repo case cannot run on this host")
        _completed(out, {BETA: BETA_SOURCE.replace("v1", "v2")})
    destination = tmp_path / "export"
    with pytest.raises(CodedError) as caught:
        _ = export(out, destination)
    assert caught.value.code == "export-path-escapes" and not destination.exists()


def test_a_skill_only_change_exports_outside_a_repo(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    shutil.rmtree(world.repo / ".git")
    _completed(out, {"references/added.md": "new file\n"})
    _ = export(out, tmp_path / "export")
    assert (tmp_path / "export/candidate.patch").is_file()
    assert not (tmp_path / "export/wedge-sources.patch").exists()


# --- cure pass A: seed bytes, shared sources, caps, reserved names, and the gate -------------------------------

def test_prepare_builds_the_seed_pyz_from_the_seed_sources_not_from_a_stale_disk_file(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    stale = world.pyz("beta")
    world.put("beta/__init__.py", BETA_SOURCE.replace("v1", "v2"))
    out = prepared(world, tmp_path, write_draft, approvals)
    frozen = cast(dict[str, str], record_of(out)["seed_frozen"])
    beta = (out / "frozen" / frozen["scripts/beta.pyz"]).read_bytes()
    assert 'print("beta v2")' in archive_text(beta, "beta/__init__.py") and beta != stale
    assert (out / "frozen" / frozen["scripts/alpha.pyz"]).read_bytes() == world.pyz("alpha")
    recorder = Recorder()
    _ = evaluate(out, recorder, lambda components: None)
    assert recorder.seen[0].frozen["scripts/beta.pyz"] == beta


def _editable_targets(world: World, helper: str | None) -> list[str]:
    contract = world.contract(helper)
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    return [target.name for target in plan.editable]


def test_a_source_that_a_nested_skill_target_builds_from_is_never_editable(world: World) -> None:
    nested = world.skill / "sub"
    nested.mkdir()
    _ = (nested / "wedge.toml").write_text(
        'project = "../../proj"\nrepo = "o/n"\n\n[[target]]\nname = "alpha"\nentry = "alpha:main"\nsource = "../../proj/alpha.py"\n')
    assert _editable_targets(world, "scripts/beta.pyz") == ["beta"]
    assert _editable_targets(world, "scripts/alpha.pyz") == []


def test_a_source_that_a_frozen_own_target_builds_from_is_never_editable(world: World) -> None:
    _ = (world.skill / "wedge.toml").write_text(
        WEDGE_TOML + '\n[[target]]\nname = "whole"\nentry = "alpha:main"\nsource = "../proj"\n')
    world.build()
    assert _editable_targets(world, "scripts/beta.pyz") == []


def test_the_wedge_source_cap_is_100000_characters(world: World) -> None:
    assert WEDGE_EDIT_LIMIT == 100_000
    for size, expected in ((100_000, ["beta"]), (100_001, [])):
        world.put("beta/__init__.py", "#" * (size - 1) + "\n")
        world.build()
        assert _editable_targets(world, "scripts/beta.pyz") == expected, size


def test_a_contract_editable_entry_that_is_a_frozen_file_stops(world: World) -> None:
    (world.skill / "data").mkdir()
    _ = (world.skill / "data/blob.bin").write_bytes(b"\xff\x00\x80")
    skeleton = Candidate.capture(world.skill, [], world.contract("scripts/beta.pyz"))
    _ = (world.skill / "evals/autoimprove.json").write_text(json.dumps({
        "schema_version": 1, "status": "approved", "skill": "echo-skill", "invocation": "$echo-skill run",
        "kinds": {"echo": {"grader": "exact-json"}}, "editable": ["SKILL.md", "data/blob.bin"]}))
    contract = load_contract(world.skill)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    with pytest.raises(CodedError) as caught:
        _ = _workflow._editable(skeleton.files, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert caught.value.code == "editable-frozen"


def test_a_skill_file_under_the_wedge_prefix_is_a_reserved_name(world: World) -> None:
    (world.skill / "@wedge").mkdir()
    _ = (world.skill / "@wedge/x.md").write_text("x\n")
    with pytest.raises(CodedError) as caught:
        _ = Candidate.capture(world.skill, [], world.contract())
    assert caught.value.code == "reserved-name"


def test_a_git_ignored_first_party_file_keeps_its_target_frozen(world: World) -> None:
    _ = (world.repo / ".gitignore").write_text("proj/beta/secret.py\n")
    world.put("beta/secret.py", "SECRET = 1\n")
    world.mention("Run scripts/alpha.pyz for the cheese.")
    assert _editable_targets(world, "scripts/beta.pyz") == ["alpha"]


def test_a_rebuild_stages_first_party_files_that_no_proposal_edits_from_the_seed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    world.put("beta/data.pyz", "payload\n")
    world.put("beta/uv.lock", "inner lock\n")
    world.build()
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    changed = BETA_SOURCE.replace("v1", "v2")
    _ = evaluate(out, recorder, lambda components: components.update({BETA: changed}))
    rebuilt = recorder.seen[0].frozen["scripts/beta.pyz"]
    assert archive_text(rebuilt, "beta/data.pyz") == "payload\n"
    world.put("beta/__init__.py", changed)
    world.build()
    assert rebuilt == world.pyz("beta")


def _gated(out: Path, recorder: Recorder, change: dict[str, str]) -> _workflow._Session:
    """Open a session whose recorded winner is the seed with `change` applied."""
    session = open_session(out, recorder)
    seed = cast(dict[str, str], session.record["seed"])
    session.record["winner"] = seed | change
    return session


def test_the_gate_scores_the_winner_with_rebuilt_bytes_and_the_baseline_with_seed_bytes(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    session = _gated(out, recorder, {BETA: BETA_SOURCE.replace("v1", "v2")})
    try:
        session.gate()
    finally:
        session.provider.close()
    sealed = cast(dict[str, str], record_of(out)["seed_frozen"])["scripts/beta.pyz"]
    seed_bytes = (out / "frozen" / sealed).read_bytes()
    betas = [candidate.frozen["scripts/beta.pyz"] for candidate in recorder.seen]
    winner = [data for data in betas if data != seed_bytes]
    holdout = len(session.cases_for("holdout"))
    repeats = cast(int, session.record["repeats"])
    assert betas.count(seed_bytes) == holdout * repeats and len(winner) == holdout * repeats
    assert all('print("beta v2")' in archive_text(data, "beta/__init__.py") for data in winner)


def test_a_winner_that_no_longer_builds_stops_the_gate_with_build_failed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    session = _gated(out, recorder, {BETA: BETA_SOURCE.replace("v1", "v2")})

    def broken(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["shiv"], stderr="shiv exploded")

    monkeypatch.setattr(wedge_build, "_shiv", broken)
    try:
        with pytest.raises(Stop) as stopped:
            session.gate()
    finally:
        session.provider.close()
    assert stopped.value.code == "build-failed" and "next" in stopped.value.data
    assert recorder.seen == []



def test_a_helper_that_shares_a_source_with_an_oversized_named_target_keeps_its_own_names(world: World) -> None:
    shared = "@wedge/proj/shared.py"
    alpha = WEDGE_TOML.split("\n[[target]]\n")[1]
    big = '\n[[target]]\nname = "big"\nentry = "big:main"\nsource = "../proj/big"\ninclude = ["../proj/shared.py"]\n'
    _ = (world.skill / "wedge.toml").write_text(
        'project = "../proj"\nrepo = "o/n"\n\n[[target]]\n' + alpha + 'include = ["../proj/shared.py"]\n' + big)
    world.put("shared.py", "VALUE = 1\n")
    world.put("big/__init__.py", "def main() -> None:\n    pass\n\n# " + "#" * WEDGE_EDIT_LIMIT + "\n")
    world.build()
    world.mention("Run scripts/big.pyz too.")
    contract = world.contract("scripts/alpha.pyz")
    plan = plan_targets(world.skill, Candidate.capture(world.skill, [], contract).files, contract, "prose+cli")
    assert [target.name for target in plan.editable] == ["alpha"]
    assert sorted(plan.editable[0].sources or {}) == [ALPHA]
    assert shared in plan.editable[0].fixed and shared not in (plan.editable[0].sources or {})

# --- cure pass B -------------------------------------------------------------------------------------------------

GAMMA_TARGET = '\n[[target]]\nname = "gamma"\nentry = "gamma:main"\nsource = "../proj/gamma.py"\n'


def test_a_small_named_target_after_an_oversized_one_stays_frozen(world: World) -> None:
    _ = (world.skill / "wedge.toml").write_text(WEDGE_TOML + GAMMA_TARGET)
    world.put("gamma.py", ALPHA_SOURCE.replace("alpha", "gamma"))
    world.put("beta/__init__.py", "#" * WEDGE_EDIT_LIMIT + "\n")
    world.build()
    world.mention("Run scripts/beta.pyz and scripts/gamma.pyz too.")
    assert _editable_targets(world, "scripts/alpha.pyz") == ["alpha"]


def test_named_targets_keep_the_order_of_wedge_toml(world: World) -> None:
    alpha, beta = WEDGE_TOML.split("\n[[target]]\n")[1:]
    _ = (world.skill / "wedge.toml").write_text('project = "../proj"\nrepo = "o/n"\n\n[[target]]\n' + beta + "\n[[target]]\n" + alpha)
    world.build()
    world.mention("Run scripts/alpha.pyz, then scripts/beta.pyz.")
    assert _editable_targets(world, None) == ["beta", "alpha"]


def test_a_frozen_target_records_why_and_helper_missing_shows_it(world: World) -> None:
    contract = world.contract()
    skeleton = Candidate.capture(world.skill, [], contract)
    plan = plan_targets(world.skill, skeleton.files, contract, "prose+cli")
    assert plan.editable == () and "SKILL.md" in plan.reasons["alpha"]
    assert cast(dict[str, str], plan.record()["frozen"]) == dict(plan.reasons)
    with pytest.raises(CodedError) as caught:
        _ = _workflow._editable(skeleton.files, contract, "prose+cli", skeleton.frozen.keys(), plan)
    assert caught.value.code == "helper-missing" and "alpha is frozen because" in str(caught.value)
    prose = plan_targets(world.skill, skeleton.files, contract, "prose")
    assert "--edit prose" in prose.reasons["beta"]


def test_export_lists_each_written_patch(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    _completed(out, {"references/added.md": "new file\n"})
    assert export(out, tmp_path / "skill-only")["patches"] == ["candidate.patch"]
    _completed(out, {BETA: BETA_SOURCE.replace("v1", "v2")})
    assert export(out, tmp_path / "both")["patches"] == ["candidate.patch", "wedge-sources.patch"]


def test_the_rebuild_cache_keeps_four_results_and_builds_equal_content_once(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    _ = world.contract("scripts/beta.pyz")
    out = prepared(world, tmp_path, write_draft, approvals)
    session = open_session(out, Recorder())
    try:
        rebuilder = session.rebuilder
        assert rebuilder is not None
        runs: list[str] = []
        real = rebuilder._run

        def counted(*args: object, **kwargs: object) -> bytes:
            runs.append("run")
            return real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

        monkeypatch.setattr(rebuilder, "_run", counted)

        def variant(index: int) -> Candidate:
            components = {name: session.seed.files[name] for name in session.seed.editable}
            components[BETA] = BETA_SOURCE + f"# {index}\n"
            return session.seed.changed(components)

        def build(_index: int) -> Candidate:
            return rebuilder.apply(variant(0))

        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(build, range(4)))
        assert len(runs) == 1 and len({result.identity for result in results}) == 1
        for index in range(1, 6):
            _ = rebuilder.apply(variant(index))
        assert len(rebuilder._cache) == 4 and len(runs) == 6
        _ = rebuilder.apply(variant(0))
        assert len(runs) == 7
    finally:
        session.provider.close()


def test_a_changed_candidate_carries_the_parent_frozen_digests() -> None:
    parent = Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.pyz": b"1", "b.pyz": b"2"})
    assert parent.changed({"SKILL.md": "y"}).frozen_digests is parent.frozen_digests
    built = parent.with_frozen({"b.pyz": b"3", "c.pyz": b"4"})
    fresh = Candidate({"SKILL.md": "x"}, ("SKILL.md",), frozen={"a.pyz": b"1", "b.pyz": b"3", "c.pyz": b"4"})
    assert built.frozen_digests == fresh.frozen_digests and built.identity == fresh.identity
