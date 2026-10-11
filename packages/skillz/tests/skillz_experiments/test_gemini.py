from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from skillz_experiments._cli import main
from skillz_experiments.harness import Configuration, command_profile
from skillz_experiments.wrapper import _gemini
from skillz_experiments.wrapper._gemini import respond
from skillz_experiments._budget import Budget

FIXTURES = Path(__file__).parent / "fixtures"
MAIN = "import sys; from skillz_experiments._cli import main; sys.exit(main())"
SCHEMA: dict[str, object] = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"],
                             "additionalProperties": False}


def no_dirs() -> list[Path]:
    return []


def fake_bin(tmp_path: Path) -> Path:
    """Put fake `gemini` and `docker` executables in a directory. The wrapper finds them with --bin-dir."""
    directory = tmp_path / "bin"
    directory.mkdir()
    for name, source in {"gemini": "fake_gemini.py", "docker": "fake_docker.py"}.items():
        script = directory / name
        _ = script.write_text(f"#!{sys.executable}\n" + (FIXTURES / source).read_text())
        script.chmod(0o700)
    return directory


def logged(path: Path) -> list[dict[str, object]]:
    return [cast(dict[str, object], json.loads(line)) for line in path.read_text().splitlines()]


def docker_calls(directory: Path) -> list[list[str]]:
    return [cast(list[str], json.loads(line)) for line in (directory / "docker.log").read_text().splitlines()]


