"""The `@new-cli` component: one new fromargs wedge target that a proposal adds to a skill (C4, AC-13, AC-14).

The builds run offline: a fake closure replaces the network step, while shiv and the archive code run for real.
"""
# pyright: reportPrivateUsage=false
from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
from types import SimpleNamespace
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final
from zipfile import ZipFile

import pytest

import wedge._build as wedge_build
from wedge._build import SiteLayer, build_many
from wedge._config import load_targets, parse_targets

from skillz_experiments import _wedge_targets, _workflow
from skillz_experiments._candidate import NEW_CLI, TEXT_FILE_LIMIT, Candidate
from skillz_experiments._cases import Case, CodedError
from skillz_experiments._contract import load_contract
from skillz_experiments._gate import DEFAULT_STATISTICS
from skillz_experiments._intake import intake_contract
from skillz_experiments._new_cli import VALUE_LIMIT, expand_files, unused_helpers
from skillz_experiments._records import read, write
from skillz_experiments._runtime import Budget
from skillz_experiments._search import Edit
from skillz_experiments._wedge_targets import BuildFailed, plan_targets, settle
from skillz_experiments._workflow import Stop, export, run

pytestmark = pytest.mark.usefixtures("host_login")

MODEL = "local-test"
LOCK = 'version = 1\n\n[[package]]\nname = "cyclopts"\nversion = "1"\n'
HELLO = 'def main() -> None:\n    print("hello")\n'


def toml_value(name: str, module: str) -> str:
    return f"name = {json.dumps(name)}\nmodule = {json.dumps(module)}\n"


VALUE = toml_value("hello-cli", HELLO)


@dataclass
class World:
    """A repository whose skill owns one fromargs target. `base` is the project directory."""

    repo: Path
    base: Path
    skill: Path

    def toml(self) -> str:
        return (self.skill / "wedge.toml").read_text()

    def files(self) -> dict[str, str]:
        return Candidate.capture(self.skill, []).files


def make_world(tmp_path: Path, make_target: Callable[..., Path], *, outside: bool = False, include: str = "fromargs",
               lock: str = LOCK) -> World:
    repo = tmp_path / "repo"
    _ = subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = make_target(repo)
    base = repo / "proj" if outside else repo
    prefix = "../proj" if outside else ".."
    for name, text in (("pyproject.toml", '[project]\nname = "p"\nversion = "0"\n'), ("uv.lock", lock),
                       ("alpha/__init__.py", 'def main() -> None:\n    print("alpha")\n'),
                       (f"lib/{include}/__init__.py", "VERSION = 1\n")):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text)
    _ = (skill / "wedge.toml").write_text(
        f'project = "{prefix}"\nrepo = "o/n"\nname = "alpha"\nentry = "alpha:main"\n'
        + f'source = "{prefix}/alpha"\ninclude = ["{prefix}/lib/{include}"]\n')
    (skill / "scripts").mkdir(exist_ok=True)
    built = repo / "built"
    for outcome in build_many([skill], built):
        assert outcome.error is None and outcome.value is not None, outcome.error
        _ = shutil.copy2(outcome.value.path, skill / "scripts" / f"{outcome.value.name}.pyz")
    return World(repo, base, skill)


def fake_layer(monkeypatch: pytest.MonkeyPatch, package: str = "cyclopts", *, grouped_only: bool = False) -> None:
    """Give every site layer (only a layer with dependency groups when `grouped_only`) the package `package`, offline."""
    def closure(_project: Path, groups: Sequence[str]) -> tuple[str, bool]:
        return ("x", True) if groups or not grouped_only else ("", False)

    def install(_requirements: str, site_dir: Path) -> None:
        (site_dir / package).mkdir(parents=True, exist_ok=True)
        _ = (site_dir / package / "__init__.py").write_text("")

    monkeypatch.setattr(wedge_build, "_closure_requirements", closure)
    monkeypatch.setattr(wedge_build, "_install_third_party", install)


