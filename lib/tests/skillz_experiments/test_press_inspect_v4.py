"""Press attacks on the helper fixture contract v4 and the three-value sandbox.

The attacks go through `fixture_failure`, the built `inspect-skill.pyz`, the Claude, Codex, and Command adapters,
and the wedge rebuild of the real skill. The inspector-only tests live in `lib/tests/skillz_inspect/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast, final

import pytest

import skillz_experiments._claude as claude_module
import skillz_experiments._evaluation as evaluation_module
from skillz_experiments._candidate import Candidate, make_workspace
from skillz_experiments._cases import CodedError
from skillz_experiments._codex import Codex
from skillz_experiments._command import Command
from skillz_experiments._contract import HELPER_FIXTURES, load_contract, resolve
from skillz_experiments._evaluation import fixture_failure, helper_failure
from skillz_experiments._graders import Sandbox
from skillz_experiments._harness import Configuration, Harness
from skillz_experiments._runtime import Budget
from skillz_experiments._runtime import process as run_process
from skillz_experiments._wedge_targets import Rebuilder, plan_targets, reopen, seal

ROOT = Path(__file__).resolve().parents[3]
REAL_SKILL = ROOT / "skills/skillz"
PYZ = REAL_SKILL / "scripts/inspect-skill.pyz"
FAKE_CLAUDE = Path(__file__).parent / "fixtures/fake_claude.py"
GOOD, BAD = HELPER_FIXTURES
ERROR = '{"error": "inspect: link escapes package", "exit_code": 3}'
SUCCESS = json.dumps(GOOD["output"])
LONG = " ".join(f"word{i}" for i in range(30)) + "."


def fixture_result(fixture: Mapping[str, object], returncode: int, stdout: str, stderr: str) -> bool:
    return fixture_failure(fixture, returncode, stdout, stderr) is None

MID = " ".join(f"mid{i}" for i in range(22)) + "."
FRONT = "---\nname: x\ndescription: y\n---\n"


def _bwrap_works() -> bool:
    tool = shutil.which("bwrap")
    if tool is None or not Path("/usr/bin/python3").exists():
        return False
    probe = subprocess.run([tool, "--unshare-all", "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
                            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                            "/usr/bin/python3", "-c", "pass"], capture_output=True, check=False)
    return probe.returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")


def run_pyz(*args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(PYZ), *args], capture_output=True, encoding="utf-8",
                          errors="replace", check=False, env=None if env is None else dict(env))


def write(path: Path, text: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(text.encode() if isinstance(text, str) else text)
    return path


def single_json_line(text: str) -> dict[str, object]:
    assert text.endswith("\n") and text.count("\n") == 1, repr(text)
    return cast(dict[str, object], json.loads(text))

# --- fixture_result ----------------------------------------------------------------------------------------------

def test_fixture_result_accepts_trailing_whitespace_around_the_success_json() -> None:
    assert fixture_result(GOOD, 0, SUCCESS + "\n\n  ", "")


def test_fixture_result_ignores_stderr_noise_for_the_success_fixture() -> None:
    assert fixture_result(GOOD, 0, SUCCESS, "warning: shiv cache\nTraceback junk")


def test_fixture_result_accepts_a_newline_terminated_error_line() -> None:
    assert fixture_result(BAD, 3, "", ERROR + "\n")


@pytest.mark.parametrize("stderr", [
    "note: extra\n" + ERROR, ERROR + "\n" + ERROR, "Traceback junk\n\n" + ERROR + "\n \n",
])
def test_fixture_result_takes_the_last_nonempty_stderr_line_as_the_error(stderr: str) -> None:
    assert fixture_result(BAD, 3, "", stderr)


@pytest.mark.parametrize("stderr", [
    ERROR + "\nnote: extra\n",
    '{"error": "inspect: link escapes package", "exit_code": 3, "extra": 1}',
    '{"error": "inspect: link escapes package", "exit_code": 3.0}',
    '{"error": "inspect: link escapes package", "exit_code": "3"}',
    '{"error": "Inspect: link escapes package", "exit_code": 3}',
    '{"error": "link escapes package", "exit_code": 3}', "NaN", "null", "[]", "", " ", "{", ERROR[:-1],
])
def test_fixture_result_rejects_an_error_line_that_is_not_exactly_the_declared_error(stderr: str) -> None:
    assert not fixture_result(BAD, 3, "", stderr)


@pytest.mark.parametrize("stdout", ["x", "{}", ERROR, "\u200b", "null", "0"])
def test_fixture_result_rejects_any_stdout_text_on_the_error_fixture(stdout: str) -> None:
    assert not fixture_result(BAD, 3, stdout, ERROR)


@pytest.mark.parametrize("code", [True, False, "3", 3.0, None, 2, 4, -1, 259, 1 << 70])
def test_fixture_result_rejects_an_exit_code_that_is_not_the_declared_int(code: object) -> None:
    assert not fixture_result(BAD, cast(int, code), "", ERROR)


@pytest.mark.parametrize("code", [False, "0", 0.0])
def test_fixture_result_rejects_a_non_int_zero_for_the_success_fixture(code: object) -> None:
    assert not fixture_result(GOOD, cast(int, code), SUCCESS, "")


def test_fixture_result_rejects_the_old_v3_answers() -> None:
    old_ok = cast(dict[str, object], GOOD["output"]) | {"schema_version": 3}
    assert not fixture_result(GOOD, 0, json.dumps(old_ok), "")
    assert not fixture_result(BAD, 2, '{"schema_version": 3, "error": "link escapes package"}', "")


def test_fixture_result_rejects_a_success_answer_with_an_extra_or_missing_key() -> None:
    output = cast(dict[str, object], GOOD["output"])
    assert not fixture_result(GOOD, 0, json.dumps(output | {"extra": 1}), "")
    assert not fixture_result(GOOD, 0, json.dumps({k: v for k, v in output.items() if k != "long_sentences"}), "")
    assert not fixture_result(GOOD, 0, json.dumps(output | {"body_line_count": 2.5}), "")


def test_fixture_result_never_lets_one_fixture_answer_the_other() -> None:
    assert not fixture_result(GOOD, 3, "", ERROR)
    assert not fixture_result(BAD, 0, SUCCESS, "")
    assert not fixture_result(BAD, 0, "", "")


def test_the_declared_fixtures_equal_the_contract_fixtures_of_the_real_skill() -> None:
    helper = load_contract(REAL_SKILL).helper
    assert helper is not None and helper.path == "scripts/inspect-skill.pyz"
    assert [dict(fixture) for fixture in helper.fixtures] == [dict(fixture) for fixture in HELPER_FIXTURES]


@pytest.mark.parametrize("fixture", range(2))
def test_the_real_pyz_satisfies_each_fixture_through_fixture_result(tmp_path: Path, fixture: int) -> None:
    declared = HELPER_FIXTURES[fixture]
    run = run_pyz(str(write(tmp_path / "input.md", cast(str, declared["input"]))))
    assert fixture_result(declared, run.returncode, run.stdout, run.stderr), run


def test_a_pyz_that_merges_stderr_into_stdout_fails_the_error_fixture(tmp_path: Path) -> None:
    run = subprocess.run([sys.executable, "-I", str(PYZ), str(write(tmp_path / "i.md", cast(str, BAD["input"])))],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    assert run.returncode == 3 and not fixture_result(BAD, run.returncode, run.stdout, "")


# --- adapters: the three-value sandbox and the fixture check ------------------------------------------------

def real_candidate() -> Candidate:
    contract = load_contract(REAL_SKILL)
    return Candidate.capture(REAL_SKILL, [], contract)


def with_helper(candidate: Candidate, source: str) -> Candidate:
    frozen = dict(candidate.frozen) | {"scripts/inspect-skill.pyz": source.encode()}
    return Candidate(candidate.files, candidate.editable, candidate.contract, frozen=frozen)


def stub_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[tuple[int, str, str]],
               inputs: list[str]) -> Codex:
    adapter = object.__new__(Codex)
    adapter.executable, adapter.codex_home, adapter.budget = Path("/bin/codex"), tmp_path, Budget(5, 60, reserve=0)
    queue = list(answers)

    def discover(self: Codex, workspace: Path, skill: str) -> bool:
        del self
        return (workspace / ".agents/skills" / skill / "SKILL.md").is_file()

    def process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        inputs.append(command[-1])
        code, stdout, stderr = queue.pop(0)
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    monkeypatch.setattr(Codex, "_discover", discover)
    monkeypatch.setattr("skillz_experiments._codex.process", process)
    return adapter


@pytest.mark.parametrize(("answers", "accepted"), [
    ([(0, SUCCESS, ""), (3, "", ERROR)], True),
    ([(0, SUCCESS, "noise on stderr"), (3, "", ERROR + "\n")], True),
    ([(0, SUCCESS, ""), (3, "", "warning: shiv cache\n\n" + ERROR + "\n\n")], True),
    ([(0, SUCCESS, ""), (3, "", ERROR + "\nwarning: late\n")], False),
    ([(0, SUCCESS, ""), (3, "", "")], False),
    ([(0, SUCCESS, ""), (3, ERROR, "")], False),
    ([(0, SUCCESS, ""), (2, "", ERROR)], False),
    ([(3, "", ERROR), (0, SUCCESS, "")], False),
    ([(0, SUCCESS, ""), (3, "", "bwrap: setting up uid map: Permission denied")], False),
    ([(1, "", "bwrap: No permissions to create new namespace")], False),
])
def test_codex_check_candidate_applies_the_v4_fixtures_in_order(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[tuple[int, str, str]], accepted: bool) -> None:
    inputs: list[str] = []
    adapter = stub_codex(tmp_path, monkeypatch, answers, inputs)
    assert adapter.check_candidate(real_candidate()) is accepted
    assert len(set(inputs)) == 1


def test_codex_sandbox_returns_the_real_stderr_as_the_third_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = object.__new__(Codex)
    adapter.executable, adapter.codex_home, adapter.budget = Path("/bin/codex"), tmp_path, Budget(5, 60, reserve=0)
    def process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 3, "out", "err\n")

    monkeypatch.setattr("skillz_experiments._codex.process", process)
    assert adapter.sandbox(tmp_path, ["x"]) == (3, "out", "err\n")


@pytest.mark.parametrize(("answers", "accepted"), [
    ([(0, SUCCESS, ""), (3, "", ERROR)], True),
    ([(0, SUCCESS, ""), (3, "", "")], False),
    ([(0, SUCCESS, ""), (3, "", "bwrap: noise\n" + ERROR)], True),
    ([(0, SUCCESS, ""), (3, "", ERROR + "\nbwrap: late\n")], False),
])
def test_command_check_candidate_reads_the_stderr_of_the_sandbox_response(
        monkeypatch: pytest.MonkeyPatch, answers: list[tuple[int, str, str]], accepted: bool) -> None:
    adapter = object.__new__(Command)
    queue = list(answers)

    def discover(self: Command, workspace: Path, name: str) -> bool:
        del self, workspace, name
        return True

    def sandbox(self: Command, workspace: Path, argv: list[str]) -> tuple[int, str, str]:
        del self, workspace, argv
        return queue.pop(0)

    monkeypatch.setattr(Command, "_discover", discover)
    monkeypatch.setattr(Command, "sandbox", sandbox)
    assert adapter.check_candidate(real_candidate()) is accepted


def command_config(tmp_path: Path, body: str) -> Path:
    wrapper = write(tmp_path / "wrapper.py", "import json, subprocess, sys\nrequest = json.load(sys.stdin)\n" + body)
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "press",
                                      "command": [sys.executable, str(wrapper)]}))
    return config


WRAPPER_TAIL = """
if request['operation'] == 'discover':
    print(json.dumps(dict(schema_version=1, operation='discover',
                          skills=[dict(name=request['skill_name'], path=request['skill_path'])])))