def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "skillz-task-x" / "workspace"
    (path / ".agents/skills").mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A host HOME that holds a login and the user files that the wrapper must never expose to Gemini."""
    home = tmp_path / "home"
    (home / ".gemini/skills/user-skill").mkdir(parents=True)
    for name in ("oauth_creds.json", "settings.json", "GEMINI.md", ".env"):
        _ = (home / ".gemini" / name).write_text("private")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(sys, "platform", "linux")
    return home


def request(operation: str, root: Path, **payload: object) -> str:
    return json.dumps({"schema_version": 1, "operation": operation, "workspace": str(root), "model": "m", **payload})


def configured(tmp_path: Path, directory: Path) -> Path:
    config = tmp_path / "harness.json"
    wrapper = [sys.executable, "-c", MAIN, "harness-wrapper", "gemini", "--bin-dir", str(directory)]
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "command", "identity": "gemini-fake",
                                     "command": wrapper}))
    return config


def infer(directory: Path, root: Path, **options: object) -> dict[str, object]:
    return respond(request("infer", root, prompt="p\nq", response_schema=SCHEMA), bin_dir=directory, **options)  # pyright: ignore[reportArgumentType]


SUBPROCESS_WRAPPER = pytest.mark.skipif(
    sys.platform == "darwin", reason="the wrapper subprocess sees the real platform and picks sandbox-exec, not docker")


def test_wrapper_passes_the_runner_sandbox_probe_and_discovery(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    config = configured(tmp_path, directory)
    status = main(["self-test", "--model", "offline", "--preflight-only", "--harness-config", str(config)])
    assert status == 0
    flags = docker_calls(directory)[0]
    assert flags[:2] == ["run", "--rm"]
    assert flags[flags.index("--name") + 1].startswith("skillz-")
    assert flags[flags.index("--network") + 1] == "none"
    assert flags[flags.index("--cap-drop") + 1] == "ALL"
    assert flags[flags.index("--pids-limit") + 1] == "256" and flags[flags.index("--memory") + 1] == "2g"
    assert any(item.startswith("type=bind,source=") and item.endswith("/.agents,readonly") for item in flags)
    assert flags[flags.index("--entrypoint") + 1] == "/usr/bin/timeout"
    calls = [item["argv"] for item in logged(directory / "gemini.log")]
    assert calls and all(call == ["skills", "list"] for call in calls)


@SUBPROCESS_WRAPPER
def test_probe_fails_when_the_container_denies_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    directory = fake_bin(tmp_path)
    script = directory / "docker"
    _ = script.write_text("\n".join([
        f"#!{sys.executable}", "import subprocess,sys",
        f"a=sys.argv[1:];c=a[a.index('/usr/bin/env')+1+{len(_gemini.CONTAINER_ENV)}:]",
        "r=subprocess.run(c,cwd=a[a.index('-w')+1],text=True)", "sys.exit(r.returncode)", ""]))
    config = configured(tmp_path, directory)
    assert main(["self-test", "--model", "offline", "--preflight-only", "--harness-config", str(config)]) != 0
    assert "harness isolation preflight fails; no unsafe fallback" in capsys.readouterr().err


@SUBPROCESS_WRAPPER
def test_infer_runs_gemini_with_the_probe_restrictions_and_reports_usage(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    adapter = Configuration.load(configured(tmp_path, directory), "fake-model").create(
        "fake-model", Budget(5, 60, 0), lambda: None)
    try:
        result = adapter.transports["task"].invoke("Say x", schema=SCHEMA)
    finally:
        adapter.close()
    assert result["answer"] == {"x": "fake"}
    assert result["usage"] == {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3}
    call = logged(directory / "gemini.log")[-1]
    argv = cast(list[str], call["argv"])
    assert argv[:5] == ["-s", "--approval-mode=yolo", "-o", "stream-json", "-m"] and argv[5] == "fake-model"
    assert "Say x" not in " ".join(argv) and "Say x" in str(call["stdin"])
    env = cast(dict[str, str], call["env"])
    assert env["GEMINI_SANDBOX"] == "docker" and env["GEMINI_SANDBOX_IMAGE"] == _gemini.IMAGE
    flags = env["SANDBOX_FLAGS"].split()
    assert flags[flags.index("--cap-drop") + 1] == "ALL" and "no-new-privileges" in flags
    assert "--pids-limit" in flags and "--memory" in flags
    agents = Path(str(call["cwd"])).resolve() / ".agents"
    assert env["SANDBOX_MOUNTS"] == f"{agents}:{agents}:ro"
    events = cast(list[dict[str, object]], result["events"])
    commands = [cast(dict[str, object], item["item"])["command"] for item in events if item["type"] == "item.completed"]
    assert commands == ["cat .agents/skills/demo/SKILL.md", "sh -c false"]


def test_infer_passes_the_image_and_the_macos_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    _ = infer(directory, root, image="example.test/box@sha256:abc")
    assert cast(dict[str, str], logged(directory / "gemini.log")[-1]["env"])["GEMINI_SANDBOX_IMAGE"] == "example.test/box@sha256:abc"
    monkeypatch.setattr(sys, "platform", "darwin")
    _ = (directory / "sandbox-exec").write_text(f"#!{sys.executable}\n")
    (directory / "sandbox-exec").chmod(0o700)
    _ = infer(directory, root)
    env = cast(dict[str, str | None], logged(directory / "gemini.log")[-1]["env"])
    assert env["GEMINI_SANDBOX"] == "sandbox-exec" and env["SEATBELT_PROFILE"] == "skillz"
    assert env["GEMINI_SANDBOX_IMAGE"] is None and env["SANDBOX_FLAGS"] is None


def test_infer_trace_holds_only_the_tool_calls_gemini_reported(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    result = infer(directory, workspace(tmp_path))
    assert result["trace"] == [{"argv": ["cat", ".agents/skills/demo/SKILL.md"], "exit_code": 0},
                               {"argv": ["sh", "-c", "false"], "exit_code": 1}]


@pytest.mark.parametrize("mode", ["no-tools", "malformed"])
def test_infer_without_usable_tool_events_returns_an_empty_trace(tmp_path: Path, mode: str) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "gemini.mode").write_text(mode)
    result = infer(directory, workspace(tmp_path))
    assert result["trace"] == [] and result["answer"] == {"x": "fake"}


@pytest.mark.parametrize(("mode", "message"), [("fails", "gemini exits 1: quota exhausted"),
                                                ("bad-answer", "not a JSON object"),
                                                ("error-result", "gemini reports an error: .*quota")])
def test_infer_faults_stop_with_a_reason(tmp_path: Path, mode: str, message: str) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "gemini.mode").write_text(mode)
    with pytest.raises(RuntimeError, match=message):
        _ = infer(directory, workspace(tmp_path))


def test_gemini_sees_only_the_login_file_in_a_temporary_home(tmp_path: Path, user_home: Path) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    (root / ".agents/skills/present").mkdir()
    _ = (root / ".agents/skills/present/SKILL.md").write_text("x")
    _ = infer(directory, root)
    _ = respond(request("discover", root, skill_name="present", skill_path=str(root / ".agents/skills/present")),
                bin_dir=directory)
    for call in logged(directory / "gemini.log"):
        assert call["home"] == [".gemini", ".gemini/oauth_creds.json@"]
        assert Path(str(call["home_dir"])) != user_home and not Path(str(call["home_dir"])).exists()
    assert (user_home / ".gemini/oauth_creds.json").read_text() == "private"


def test_discover_lists_every_skill_gemini_reports(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "gemini.mode").write_text("foreign")
    root = workspace(tmp_path)
    (root / ".agents/skills/present").mkdir()
    _ = (root / ".agents/skills/present/SKILL.md").write_text("x")
    (root / ".agents/skills/present-not-listed").mkdir()
    path = str(root / ".agents/skills/present")
    found = respond(request("discover", root, skill_name="present", skill_path=path), bin_dir=directory)
    assert found["skills"] == [{"name": "present", "path": path},
                               {"name": "other-skill", "path": str(root / ".agents/skills/other-skill")}]


def test_discover_does_not_answer_a_skill_that_has_no_skill_file(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    _ = (directory / "gemini.mode").write_text("foreign")
    found = respond(request("discover", root, skill_name="other-skill", skill_path=str(root / ".agents/skills/other-skill")),
                    bin_dir=directory)
    assert found["skills"] == []


def test_discover_rejects_a_skill_path_outside_the_workspace_skills(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    with pytest.raises(ValueError, match="skill_path must be"):
        _ = respond(request("discover", root, skill_name="present", skill_path="/etc"), bin_dir=directory)
    assert not (directory / "gemini.log").exists()


@pytest.mark.parametrize("raw", [
    "not json", "[]", json.dumps({"schema_version": 2, "operation": "sandbox", "workspace": "/tmp"}),
    json.dumps({"schema_version": 1, "operation": "frobnicate", "workspace": "/tmp"}),
    json.dumps({"schema_version": 1, "operation": "sandbox", "workspace": "relative"}),
    json.dumps({"schema_version": 1, "operation": "sandbox", "workspace": "/tmp", "argv": []}),
    json.dumps({"schema_version": 1, "operation": "discover", "workspace": "/tmp"}),
    json.dumps({"schema_version": 1, "operation": "infer", "workspace": "/tmp", "prompt": "p"}),
])
def test_protocol_errors_are_rejected_before_any_tool_runs(tmp_path: Path, raw: str) -> None:
    directory = fake_bin(tmp_path)
    with pytest.raises(ValueError):
        _ = respond(raw, bin_dir=directory)
    assert not (directory / "docker.log").exists() and not (directory / "gemini.log").exists()


@pytest.mark.parametrize("layout", ["plain/workspace", "skillz-x/other", "skillz-x/workspace", "skillz-a,b/workspace"])
def test_a_workspace_that_the_runner_did_not_make_is_refused(tmp_path: Path, layout: str) -> None:
    directory = fake_bin(tmp_path)
    root = tmp_path / layout
    root.mkdir(parents=True)
    with pytest.raises(ValueError, match="workspace"):
        _ = respond(request("sandbox", root, argv=["true"]), bin_dir=directory)
    assert not (root / ".agents").exists() and not (directory / "docker.log").exists()


def test_an_image_that_looks_like_an_option_is_refused(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    with pytest.raises(ValueError, match="--image"):
        _ = respond(request("sandbox", workspace(tmp_path), argv=["true"]), bin_dir=directory, image="--privileged")
    assert not (directory / "docker.log").exists()


def test_a_container_runtime_is_refused_on_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    directory = fake_bin(tmp_path)
    with pytest.raises(ValueError, match="not available on macOS"):
        _ = respond(request("sandbox", workspace(tmp_path), argv=["true"]), bin_dir=directory, runtime="docker")
    assert not (directory / "docker.log").exists()


def test_missing_linux_runtime_names_the_runtime_and_its_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(_gemini, "_extra_dirs", no_dirs)
    with pytest.raises(RuntimeError, match=r"missing docker, podman\). Fix: docker: install Docker Engine.*podman: install Podman"):
        _ = respond(request("sandbox", workspace(tmp_path), argv=["true"]))


def test_missing_macos_runtime_names_seatbelt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(_gemini, "_extra_dirs", no_dirs)
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match=r"missing sandbox-exec\). Fix: sandbox-exec: use macOS"):
        _ = respond(request("sandbox", workspace(tmp_path), argv=["true"]))


def test_a_sandbox_timeout_kills_the_named_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "docker.mode").write_text("hang")
    monkeypatch.setitem(_gemini.TIMEOUTS, "sandbox", 1)
    with pytest.raises(RuntimeError, match="timed out after 1 s"):
        _ = respond(request("sandbox", workspace(tmp_path), argv=["true"]), bin_dir=directory)
    run, kill = docker_calls(directory)
    assert kill == ["kill", run[run.index("--name") + 1]]


def test_an_infer_timeout_kills_the_labeled_container(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "gemini.mode").write_text("hang")
    monkeypatch.setitem(_gemini.TIMEOUTS, "infer", 1)
    with pytest.raises(RuntimeError, match="timed out after 1 s"):
        _ = infer(directory, workspace(tmp_path))
    listing, kill = docker_calls(directory)
    label = cast(dict[str, str], logged(directory / "gemini.log")[0]["env"])["SANDBOX_FLAGS"].split()[-1]
    assert listing == ["ps", "-q", "--filter", f"label={label}"] and kill == ["kill", "container-one"]


@SUBPROCESS_WRAPPER
def test_a_termination_signal_kills_the_named_container(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "docker.mode").write_text("hang")
    root = workspace(tmp_path)
    wrapper = subprocess.Popen([sys.executable, "-c", MAIN, "harness-wrapper", "gemini", "--bin-dir", str(directory)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert wrapper.stdin is not None
    _ = wrapper.stdin.write(request("sandbox", root, argv=["true"]))
    wrapper.stdin.close()
    deadline = time.monotonic() + 20
    while not (directory / "docker.log").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    wrapper.send_signal(signal.SIGTERM)
    _ = wrapper.communicate(timeout=30)
    run, kill = docker_calls(directory)
    assert kill == ["kill", run[run.index("--name") + 1]]


@SUBPROCESS_WRAPPER
def test_the_runner_deadline_runs_the_wrapper_stopper_before_it_kills_the_group(tmp_path: Path) -> None:
    from skillz_experiments._budget import BudgetExhausted
    from skillz_experiments._runtime import process

    directory = fake_bin(tmp_path)
    _ = (directory / "docker.mode").write_text("hang")
    root = workspace(tmp_path)
    command = [sys.executable, "-c", MAIN, "harness-wrapper", "gemini", "--bin-dir", str(directory)]
    with pytest.raises(BudgetExhausted):
        _ = process(command, cwd=root.parent, timeout=6, environment={"PATH": "/usr/bin:/bin"},
                    input_text=request("sandbox", root, argv=["true"]))
    run, kill = docker_calls(directory)
    assert kill == ["kill", run[run.index("--name") + 1]]


def test_the_sandbox_container_has_its_own_time_limit(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = respond(request("sandbox", workspace(tmp_path), argv=["true"]), bin_dir=directory)
    flags = docker_calls(directory)[0]
    start = flags.index("--entrypoint")
    assert flags[start + 1] == "/usr/bin/timeout"
    assert flags[start + 3:start + 6] == ["-s", "KILL", str(_gemini.TIMEOUTS["sandbox"] + _gemini.CONTAINER_GRACE)]
    assert flags[start + 6] == "/usr/bin/env"


def test_infer_selects_google_login_and_keeps_the_docker_context(tmp_path: Path, user_home: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = infer(directory, workspace(tmp_path))
    env = cast(dict[str, str], logged(directory / "gemini.log")[-1]["env"])
    assert env["GOOGLE_GENAI_USE_GCA"] == "true" and env["DOCKER_CONFIG"] == str(user_home / ".docker")


def test_a_parent_dotenv_cannot_set_the_keys_that_redirect_gemini(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = (tmp_path / ".env").write_text("GOOGLE_GEMINI_BASE_URL=http://evil.test\nSANDBOX_ENV=X=1\n")
    _ = infer(directory, workspace(tmp_path))
    env = cast(dict[str, str | None], logged(directory / "gemini.log")[-1]["env"])
    for name in _gemini.DOTENV_GUARDED:
        assert env[name] == "", name


def test_a_symlinked_workspace_is_checked_and_used_by_its_real_path(tmp_path: Path, user_home: Path) -> None:
    directory = fake_bin(tmp_path)
    (user_home / ".agents").mkdir()
    (tmp_path / "skillz-link").mkdir()
    (tmp_path / "skillz-link/workspace").symlink_to(user_home)
    with pytest.raises(ValueError, match="workspace"):
        _ = respond(request("sandbox", tmp_path / "skillz-link/workspace", argv=["true"]), bin_dir=directory)
    assert not (directory / "docker.log").exists()
    real = workspace(tmp_path)
    (tmp_path / "skillz-alias").mkdir()
    (tmp_path / "skillz-alias/workspace").symlink_to(real)
    _ = respond(request("sandbox", tmp_path / "skillz-alias/workspace", argv=["true"]), bin_dir=directory)
    mounts = [item for item in docker_calls(directory)[0] if item.startswith("type=bind")]
    assert mounts[0] == f"type=bind,source={real},target={real}"


def test_a_symlinked_agents_directory_is_refused(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    root = tmp_path / "skillz-x/workspace"
    root.mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (root / ".agents").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="workspace"):
        _ = respond(request("sandbox", root, argv=["true"]), bin_dir=directory)


def test_discover_answers_with_the_runner_path_of_a_symlinked_workspace(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    real = workspace(tmp_path)
    (real / ".agents/skills/present").mkdir()
    _ = (real / ".agents/skills/present/SKILL.md").write_text("x")
    (tmp_path / "skillz-alias").mkdir()
    alias = tmp_path / "skillz-alias/workspace"
    alias.symlink_to(real)
    path = str(alias / ".agents/skills/present")
    found = respond(request("discover", alias, skill_name="present", skill_path=path), bin_dir=directory)
    assert found["skills"] == [{"name": "present", "path": path}]


def shell_events(output: object, status: str = "success") -> list[dict[str, object]]:
    result: dict[str, object] = {"type": "tool_result", "tool_id": "t", "status": status}
    if output is not None:
        result["output"] = output
    return [{"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "t", "parameters": {"command": "helper"}}, result]


@pytest.mark.parametrize(("output", "status", "code"), [
    ("done", "success", 0), (None, "success", 0),
    ("Output: boom\nExit Code: 2", "success", 2), ("Exit Code: 1\nmore\nExit Code: 3", "success", 3),
    ("Exit Code: 0", "success", 0), ("Exit Code: soon", "success", 1), ("Exit Code: -1", "success", 1),
    ("Exit Code: 0", "error", 1), ("Exit Code: 7", "error", 7), ("no line", "error", 1),
])
def test_trace_exit_code_comes_from_the_shell_output_line(output: object, status: str, code: int) -> None:
    assert _gemini._trace(shell_events(output, status)) == [{"argv": ["sh", "-c", "helper"], "exit_code": code}]  # pyright: ignore[reportPrivateUsage]


def test_macos_profile_denies_agents_writes_after_every_allow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    _ = (directory / "sandbox-exec").write_text(f"#!{sys.executable}\n")
    (directory / "sandbox-exec").chmod(0o700)
    _ = infer(directory, root)
    env = cast(dict[str, str], logged(directory / "gemini.log")[-1]["env"])
    assert env["SEATBELT_PROFILE"] == "skillz" and env["GOOGLE_GENAI_USE_GCA"] == "true"
    text = (root.resolve() / ".gemini/sandbox-macos-skillz.sb").read_text()
    assert text.startswith("(version 1)\n(deny default)") and '(subpath (param "TARGET_DIR"))' in text
    assert text.rstrip().splitlines()[-1] == f'(deny file-write* (subpath "{root.resolve() / ".agents"}"))'
    assert (root / ".gemini/sandbox-macos-skillz.sb").stat().st_mode & 0o077 == 0


def test_infer_gives_gemini_a_private_temp_directory(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = infer(directory, workspace(tmp_path))
    call = logged(directory / "gemini.log")[-1]
    scratch = Path(cast(dict[str, str], call["env"])["TMPDIR"])
    assert call["tmp_is_dir"] is True and scratch.name.startswith("skillz-gemini-tmp-") and not scratch.exists()
    assert scratch.parent != Path(str(call["home_dir"]))


def first_dotenv(start: Path) -> Path | None:
    """Return the `.env` that Gemini CLI loads: the first `.gemini/.env` or `.env` at or above `start`."""
    for folder in (start, *start.parents):
        for candidate in (folder / ".gemini/.env", folder / ".env"):
            if candidate.is_file():
                return candidate
    return None


@pytest.mark.parametrize("operation", ["infer", "discover"])
def test_gemini_finds_an_empty_dotenv_before_a_shared_one(tmp_path: Path, operation: str) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    _ = (tmp_path / ".env").write_text("GEMINI_API_KEY=planted\nGOOGLE_CLOUD_PROJECT=planted\n")
    if operation == "infer":
        _ = infer(directory, root)
    else:
        (root / ".agents/skills/present").mkdir()
        _ = (root / ".agents/skills/present/SKILL.md").write_text("x")
        _ = respond(request("discover", root, skill_name="present", skill_path=str(root / ".agents/skills/present")),
                    bin_dir=directory)
    found = first_dotenv(Path(str(logged(directory / "gemini.log")[-1]["cwd"])))
    assert found is not None and found.read_text() == ""
    assert found.parent == root.resolve().parent


@pytest.mark.parametrize("operation", ["infer", "discover"])
@pytest.mark.parametrize("local", ["." + "env", ".gemini/" + "." + "env"])
@pytest.mark.parametrize("link", [False, True])
def test_a_workspace_dotenv_file_is_refused_before_gemini_runs(
        tmp_path: Path, operation: str, local: str, link: bool) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    planted = root / local
    planted.parent.mkdir(exist_ok=True)
    if link:
        target = tmp_path / "elsewhere"
        _ = target.write_text("GEMINI_API_KEY=planted")
        planted.symlink_to(target)
    else:
        _ = planted.write_text("GEMINI_API_KEY=planted")
    with pytest.raises(ValueError, match="workspace must not contain"):
        if operation == "infer":
            _ = infer(directory, root)
        else:
            _ = respond(request("discover", root, skill_name="present", skill_path=str(root / ".agents/skills/present")),
                        bin_dir=directory)
    assert not (directory / "gemini.log").exists()

def test_macos_sandbox_runs_the_command_under_the_shared_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = fake_bin(tmp_path)
    root = workspace(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    log = tmp_path / "sandbox-exec.log"
    _ = (directory / "sandbox-exec").write_text(
        f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n")
    (directory / "sandbox-exec").chmod(0o700)
    _ = respond(request("sandbox", root, argv=["true"]), bin_dir=directory)
    argv = cast(list[str], json.loads(log.read_text()))
    assert argv[:2] == ["-p", command_profile(root.resolve())]
    assert argv[2:] == ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin", f"HOME={root.resolve()}", f"TMPDIR={root.resolve()}",
                        "true"]


def test_a_host_fault_while_starting_gemini_is_a_runtime_error(tmp_path: Path) -> None:
    directory = fake_bin(tmp_path)
    _ = (directory / "gemini").write_text("#!/nonexistent/interpreter\n")
    with pytest.raises(RuntimeError, match="host fault while running infer"):
        _ = infer(directory, workspace(tmp_path))


def test_command_line_reports_a_bad_request_as_json_error() -> None:
    result = subprocess.run([sys.executable, "-c", MAIN, "harness-wrapper", "gemini"], input="not json",
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1 and result.stdout == ""
    assert str(cast(dict[str, object], json.loads(result.stderr))["error"]).endswith("request is not JSON")


def test_command_line_refuses_an_unknown_runtime() -> None:
    result = subprocess.run([sys.executable, "-c", MAIN, "harness-wrapper", "gemini", "--runtime", "lxc"], input="{}",
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0 and result.stdout == ""