def layered_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Callable[..., Path], package: str) -> World:
    """Return a world whose site layer holds the package `package`."""
    def empty(_project: Path, _groups: object) -> tuple[str, bool]:
        return "", False

    monkeypatch.setattr(wedge_build, "_closure_requirements", empty)
    built = make_world(tmp_path, make_target)
    fake_layer(monkeypatch, package)
    return built


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Callable[..., Path]) -> World:
    return layered_world(tmp_path, monkeypatch, make_target, "cyclopts")


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


def prepared(w: World, tmp_path: Path, write_draft: Callable[..., list[str]],
             approvals: Callable[..., tuple[str, int]], *, edit: str = "prose+cli") -> Path:
    out = tmp_path / "run"
    _ = write_draft(out)
    approved_hash, calls = approvals(w.skill, out, edit=edit)
    with pytest.raises(Stop) as stopped:
        _ = run(w.skill, out, MODEL, edit=cast(Edit, edit), approve_cases=approved_hash, approve_budget=calls)
    assert stopped.value.code == "live-required"
    return out


def open_session(out: Path, recorder: Recorder) -> _workflow._Session:
    return _workflow._Session(out, MODEL, "claude", recorder.factory, None, statistics=DEFAULT_STATISTICS)


def evaluate(out: Path, recorder: Recorder, value: str) -> tuple[float, dict[str, object]]:
    session = open_session(out, recorder)
    try:
        components = {name: session.seed.files[name] for name in session.seed.editable}
        components[NEW_CLI] = value
        return session._evaluate_example(components, next(iter(session._search_cases)))
    finally:
        session.provider.close()


def archive_text(data: bytes, suffix: str) -> str:
    with ZipFile(io.BytesIO(data)) as archive:
        name = next(name for name in archive.namelist() if name.endswith(suffix))
        return archive.read(name).decode()


# --- AC-13: the target is offered only when the skill can build it -----------------------------------------