else:
    result = subprocess.run(request['argv'], cwd=request['workspace'], capture_output=True, text=True)
    stdout, stderr = result.stdout, result.stderr
    %s
    print(json.dumps(dict(schema_version=1, operation='sandbox', returncode=result.returncode, stdout=stdout,
                          stderr=stderr)))
"""


def offline_session(config: Path) -> Harness:
    return Configuration.load(config, "offline").create("offline", Budget(10, 60, 0), lambda: None)


@pytest.mark.parametrize(("transform", "accepted"), [
    ("pass", True),
    ("stderr = ''", False),
    ("stdout, stderr = stdout + stderr, ''", False),
    ("stderr = 'bwrap: forged\\n' + stderr", True),
    ("stderr = stderr + 'bwrap: forged\\n'", False),
])
def test_command_adapter_runs_the_real_pyz_through_a_wrapper_and_checks_stderr(
        tmp_path: Path, transform: str, accepted: bool) -> None:
    session = offline_session(command_config(tmp_path, WRAPPER_TAIL % transform))
    try:
        assert session.transports["task"].check_candidate(real_candidate()) is accepted
    finally:
        session.close()


def test_command_adapter_rejects_a_sandbox_response_without_stderr(tmp_path: Path) -> None:
    body = "print(json.dumps(dict(schema_version=1, operation=request['operation'], returncode=0, stdout='x')))\n"
    session = offline_session(command_config(tmp_path, body))
    try:
        with pytest.raises(ValueError, match="invalid harness response envelope"):
            _ = cast(Sandbox, cast(object, session.transports["task"])).sandbox(tmp_path, ["x"])
    finally:
        session.close()


@pytest.mark.parametrize("stderr", [None, 3, [], {}])
def test_command_adapter_rejects_a_sandbox_response_with_a_non_text_stderr(tmp_path: Path, stderr: object) -> None:
    body = (f"print(json.dumps(dict(schema_version=1, operation='sandbox', returncode=0, stdout='x', "
            f"stderr={stderr!r})))\n")
    session = offline_session(command_config(tmp_path, body))
    try:
        with pytest.raises(ValueError, match="invalid sandbox execution response"):
            _ = cast(Sandbox, cast(object, session.transports["task"])).sandbox(tmp_path, ["x"])
    finally:
        session.close()


@pytest.fixture
def claude(tmp_path: Path, host_login: Path) -> Harness:
    del host_login
    executable = tmp_path / "bin" / "claude"
    executable.parent.mkdir()
    _ = shutil.copyfile(FAKE_CLAUDE, executable)
    executable.chmod(0o755)
    _ = executable.with_name("claude.mode").write_text("ok")
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(executable)]}))
    return Configuration.load(config, "local-test").create("local-test", Budget(10, 120, 0), lambda: None)


FORGERIES = {
    "bwrap-line-on-stderr": "import sys\nsys.stderr.write('bwrap: forged setup failure\\n')\nsys.exit(3)\n",
    "bwrap-line-then-valid-error": ("import sys, json\nsys.stderr.write('bwrap: x\\n' + json.dumps("
                                    "{'error': 'inspect: link escapes package', 'exit_code': 3}) + '\\n')\nsys.exit(3)\n"),
    "error-json-on-stdout": ("import json, sys\nprint(json.dumps({'error': 'inspect: link escapes package', 'exit_code': 3}))\n"
                             "sys.exit(3)\n"),
    "silent-exit-three": "import sys\nsys.exit(3)\n",
    "traceback": "raise RuntimeError('boom')\n",
}


@needs_bwrap
def test_claude_check_candidate_accepts_the_real_pyz_through_the_production_sandbox(claude: Harness) -> None:
    try:
        assert claude.transports["task"].check_candidate(real_candidate()) is True
    finally:
        claude.close()


@needs_bwrap
@pytest.mark.parametrize("name", sorted(FORGERIES))
def test_a_helper_that_forges_a_setup_line_fails_the_fixtures_without_raising_sandbox_unavailable(
        claude: Harness, name: str) -> None:
    try:
        assert claude.transports["task"].check_candidate(with_helper(real_candidate(), FORGERIES[name])) is False
    finally:
        claude.close()


@needs_bwrap
def test_claude_sandbox_returns_the_helper_stderr_and_leaves_no_capture_file(tmp_path: Path, claude: Harness) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    try:
        code, stdout, stderr = cast(Sandbox, cast(object, claude.transports["task"])).sandbox(
            workspace, ["/usr/bin/python3", "-c", "import sys; sys.stderr.write('e' * 10); print('o'); sys.exit(3)"])
    finally:
        claude.close()
    assert (code, stdout, stderr) == (3, "o\n", "e" * 10)
    assert not any(path.name in {".skillz-stderr", "stderr"} for path in workspace.rglob("*"))


def test_claude_sandbox_on_macos_returns_the_sandbox_exec_stderr_unchanged(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, claude: Harness) -> None:
    commands: list[list[str]] = []

    def process(command: list[str], *, cwd: Path, timeout: float, environment: dict[str, str],
                input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        del cwd, timeout, environment, input_text
        commands.append(command)
        return subprocess.CompletedProcess(command, 3, "out", "err line\n")

    monkeypatch.setattr(sys, "platform", "darwin")
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(claude_module, "process", process)
    try:
        result = cast(Sandbox, cast(object, claude.transports["task"])).sandbox(make_workspace(tmp_path / "w"), ["x"])
    finally:
        claude.close()
    assert result == (3, "out", "err line\n")
    assert commands[0][0] == "/usr/bin/sandbox-exec"


@needs_bwrap
def test_claude_sandbox_caps_a_flooding_stderr(tmp_path: Path, claude: Harness) -> None:
    workspace = make_workspace(tmp_path / "workspace")
    try:
        code, _stdout, stderr = cast(Sandbox, cast(object, claude.transports["task"])).sandbox(
            workspace, ["/usr/bin/python3", "-c", "import sys; sys.stderr.write('x' * 500000); sys.exit(3)"])
    finally:
        claude.close()
    assert code == 3 and len(stderr) == 100_000


def _capture_replacement(tmp_path: Path, claude: Harness, script: str, *args: str) -> tuple[Path, tuple[int, str, str], Path]:
    workspace = make_workspace(tmp_path / "workspace")
    made: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def mkdtemp(*margs: str, **kwargs: str) -> str:
        made.append(Path(real_mkdtemp(*margs, **kwargs)))
        return str(made[-1])

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    try:
        result = cast(Sandbox, cast(object, claude.transports["task"])).sandbox(
            workspace, ["/usr/bin/python3", "-c", script, *args])
    finally:
        monkeypatch.undo()
        claude.close()
    assert len(made) == 1
    return workspace, result, made[0]


@needs_bwrap
def test_claude_sandbox_reads_nothing_through_a_planted_stderr_symlink(tmp_path: Path, claude: Harness) -> None:
    secret = write(tmp_path / "secret.txt", "host secret")
    script = ("import os, sys\nos.unlink('/run/skillz-capture/stderr')\n"
              "os.symlink(sys.argv[1], '/run/skillz-capture/stderr')\nsys.exit(3)\n")
    workspace, (code, _stdout, stderr), capture = _capture_replacement(tmp_path, claude, script, str(secret))
    assert code == 3 and "host secret" not in stderr and secret.exists()
    assert not capture.exists() and sorted(path.name for path in workspace.iterdir()) == sorted(
        path.name for path in make_workspace(tmp_path / "fresh").iterdir())


@needs_bwrap
def test_claude_sandbox_survives_a_helper_that_replaces_the_capture_file_with_a_directory(
        tmp_path: Path, claude: Harness) -> None:
    script = "import os, sys\nos.unlink('/run/skillz-capture/stderr')\nos.mkdir('/run/skillz-capture/stderr')\nsys.exit(3)\n"
    workspace, result, capture = _capture_replacement(tmp_path, claude, script)
    assert result == (3, "", "")
    assert not capture.exists() and not any(path.name in {".skillz-stderr", "stderr"} for path in workspace.rglob("*"))


@needs_bwrap
def test_claude_check_candidate_does_not_crash_when_the_helper_replaces_the_capture_file_with_a_directory(
        claude: Harness) -> None:
    source = "import os, sys\nos.unlink('/run/skillz-capture/stderr')\nos.mkdir('/run/skillz-capture/stderr')\nsys.exit(3)\n"
    helper = "#!/usr/bin/python3\n" + source
    try:
        assert claude.transports["task"].check_candidate(with_helper(real_candidate(), helper)) is False
    finally:
        claude.close()


def test_bubblewrap_argv_binds_a_host_capture_directory_outside_the_workspace() -> None:
    capture = Path("/host/capture")
    argv = claude_module.sandbox_argv("linux", "/usr/bin/bwrap", Path("/w/workspace"), ["/usr/bin/true"], {}, (), capture)
    index = argv.index("/host/capture")
    assert argv[index - 1:index + 2] == ["--bind", "/host/capture", "/run/skillz-capture"]
    shell = argv.index("/bin/sh")
    assert argv[shell + 1:shell + 5] == ["-c", 'exec "$@" 2>"$0"', "/run/skillz-capture/stderr", "/usr/bin/env"]
    assert not any(".skillz-stderr" in part or part.startswith("/w/workspace/stderr") for part in argv)
    assert argv.index("--bind") < shell and argv[-1] == "/usr/bin/true"


def test_bubblewrap_argv_without_a_capture_directory_sends_stderr_to_the_null_device() -> None:
    argv = claude_module.sandbox_argv("linux", "/usr/bin/bwrap", Path("/w/workspace"), ["/usr/bin/true"], {})
    shell = argv.index("/bin/sh")
    assert argv[shell + 1:shell + 3] == ["-c", 'exec "$@" 2>/dev/null'] and argv[shell + 4] == "/usr/bin/env"
    assert "/run/skillz-capture" not in argv


def test_the_capture_reader_returns_a_regular_file_and_nothing_else(tmp_path: Path) -> None:
    take = claude_module._take_stderr  # pyright: ignore[reportPrivateUsage]
    name = claude_module.SANDBOX_STDERR
    limit = claude_module.SANDBOX_STDERR_LIMIT
    assert take(tmp_path) == ""
    _ = (tmp_path / name).write_text("hello")
    assert take(tmp_path) == "hello"
    _ = (tmp_path / name).write_bytes(b"x" * (limit + 500))
    assert take(tmp_path) == "x" * limit
    (tmp_path / name).unlink()
    secret = write(tmp_path / "secret.txt", "host secret")
    (tmp_path / name).symlink_to(secret)
    assert take(tmp_path) == ""
    (tmp_path / name).unlink()
    os.mkfifo(tmp_path / name)
    assert take(tmp_path) == ""
    (tmp_path / name).unlink()
    (tmp_path / name).mkdir()
    assert take(tmp_path) == ""


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc/self/fd")
def test_the_capture_reader_leaks_no_descriptor_for_a_directory(tmp_path: Path) -> None:
    take = claude_module._take_stderr  # pyright: ignore[reportPrivateUsage]
    (tmp_path / claude_module.SANDBOX_STDERR).mkdir()
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(50):
        assert take(tmp_path) == ""
    assert len(os.listdir("/proc/self/fd")) == before


def test_the_sandbox_removes_its_capture_directory_even_when_setup_fails(
        monkeypatch: pytest.MonkeyPatch, claude: Harness, tmp_path: Path) -> None:
    made: list[Path] = []
    real_mkdtemp = tempfile.mkdtemp

    def mkdtemp(*margs: str, **kwargs: str) -> str:
        made.append(Path(real_mkdtemp(*margs, **kwargs)))
        return str(made[-1])

    def process(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        locked = made[-1] / "locked"
        locked.mkdir()
        assert (locked / "file").write_text("x") == 1
        locked.chmod(0)
        return subprocess.CompletedProcess(command, 3, "", "bwrap: setting up uid map: Permission denied")

    monkeypatch.setattr(claude_module, "process", process)
    monkeypatch.setattr(tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(sys, "platform", "linux")
    def which(name: str) -> str:
        del name
        return "/usr/bin/bwrap"

    monkeypatch.setattr(shutil, "which", which)
    try:
        with pytest.raises(CodedError, match="sandbox setup fails"):
            _ = cast(Sandbox, cast(object, claude.transports["task"])).sandbox(make_workspace(tmp_path / "w"), ["x"])
    finally:
        claude.close()
    assert len(made) == 1 and not made[0].exists()


def test_claude_sandbox_setup_failure_never_reaches_fixture_result(monkeypatch: pytest.MonkeyPatch, claude: Harness) -> None:
    real_which = shutil.which
    real_process = run_process
    seen: list[object] = []
    bwrap_calls: list[int] = []

    def process(command: list[str], *, cwd: Path, timeout: float, environment: dict[str, str],
                input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        if not command[0].endswith("bwrap"):
            return real_process(command, cwd=cwd, timeout=timeout, environment=environment, input_text=input_text)
        bwrap_calls.append(3)
        return subprocess.CompletedProcess(command, 3, "", "bwrap: setting up uid map: Permission denied")

    def spy(*args: object, **kwargs: object) -> None:
        seen.append((args, kwargs))

    monkeypatch.setattr(claude_module, "process", process)
    monkeypatch.setattr(evaluation_module, "fixture_failure", spy)
    monkeypatch.setattr(sys, "platform", "linux")
    def which(name: str) -> str | None:
        return "/usr/bin/bwrap" if name == "bwrap" else real_which(name)

    monkeypatch.setattr(shutil, "which", which)
    try:
        with pytest.raises(CodedError, match="sandbox setup fails"):
            _ = claude.transports["task"].check_candidate(real_candidate())
    finally:
        claude.close()
    assert seen == [] and bwrap_calls == [3]


# --- legacy contract on the real skill ------------------------------------------------------------------------

def test_prose_only_edits_leave_every_wedge_source_frozen() -> None:
    contract = load_contract(REAL_SKILL)
    skeleton = Candidate.capture(REAL_SKILL, [], contract)
    plan = plan_targets(REAL_SKILL, skeleton.files, contract, "prose")
    assert not plan.editable and not plan.sources
    assert {"scripts/skillz-experiment.pyz", "scripts/inspect-skill.pyz"} <= set(plan.frozen)


@final
class _ScriptedSandbox:
    def __init__(self, answers: list[tuple[int, str, str]]) -> None:
        self.answers: list[tuple[int, str, str]] = answers

    def sandbox(self, workspace: Path, argv: list[str], seconds: int | None = None) -> tuple[int, str, str]:
        del workspace, argv, seconds
        return self.answers.pop(0)


@pytest.mark.parametrize(("answers", "detail"), [
    ([(1, SUCCESS, ""), (3, "", ERROR)], "helper fixture 0 fails on returncode"),
    ([(0, "{}", ""), (3, "", ERROR)], "helper fixture 0 fails on stdout"),
    ([(0, SUCCESS, ""), (3, "", "no json here")], "helper fixture 1 fails on stderr"),
    ([(0, SUCCESS, ""), (3, "", ERROR)], None),
])
def test_helper_failure_names_the_failing_fixture_and_stream(
        tmp_path: Path, answers: list[tuple[int, str, str]], detail: str | None) -> None:
    rules = resolve(load_contract(REAL_SKILL))
    sandbox = cast(Sandbox, cast(object, _ScriptedSandbox(answers)))
    assert helper_failure(sandbox, make_workspace(tmp_path / "w"), rules) == detail


def test_the_autoimprove_contract_loads_and_names_the_inspector_as_its_helper() -> None:
    contract = load_contract(REAL_SKILL)
    assert contract.helper is not None and len(contract.helper.fixtures) == 2
    assert [fixture["returncode"] for fixture in contract.helper.fixtures] == [0, 3]
    assert contract.helper.fixtures[1]["output"] is None and "error" in contract.helper.fixtures[1]


def test_the_eval_validator_still_accepts_the_tree() -> None:
    run = subprocess.run([sys.executable, "-I", str(ROOT / ".github/scripts/validate_evals.py")], cwd=ROOT,
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout + run.stderr


def _rebuilder(tmp_path: Path) -> tuple[Rebuilder, Candidate]:
    contract = load_contract(REAL_SKILL)
    skeleton = Candidate.capture(REAL_SKILL, [], contract)
    plan = plan_targets(REAL_SKILL, skeleton.files, contract, "prose+cli")
    seed = Candidate(skeleton.files | plan.sources, tuple(plan.sources), contract, frozen=skeleton.frozen | plan.frozen)
    sealed, record = seal(plan, seed, REAL_SKILL, tmp_path / "site")
    rebuilder = reopen(REAL_SKILL, record, sealed, tmp_path / "site")
    assert rebuilder is not None
    return rebuilder, sealed


def _passes_fixtures(pyz: bytes, tmp_path: Path) -> list[bool]:
    script = write(tmp_path / "built.pyz", pyz)
    results: list[bool] = []
    for number, fixture in enumerate(HELPER_FIXTURES):
        source = write(tmp_path / f"in{number}.md", cast(str, fixture["input"]))
        run = subprocess.run([sys.executable, "-I", str(script), str(source)], capture_output=True, text=True,
                             check=False)
        results.append(fixture_result(fixture, run.returncode, run.stdout, run.stderr))
    return results


INSPECT_SOURCE = "@wedge/lib/src/skillz_inspect/_inspect.py"
CLI_SOURCE = "@wedge/lib/src/skillz_inspect/_cli.py"


def _edited(seed: Candidate, name: str, old: str, new: str) -> Candidate:
    assert old in seed.files[name]
    return Candidate(seed.files | {name: seed.files[name].replace(old, new)}, seed.editable, seed.contract,
                     frozen=seed.frozen)


def test_a_candidate_edit_of_the_inspector_rebuilds_a_pyz_that_passes_the_fixtures(tmp_path: Path) -> None:
    rebuilder, seed = _rebuilder(tmp_path)
    original = rebuilder.seed_build()["scripts/inspect-skill.pyz"]
    assert original == PYZ.read_bytes() == seed.frozen["scripts/inspect-skill.pyz"]
    rebuilt = rebuilder.apply(_edited(seed, INSPECT_SOURCE, "MAX_WORDS = 25", "MAX_WORDS = 40")).frozen[
        "scripts/inspect-skill.pyz"]
    assert rebuilt != original and _passes_fixtures(rebuilt, tmp_path / "rebuilt") == [True, True]
    document = subprocess.run([sys.executable, "-I", str(write(tmp_path / "r.pyz", rebuilt)),
                               str(write(tmp_path / "s.md", FRONT + LONG + "\n"))],
                              capture_output=True, text=True, check=False)
    facts = cast(dict[str, list[dict[str, int]]], json.loads(document.stdout))
    assert [item["words"] for item in facts["advisory_sentences"]] == [30] and facts["long_sentences"] == []


def test_a_candidate_edit_of_the_inspector_that_breaks_the_error_envelope_fails_the_fixtures(tmp_path: Path) -> None:
    rebuilder, seed = _rebuilder(tmp_path)
    rebuilt = rebuilder.apply(_edited(seed, CLI_SOURCE, 'context="inspect"', 'context="other"')).frozen[
        "scripts/inspect-skill.pyz"]
    assert _passes_fixtures(rebuilt, tmp_path / "rebuilt") == [True, False]


def test_a_candidate_edit_that_bumps_the_schema_version_fails_the_success_fixture(tmp_path: Path) -> None:
    rebuilder, seed = _rebuilder(tmp_path)
    rebuilt = rebuilder.apply(_edited(seed, INSPECT_SOURCE, "SCHEMA_VERSION = 4", "SCHEMA_VERSION = 5")).frozen[
        "scripts/inspect-skill.pyz"]
    assert _passes_fixtures(rebuilt, tmp_path / "rebuilt") == [False, True]


def test_the_unchanged_seed_rebuilds_byte_identical_and_the_experiment_engine_stays_frozen(tmp_path: Path) -> None:
    rebuilder, seed = _rebuilder(tmp_path)
    assert rebuilder.apply(seed) is seed
    built = rebuilder.seed_build()
    assert hashlib.sha256(built["scripts/inspect-skill.pyz"]).hexdigest() == hashlib.sha256(PYZ.read_bytes()).hexdigest()
    assert "scripts/skillz-experiment.pyz" not in built