def test_a_skill_with_a_fromargs_include_and_a_cyclopts_lock_is_offered_the_component(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    plan = plan_targets(world.skill, world.files(), load_contract(world.skill), "prose+cli")
    assert plan.capable is not None and plan.capable.site.name == "alpha"
    assert any(name.endswith("lib/fromargs/__init__.py") for name in plan.capable.fixed)
    out = prepared(world, tmp_path, write_draft, approvals)
    assert NEW_CLI in cast(list[str], read(out / "run.json")["editable"])
    assert "new_cli" in cast(dict[str, object], read(out / "run.json")["own_targets"])


def test_a_prose_run_does_not_offer_the_component(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals, edit="prose")
    assert NEW_CLI not in cast(list[str], read(out / "run.json")["editable"])


@pytest.mark.parametrize("case", ["no-wedge-toml", "no-fromargs-include", "no-cyclopts", "skill-outside-project"])
def test_the_component_is_not_offered_without_a_buildable_fromargs_site(
        case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Callable[..., Path]) -> None:
    def empty(_project: Path, _groups: object) -> tuple[str, bool]:
        return "", False

    monkeypatch.setattr(wedge_build, "_closure_requirements", empty)
    click = 'version = 1\n\n[[package]]\nname = "click"\nversion = "1"\n'
    built = make_world(tmp_path, make_target, outside=case == "skill-outside-project",
                       include="other" if case == "no-fromargs-include" else "fromargs",
                       lock=click if case == "no-cyclopts" else LOCK)
    if case == "no-wedge-toml":
        (built.skill / "wedge.toml").unlink()
        shutil.rmtree(built.skill / "scripts")
    plan = plan_targets(built.skill, built.files(), load_contract(built.skill), "prose+cli")
    assert plan.capable is None
    assert "new_cli" not in plan.record()


# --- expansion and the wedge.toml conversion ---------------------------------------------------------------

def test_a_single_target_file_becomes_the_multi_target_form_and_still_loads(world: World) -> None:
    seed = world.files()
    added = expand_files(world.skill, seed, VALUE)
    assert added["src/hello_cli/__init__.py"] == HELLO
    text = added["wedge.toml"]
    assert text.count("[[target]]") == 2
    assert text.startswith('project = ".."\nrepo = "o/n"\n')
    old, new = parse_targets(world.skill, text)
    before = load_targets(world.skill)[0]
    assert (old.name, old.entry, old.source, old.include) == (before.name, before.entry, before.source, before.include)
    assert (new.name, new.entry, new.source, new.include) == (
        "hello-cli", "hello_cli:main", "src/hello_cli", before.include[:1])
    assert expand_files(world.skill, seed, VALUE) == added


def test_the_generated_target_names_no_dependency_group_and_builds_from_the_no_group_layer(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = (world.skill / "wedge.toml").write_text(world.toml() + 'groups = ["extra"]\n')
    assert tuple(load_targets(world.skill)[0].groups) == ("extra",)
    new = parse_targets(world.skill, expand_files(world.skill, world.files(), VALUE)["wedge.toml"])[-1]
    assert new.name == "hello-cli" and tuple(new.groups) == ()
    plan = plan_targets(world.skill, world.files(), load_contract(world.skill), "prose+cli")
    assert plan.capable is not None and plan.layer_ids() == [(world.base.resolve(), ())]
    out = prepared(world, tmp_path, write_draft, approvals)
    assert len(cast(list[str], cast(dict[str, object], read(out / "run.json")["own_targets"])["layers"])) == 1
    recorder = Recorder()
    _, feedback = evaluate(out, recorder, VALUE)
    assert "rejected" not in feedback and "scripts/hello-cli.pyz" in recorder.seen[0].frozen


def test_a_multi_target_file_keeps_its_text_and_gains_one_table(world: World) -> None:
    first = expand_files(world.skill, world.files(), VALUE)
    seed = world.files() | first
    second = expand_files(world.skill, seed, toml_value("bye-cli", HELLO))["wedge.toml"]
    assert second.startswith(first["wedge.toml"].rstrip("\n")) and second.count("[[target]]") == 3
    assert [config.name for config in parse_targets(world.skill, second)] == ["alpha", "hello-cli", "bye-cli"]


def test_a_seed_without_a_fromargs_include_is_unavailable(world: World) -> None:
    seed = world.files()
    seed["wedge.toml"] = seed["wedge.toml"].replace("/lib/fromargs", "/lib/other")
    (world.base / "lib/other").mkdir(parents=True)
    with pytest.raises(CodedError) as raised:
        _ = expand_files(world.skill, seed, VALUE)
    assert raised.value.code == "new-cli-unavailable"


# --- AC-13: the build, the score, and the rejections ---------------------------------------------------------

def test_a_proposal_builds_a_new_target_and_scores_with_its_pyz(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    _, feedback = evaluate(out, recorder, VALUE)
    assert "rejected" not in feedback
    candidate = recorder.seen[0]
    assert archive_text(candidate.frozen["scripts/hello-cli.pyz"], "hello_cli/__init__.py") == HELLO
    assert candidate.files["src/hello_cli/__init__.py"] == HELLO and candidate.files["wedge.toml"].count("[[target]]") == 2
    assert "scripts/alpha.pyz" in candidate.frozen


def test_an_empty_component_adds_no_target(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    _ = evaluate(out, recorder, "")
    assert not [name for name in recorder.seen[0].frozen if name == "scripts/hello-cli.pyz"]
    assert recorder.seen[0].files["wedge.toml"] == world.toml()


_BAD: list[tuple[str, str]] = [
    ("new-cli-malformed", "name = [unclosed"),
    ("new-cli-second-target", '[[target]]\nname = "a"\n'),
    ("new-cli-second-target", 'name = ["a", "b"]\nmodule = "x = 1"\n'),
    ("new-cli-extra-key", toml_value("hello-cli", HELLO) + 'entry = "x:y"\n'),
    ("new-cli-missing-key", 'name = "hello-cli"\n'),
    ("new-cli-bad-name", toml_value("Hello_Cli", HELLO)),
    ("new-cli-module-size", toml_value("hello-cli", "   ")),
    ("new-cli-module-size", toml_value("hello-cli", "x = 1\n" * (TEXT_FILE_LIMIT // 6 + 2))),
    ("new-cli-module-size", "#" * (VALUE_LIMIT + 1)),
    ("new-cli-module-size", toml_value("hello-cli", "#" * (TEXT_FILE_LIMIT + 1))),
    ("new-cli-malformed", "a = " + "[" * 5000 + "]" * 5000),
    ("new-cli-syntax", toml_value("hello-cli", "x = " + "1+" * 30000 + "1\n")),
    ("new-cli-syntax", toml_value("hello-cli", "x = " + "-" * 20000 + "1\n")),
    ("new-cli-name-collision", toml_value("alpha", HELLO)),
    ("new-cli-package-collision", toml_value("json", HELLO)),
    ("new-cli-package-collision", toml_value("fromargs", HELLO)),
    ("new-cli-undeclared-import", toml_value("hello-cli", "import requests\n")),
    ("new-cli-undeclared-import", toml_value("hello-cli", "def main() -> None:\n    from . import sibling\n")),
    ("new-cli-syntax", toml_value("hello-cli", "def (:\n")),
    ("new-cli-no-main", toml_value("hello-cli", "def run() -> None:\n    pass\n")),
    ("new-cli-no-main", toml_value("hello-cli", "class A:\n    def main(self) -> None:\n        pass\n")),
]


@pytest.mark.parametrize(("code", "value"), _BAD, ids=[f"{code}-{index}" for index, (code, _) in enumerate(_BAD)])
def test_a_bad_component_rejects_the_candidate_with_its_code(
        code: str, value: str, world: World, tmp_path: Path, write_draft: Callable[..., list[str]],
        approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    score, feedback = evaluate(out, recorder, value)
    assert score == 0.0 and recorder.seen == []
    assert feedback["reason"] == code and "rejected" in feedback


def test_a_value_of_exactly_the_limit_is_not_a_size_rejection(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    _, feedback = evaluate(out, Recorder(), "#" * VALUE_LIMIT)
    assert feedback["reason"] != "new-cli-module-size"


def test_a_failed_build_of_the_new_target_rejects_with_build_failed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["shiv"], stderr="shiv exploded")

    monkeypatch.setattr(wedge_build, "_shiv", broken)
    score, feedback = evaluate(out, Recorder(), VALUE)
    assert score == 0.0 and feedback["reason"] == "build-failed" and "hello-cli" in str(feedback["rejected"])


def test_the_module_never_runs_on_the_host(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    marker = tmp_path / "host-marker"
    module = f"open({str(marker)!r}, 'w').write('ran')\n" + HELLO
    recorder = Recorder()
    _ = evaluate(out, recorder, toml_value("hello-cli", module))
    assert not marker.exists()
    assert "host-marker" in archive_text(recorder.seen[0].frozen["scripts/hello-cli.pyz"], "hello_cli/__init__.py")


def test_a_resume_reopens_the_rebuilder_of_a_new_target_run(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    session = open_session(out, Recorder())
    session.provider.close()
    assert session.rebuilder is not None and session.rebuilder.capable is not None
    recorder = Recorder()
    _ = evaluate(out, recorder, VALUE)
    assert "scripts/hello-cli.pyz" in recorder.seen[0].frozen


# --- AC-14: export -------------------------------------------------------------------------------------------

def _completed(out: Path, winner_extra: dict[str, str]) -> None:
    record = read(out / "run.json")
    seed = cast(dict[str, str], record["seed"])
    record["phase"] = "complete"
    record["winner"] = seed | winner_extra
    write(out / "run.json", record)


def test_export_carries_the_new_module_and_the_converted_wedge_toml(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    seed_text = cast(dict[str, str], read(out / "run.json")["seed"])["SKILL.md"]
    _completed(out, {NEW_CLI: VALUE, "SKILL.md": seed_text + "\nRun scripts/hello-cli.pyz.\n"})
    destination = tmp_path / "export"
    assert export(out, destination)["patches"] == ["candidate.patch"]
    patch = (destination / "candidate.patch").read_text()
    assert "--- /dev/null" in patch and "+++ b/src/hello_cli/__init__.py" in patch
    assert NEW_CLI not in patch
    applied = subprocess.run(["git", "apply", str(destination / "candidate.patch")], cwd=world.skill,
                             capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert (world.skill / "src/hello_cli/__init__.py").read_text() == HELLO
    assert [config.name for config in load_targets(world.skill)] == ["alpha", "hello-cli"]
    report = read(destination / "report.json")
    assert report["new_cli"] == {"target": "hello-cli", "package": "hello_cli", "unused_helpers": []}


def test_export_without_a_component_has_no_new_cli_report(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    _completed(out, {})
    destination = tmp_path / "export"
    _ = export(out, destination)
    assert "new_cli" not in read(destination / "report.json")
    assert (destination / "candidate.patch").read_text() == ""


def test_a_helper_that_the_winner_guide_drops_is_reported_unused() -> None:
    seed = {"SKILL.md": "Run scripts/a.py then scripts/b.py. See references/c.py."}
    assert unused_helpers(seed, {"SKILL.md": "Run scripts/b.py."}) == ["scripts/a.py"]
    assert unused_helpers(seed, seed) == []
    assert unused_helpers({}, seed) == []


def test_the_export_report_lists_the_unused_helper(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    _ = (world.skill / "scripts/echo.py").write_text("print(1)\n")
    guide = world.skill / "SKILL.md"
    _ = guide.write_text(guide.read_text() + "\nRun scripts/echo.py first.\n")
    out = prepared(world, tmp_path, write_draft, approvals)
    _completed(out, {NEW_CLI: VALUE, "SKILL.md": "# Echo skill\n\nRun scripts/hello-cli.pyz.\n"})
    destination = tmp_path / "export"
    _ = export(out, destination)
    assert cast(dict[str, object], read(destination / "report.json")["new_cli"])["unused_helpers"] == ["scripts/echo.py"]


# --- AC-12: the reflection prompt -----------------------------------------------------------------------------

PROSE_HEAD = ("Improve only the supplied skill text components. Preserve the helper CLI contract. "
              "Write every proposed Markdown component in ASD-STE100 Simplified Technical English: "
              "active voice, present tense, one instruction per sentence, at most 20 words per procedural sentence, "
              "and at most 25 words per descriptive sentence. "
              "Return complete component contents. Do not alter independent checks or permissions.")


def test_the_prose_prompt_is_unchanged_byte_for_byte() -> None:
    prompt, _ = _workflow._reflection_request("prose", {"SKILL.md": "x"}, {}, ["SKILL.md"])
    assert prompt == PROSE_HEAD + '\n{"candidate": {"SKILL.md": "x"}, "feedback": {}}'


def test_the_prose_cli_prompt_names_fromargs_and_its_contract() -> None:
    prompt, _ = _workflow._reflection_request("prose+cli", {"SKILL.md": "x"}, {}, ["SKILL.md"])
    head = prompt.split("\n", 1)[0]
    assert ("The fromargs package is the default for a wedged Python source. Use one parser. "
            "Print JSON errors on stderr. "
            "Exit with code 2 for a usage error and code 3 for a failed input contract. ") in head
    assert "A non-Python helper keeps its language." in head and "no wedge.toml" in head
    assert "stdlib `argparse` helper" in head and "standard library" not in head
    assert "references/fromargs.md" not in head
    assert "ASD-STE100" in head and NEW_CLI not in head


def test_the_new_cli_sentence_appears_only_when_the_component_is_offered() -> None:
    offered, _ = _workflow._reflection_request("prose+cli", {NEW_CLI: ""}, {}, ["SKILL.md", NEW_CLI])
    assert f"The {NEW_CLI} component adds one new fromargs wedge target as TOML with the keys name and module." in offered
    absent, _ = _workflow._reflection_request("prose+cli", {"SKILL.md": "x"}, {}, ["SKILL.md"])
    assert "component adds one new" not in absent


# --- cure pass 1: cache key, layer fallback, source_paths, gate, and the whole path ---------------------------

def _break_layer(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    if kind == "uv-missing":
        def no_uv(_name: str) -> str | None:
            return None

        monkeypatch.setattr(_wedge_targets, "shutil", SimpleNamespace(which=no_uv, rmtree=shutil.rmtree))
        return

    def fail(*_args: object, **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["uv"], stderr="no network")

    monkeypatch.setattr(_wedge_targets, "populate_site_layer", fail)


def test_candidates_that_differ_in_a_fixed_file_of_the_new_target_get_separate_builds(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    session = open_session(out, Recorder())
    session.provider.close()
    rebuilder = session.rebuilder
    assert rebuilder is not None
    fixed = next(name for name in session.seed.frozen if name.endswith("lib/fromargs/__init__.py"))
    components = {name: session.seed.files[name] for name in session.seed.editable} | {NEW_CLI: VALUE}
    base = session.seed.changed(components)
    failing = [True]
    real = wedge_build._shiv

    def shiv(*args: object, **kwargs: object) -> None:
        if failing[0]:
            raise subprocess.CalledProcessError(1, ["shiv"], stderr="shiv exploded")
        real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(wedge_build, "_shiv", shiv)
    with pytest.raises(BuildFailed):
        _ = rebuilder.apply(base.with_frozen({fixed: b"VERSION = 2\n"}))
    failing[0] = False
    second = rebuilder.apply(base.with_frozen({fixed: b"VERSION = 3\n"}))
    third = rebuilder.apply(base.with_frozen({fixed: b"VERSION = 4\n"}))
    assert archive_text(second.frozen["scripts/hello-cli.pyz"], "fromargs/__init__.py") == "VERSION = 3\n"
    assert archive_text(third.frozen["scripts/hello-cli.pyz"], "fromargs/__init__.py") == "VERSION = 4\n"


def test_candidates_that_differ_in_an_editable_fromargs_source_build_their_own_text(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    guide = world.skill / "SKILL.md"
    _ = guide.write_text(guide.read_text() + "\nRun scripts/alpha.pyz.\n")
    out = prepared(world, tmp_path, write_draft, approvals)
    session = open_session(out, Recorder())
    session.provider.close()
    rebuilder = session.rebuilder
    assert rebuilder is not None
    source = next(name for name in session.seed.editable if name.endswith("lib/fromargs/__init__.py"))
    assert source.startswith("@wedge/")
    components = {name: session.seed.files[name] for name in session.seed.editable} | {NEW_CLI: VALUE}
    failing = [True]
    real = wedge_build._shiv

    def shiv(*args: object, **kwargs: object) -> None:
        if failing[0]:
            raise subprocess.CalledProcessError(1, ["shiv"], stderr="shiv exploded")
        real(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(wedge_build, "_shiv", shiv)
    with pytest.raises(BuildFailed):
        _ = rebuilder.apply(session.seed.changed(components | {source: "VERSION = 2\n"}))
    failing[0] = False
    second = rebuilder.apply(session.seed.changed(components | {source: "VERSION = 3\n"}))
    third = rebuilder.apply(session.seed.changed(components | {source: "VERSION = 4\n"}))
    assert archive_text(second.frozen["scripts/hello-cli.pyz"], "fromargs/__init__.py") == "VERSION = 3\n"
    assert archive_text(third.frozen["scripts/hello-cli.pyz"], "fromargs/__init__.py") == "VERSION = 4\n"


def test_an_existing_probe_cli_target_does_not_withdraw_the_component(world: World) -> None:
    from skillz_experiments._new_cli import inherits_source_paths

    text = (world.skill / "wedge.toml").read_text()
    assert not inherits_source_paths(world.skill, text)
    both = expand_files(world.skill, world.files(), toml_value("probe-cli", HELLO))["wedge.toml"]
    assert not inherits_source_paths(world.skill, both)


def test_a_layer_without_cyclopts_drops_the_component(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_target: Callable[..., Path],
        write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    def empty(_project: Path, _groups: object) -> tuple[str, bool]:
        return "", False

    monkeypatch.setattr(wedge_build, "_closure_requirements", empty)
    built = make_world(tmp_path, make_target)
    _ = (built.skill / "wedge.toml").write_text(built.toml() + 'groups = ["cli"]\n')
    fake_layer(monkeypatch, grouped_only=True)
    guide = built.skill / "SKILL.md"
    _ = guide.write_text(guide.read_text() + "\nRun scripts/alpha.pyz.\n")
    plan = plan_targets(built.skill, built.files(), load_contract(built.skill), "prose+cli")
    assert plan.capable is not None and [target.name for target in plan.editable] == ["alpha"]
    assert len(plan.layer_ids()) == 2
    settled, layers = settle(plan, tmp_path / "layers")
    assert settled.capable is None and len(layers) == 1 and "new_cli" not in settled.record()
    assert settled.dropped == "the fromargs site layer lacks cyclopts" == settled.record()["new_cli_dropped"]
    out = prepared(built, tmp_path, write_draft, approvals)
    assert NEW_CLI not in cast(list[str], read(out / "run.json")["editable"])
    assert "new_cli" not in cast(dict[str, object], read(out / "run.json")["own_targets"])


@pytest.mark.parametrize("kind", ["uv-missing", "populate-error"])
def test_a_layer_that_fails_only_for_the_component_drops_it(
        kind: str, world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = plan_targets(world.skill, world.files(), load_contract(world.skill), "prose+cli")
    assert plan.capable is not None and plan.editable == ()
    _break_layer(monkeypatch, kind)
    settled, layers = settle(plan, tmp_path / "layers")
    assert settled.capable is None and layers == {} and "new_cli" not in settled.record()
    assert settled.layer_ids() == []
    assert settled.record()["new_cli_dropped"] == settled.dropped != ""
    assert "new_cli_dropped" not in plan.record()


def test_a_dropped_component_is_named_when_prose_cli_finds_nothing_to_edit(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "run"
    _ = write_draft(out)
    approved_hash, calls = approvals(world.skill, out, edit="prose+cli")
    _break_layer(monkeypatch, "populate-error")
    with pytest.raises(CodedError) as raised:
        _ = run(world.skill, out, MODEL, edit="prose+cli", approve_cases=approved_hash, approve_budget=calls)
    assert raised.value.code == "helper-missing"
    assert "@new-cli is unavailable because the fromargs site layer failed" in str(raised.value)


def test_a_resume_tolerates_a_dropped_reason_in_the_record(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    guide = world.skill / "SKILL.md"
    _ = guide.write_text(guide.read_text() + "\nRun scripts/alpha.pyz.\n")
    out = prepared(world, tmp_path, write_draft, approvals)
    record = read(out / "run.json")
    cast(dict[str, object], record["own_targets"])["new_cli_dropped"] = "the fromargs site layer failed: x"
    write(out / "run.json", record)
    session = open_session(out, Recorder())
    session.provider.close()
    assert session.rebuilder is not None


@pytest.mark.parametrize("kind", ["uv-missing", "populate-error"])
def test_a_layer_that_an_editable_target_needs_still_fails_hard(
        kind: str, world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guide = world.skill / "SKILL.md"
    _ = guide.write_text(guide.read_text() + "\nRun scripts/alpha.pyz.\n")
    plan = plan_targets(world.skill, world.files(), load_contract(world.skill), "prose+cli")
    assert [target.name for target in plan.editable] == ["alpha"] and plan.capable is not None
    _break_layer(monkeypatch, kind)
    with pytest.raises(CodedError) as raised:
        _ = settle(plan, tmp_path / "layers")
    assert raised.value.code == "site-layer-failed"


def test_a_top_level_source_paths_withdraws_the_component(world: World) -> None:
    _ = (world.skill / "wedge.toml").write_text(
        'project = ".."\nrepo = "o/n"\nsource_paths = ["__init__.py"]\n\n[[target]]\nname = "alpha"\n'
        + 'entry = "alpha:main"\nsource = "../alpha"\ninclude = ["../lib/fromargs"]\n')
    assert load_targets(world.skill)[0].source_paths == ("__init__.py",)
    plan = plan_targets(world.skill, world.files(), load_contract(world.skill), "prose+cli")
    assert plan.capable is None
    with pytest.raises(CodedError) as raised:
        _ = expand_files(world.skill, world.files(), VALUE)
    assert raised.value.code == "new-cli-unavailable"


def test_a_package_that_matches_a_source_directory_is_a_package_collision(world: World) -> None:
    _ = (world.base / "alpha").rename(world.base / "alpha_src")
    _ = (world.skill / "wedge.toml").write_text(world.toml().replace("/alpha\"", "/alpha_src\""))
    assert load_targets(world.skill)[0].source.endswith("alpha_src")
    with pytest.raises(CodedError) as raised:
        _ = expand_files(world.skill, world.files(), toml_value("alpha-src", HELLO))
    assert raised.value.code == "new-cli-package-collision"


def test_a_winner_whose_component_is_rejected_stops_the_gate_with_build_failed(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    recorder = Recorder()
    session = open_session(out, recorder)
    seed = cast(dict[str, str], session.record["seed"])
    session.record["winner"] = seed | {NEW_CLI: "name = [unclosed"}
    try:
        with pytest.raises(Stop) as stopped:
            session.gate()
    finally:
        session.provider.close()
    assert stopped.value.code == "build-failed" and recorder.seen == []


def test_a_resume_reads_the_layer_names_once(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    seen: list[object] = []
    real = wedge_build.layer_import_names

    def counting(layer: SiteLayer) -> frozenset[str]:
        seen.append(layer)
        return real(layer)

    monkeypatch.setattr(_wedge_targets, "layer_import_names", counting)
    session = open_session(out, Recorder())
    session.provider.close()
    assert len(seen) == 1


def test_a_resume_that_cannot_list_the_layer_is_site_layer_drift(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]],
        monkeypatch: pytest.MonkeyPatch) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)

    def unreadable(_layer: object) -> frozenset[str]:
        raise OSError("layer vanished")

    monkeypatch.setattr(_wedge_targets, "layer_import_names", unreadable)
    with pytest.raises(CodedError) as raised:
        _ = open_session(out, Recorder())
    assert raised.value.code == "site-layer-drift"


@final
class Preferring:
    """A provider that proposes `@new-cli` and scores a candidate that carries the built `hello-cli` target above the rest."""

    def __init__(self, skill: Path, seed: dict[str, str]) -> None:
        self.contract = intake_contract(skill)
        self.seed = seed
        self.lock = threading.Lock()
        self.holdout: list[Candidate] = []

    def factory(self, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> _Preferred:
        return _Preferred(self, budget, checkpoint)


@final
class _Preferred:
    def __init__(self, state: Preferring, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.state, self.budget, self.checkpoint = state, budget, checkpoint

    def preflight(self) -> dict[str, object]:
        if self.budget.calls == 0:
            for _ in range(_workflow.PREFLIGHT_CALLS):
                self.budget.claim()
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        for _ in range(self.state.contract.calls(case.kind)):
            self.budget.claim(holdout=holdout)
        self.checkpoint()
        built = "scripts/hello-cli.pyz" in candidate.frozen
        if holdout:
            with self.state.lock:
                self.state.holdout.append(candidate)
        return {"score": 0.9 if built else 0.2, "candidate_hash": candidate.identity, "case_hash": case.identifier}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case
        assert schema is not None
        self.budget.claim(holdout=holdout)
        names = cast(list[str], schema["required"])
        return {"answer": {name: VALUE if name == NEW_CLI else self.state.seed[name] for name in names}}


def test_a_search_that_prefers_the_component_gates_with_the_built_target_and_exports_applicable_patches(
        world: World, tmp_path: Path, write_draft: Callable[..., list[str]], approvals: Callable[..., tuple[str, int]]) -> None:
    out = prepared(world, tmp_path, write_draft, approvals)
    state = Preferring(world.skill, cast(dict[str, str], read(out / "run.json")["seed"]))
    result = run(world.skill, out, MODEL, live=True, factory=state.factory)
    assert result["phase"] == "complete"
    record = read(out / "run.json")
    assert cast(dict[str, str], record["winner"])[NEW_CLI] == VALUE
    built = [candidate for candidate in state.holdout if "scripts/hello-cli.pyz" in candidate.frozen]
    assert built and len(built) < len(state.holdout)
    assert all(archive_text(candidate.frozen["scripts/hello-cli.pyz"], "hello_cli/__init__.py") == HELLO for candidate in built)
    destination = tmp_path / "export"
    exported = export(out, destination)
    assert exported["patches"] == ["candidate.patch"]
    copy = tmp_path / "copy"
    _ = shutil.copytree(world.repo, copy)
    skill = copy / world.skill.relative_to(world.repo)
    checked = subprocess.run(["git", "apply", "--check", str(destination / "candidate.patch")], cwd=skill,
                             capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert "+++ b/src/hello_cli/__init__.py" in (destination / "candidate.patch").read_text()
