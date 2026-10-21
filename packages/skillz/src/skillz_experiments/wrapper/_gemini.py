"""Reference wrapper that runs Gemini CLI behind the command adapter (protocol version 1).

The runner starts this module with a stripped environment (`PATH` only), so the wrapper finds its tools
through a short list of install directories. Gemini CLI has no "run this argv" mode. The `sandbox`
operation therefore runs the argv in its own hardened container, built from the image that Gemini CLI uses.
The `infer` operation runs Gemini CLI in its native sandbox and passes the restrictions that Gemini documents.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from typing import Literal, NoReturn, cast

from skillz_experiments._cases import loads_untrusted, mapping
from skillz_experiments._protocol import OPERATIONS, USAGE_KEYS, VERSION, argv_strings
from skillz_experiments._redact import redact
from skillz_experiments.harness import command_profile
from skillz_experiments.wrapper._seatbelt import profile as seatbelt_profile

Runtime = Literal["docker", "podman", "runsc", "sandbox-exec"]
IMAGE = "ghcr.io/google/gemini-cli:latest"
CONTAINER_ENV = ["-i", "PATH=/usr/bin:/bin", "HOME=/tmp", "TMPDIR=/tmp"]
LIMITS = ["--pids-limit", "256", "--memory", "2g"]
LABEL = "skillz-run"
CREDENTIALS = ("oauth_creds.json",)
SEATBELT_PROFILE = "skillz"
PROFILE_FILE = f"sandbox-macos-{SEATBELT_PROFILE}.sb"
TIMEOUTS = {"sandbox": 300, "discover": 60, "infer": 900}
CONTAINER_GRACE = 10
STOP_BUDGET = 6
DOTENV = "." + "env"
PROMPT_FLAG = "Follow the request on standard input."
EXIT_LINE = re.compile(r"^Exit Code: (.*)$", re.MULTILINE)
DOTENV_GUARDED = ("GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL", "GEMINI_SANDBOX_PROXY_COMMAND", "SANDBOX_ENV",
                  "SANDBOX_PORTS", "BUILD_SANDBOX", "SANDBOX_SET_UID_GID", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                  "http_proxy")
INSTALL = {
    "docker": "install Docker Engine and start it (https://docs.docker.com/engine/install/)",
    "podman": "install Podman (https://podman.io/docs/installation)",
    "runsc": "install gVisor runsc and register it as a Docker runtime (https://gvisor.dev/docs/user_guide/install/)",
    "sandbox-exec": "use macOS, where /usr/bin/sandbox-exec ships with the system",
}
SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LISTED_SKILL = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|:|\[|$)")
TOOL_ARGV: dict[str, tuple[str, Callable[[str], list[str]]]] = {
    "run_shell_command": ("command", lambda value: ["sh", "-c", value]),
    "read_file": ("file_path", lambda value: ["cat", value])}


def _tail(text: str) -> str:
    """Return the last 300 characters of a process's stderr, without URLs or credential-like words and values."""
    return redact(text.strip()[-300:])


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _extra_dirs() -> list[Path]:
    return [Path("/usr/local/bin"), Path("/opt/homebrew/bin"), _home() / ".local/bin", _home() / ".npm-global/bin"]


def _search(bin_dir: Path | None) -> list[str]:
    inherited = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    return [str(path) for path in ([bin_dir] if bin_dir else [])] + inherited + [str(path) for path in _extra_dirs()]


def _which(name: str, bin_dir: Path | None) -> str | None:
    return shutil.which(name, path=os.pathsep.join(_search(bin_dir)))


def _runtime(requested: str | None, bin_dir: Path | None) -> tuple[str, str]:
    """Return the sandbox kind and the executable that starts it, or stop with the missing runtime and its fix."""
    if sys.platform == "darwin":
        if requested not in (None, "sandbox-exec"):
            raise ValueError(f"--runtime {requested} is not available on macOS; use sandbox-exec or omit it")
        kinds = ["sandbox-exec"]
    elif requested is not None:
        kinds = [requested]
    else:
        kinds = ["docker", "podman"]
    for kind in kinds:
        executable = _which("docker" if kind == "runsc" else kind, bin_dir)
        if executable is not None and (kind != "runsc" or _which("runsc", bin_dir) is not None):
            return kind, executable
    fixes = "; or ".join(f"{kind}: {INSTALL[kind]}" for kind in kinds)
    raise RuntimeError("no sandbox runtime for Gemini CLI found (missing " + ", ".join(kinds)
                       + "). Fix: " + fixes + ". The wrapper does not drive LXC.")


def _gemini(bin_dir: Path | None) -> str:
    found = _which("gemini", bin_dir)
    if found is None:
        raise RuntimeError("gemini CLI not found; fix: npm install -g @google/gemini-cli, then sign in once with `gemini`")
    return found


def _interrupted(signum: int, _frame: FrameType | None) -> NoReturn:
    raise SystemExit(128 + signum)


def _abandon(process: subprocess.Popen[str], stop: Callable[[], str] | None) -> str:
    """Kill the process group, then ask the container runtime to stop the container that the group started.

    Return a note when the cleanup may have left a container running. The note also goes to stderr.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    _ = process.communicate()
    note = stop() if stop is not None else ""
    if note:
        print(note, file=sys.stderr, flush=True)
    return note


def _run(command: list[str], workspace: Path, env: dict[str, str], *, timeout: float, stdin: str | None = None,
         stop: Callable[[], str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run one command with a deadline. A timeout, SIGTERM, or interrupt kills the group and calls `stop`."""
    try:
        previous = signal.signal(signal.SIGTERM, _interrupted)
    except ValueError:
        previous = None
    process = subprocess.Popen(command, cwd=workspace, env=env, text=True, errors="replace", start_new_session=True,
                               stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        note = _abandon(process, stop)
        raise RuntimeError(f"{Path(command[0]).name} timed out after {timeout:g} s" + (f"; {note}" if note else "")) from None
    except BaseException:
        _ = _abandon(process, stop)
        raise
    finally:
        if previous is not None:
            _ = signal.signal(signal.SIGTERM, previous)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _stopper(executable: str, env: dict[str, str], *selector: str) -> Callable[[], str]:
    """Return a callback that kills containers. `("kill", NAME)` kills one name; `("ps", FILTER)` kills a label match.

    All docker calls share one deadline of `STOP_BUDGET` seconds, so the callback ends before the runner's SIGKILL.
    The callback returns an empty string when every call worked. Otherwise it returns a note that names the failure.
    """
    def stop() -> str:
        problems: list[str] = []
        deadline = time.monotonic() + STOP_BUDGET

        def call(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([executable, *arguments], env=env, text=True, capture_output=True,
                                  timeout=max(deadline - time.monotonic(), 0.1), check=False)
        try:
            if selector[0] == "kill":
                ids = [selector[1]]
            else:
                listing = call("ps", "-q", "--filter", selector[1])
                if listing.returncode:
                    problems.append(f"docker ps exits {listing.returncode}: {_tail(listing.stderr)}")
                ids = listing.stdout.split()
            for identifier in ids:
                killed = call("kill", identifier)
                if killed.returncode:
                    problems.append(f"docker kill {identifier} exits {killed.returncode}: {_tail(killed.stderr)}")
        except (OSError, subprocess.TimeoutExpired) as error:
            problems.append(f"{type(error).__name__}: {error}")
        if not problems:
            return ""
        return (f"container cleanup failed for {selector[1]} ({'; '.join(problems)}); a container may still be running: "
                f"run `{Path(executable).name} ps` and kill it")
    return stop


def _environment(executable: str, bin_dir: Path | None, home: Path | None = None, **extra: str) -> dict[str, str]:
    """Return the child environment. Gemini loads the first `.env` above the workspace for every key that the
    environment does not hold, so the keys that redirect traffic or sandbox start-up are present and empty.
    """
    path = os.pathsep.join([str(Path(executable).parent), *_search(bin_dir)])
    return {**dict.fromkeys(DOTENV_GUARDED, ""), "PATH": path, "HOME": str(home or _home()), **extra}


def _stop_dotenv_search(root: Path) -> None:
    """Refuse a workspace that holds its own dotenv file or `.gemini` content, then write an empty dotenv file in the
    runner directory. Workspace `.gemini` settings can widen the sandbox, so only the wrapper's own profile may exist.

    Gemini loads only the first dotenv file that it finds at or above its working directory. A file in the workspace or in
    its `.gemini` directory (even a symlink) would load before the guard and could carry credentials. The guard stops the
    search before a shared directory such as `/tmp`, where any local user can write a dotenv file.
    """
    if os.path.lexists(root / DOTENV):
        raise ValueError(f"workspace must not contain {DOTENV}; Gemini CLI would load it")
    directory = root / ".gemini"
    if os.path.lexists(directory):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("workspace must not contain .gemini; Gemini CLI would load its settings")
        for entry in directory.iterdir():
            if entry.name != PROFILE_FILE:
                raise ValueError(f"workspace must not contain .gemini/{entry.name}; Gemini CLI would load it")
    guard = root.parent / DOTENV
    guard.unlink(missing_ok=True)
    _ = guard.write_text("")


@contextmanager
def _login_home() -> Generator[Path]:
    """Yield a temporary HOME that links only the Gemini login files, so no user setting or skill loads."""
    with tempfile.TemporaryDirectory(prefix="skillz-gemini-home-") as directory:
        home = Path(directory)
        for name in CREDENTIALS:
            source = _home() / ".gemini" / name
            if source.is_file():
                (home / ".gemini").mkdir(exist_ok=True)
                (home / ".gemini" / name).symlink_to(source)
        yield home


def _sandbox(request: dict[str, object], workspace: Path, runtime: str | None, image: str,
             bin_dir: Path | None) -> dict[str, object]:
    command = argv_strings(request.get("argv"))
    if command is None:
        raise ValueError("sandbox request needs a non-empty argv of strings")
    kind, executable = _runtime(runtime, bin_dir)
    root = workspace.resolve()
    agents = root / ".agents"
    env = _environment(executable, bin_dir)
    stop = None
    if kind == "sandbox-exec":
        launch = [executable, "-p", command_profile(root),
                  "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", f"HOME={root}", f"TMPDIR={root}", *command]
    else:
        name = f"skillz-{secrets.token_hex(6)}"
        flags = ["--runtime=runsc"] if kind == "runsc" else []
        user = ["--user", f"{os.getuid()}:{os.getgid()}"] if executable.endswith("docker") else []
        launch = [executable, "run", "--rm", "--name", name, "--network", "none", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges", *LIMITS, *flags, *user,
                  "--mount", f"type=bind,source={root},target={root}",
                  "--mount", f"type=bind,source={agents},target={agents},readonly", "-w", str(root),
                  "--entrypoint", "/usr/bin/timeout", image, "-s", "KILL", str(TIMEOUTS["sandbox"] + CONTAINER_GRACE),
                  "/usr/bin/env", *CONTAINER_ENV, *command]
        stop = _stopper(executable, env, "kill", name)
    result = _run(launch, root, env, timeout=TIMEOUTS["sandbox"], stop=stop)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def _listed(output: str) -> list[str]:
    """Return the skill names that start a line of `gemini skills list` output. Indented lines are details."""
    names: list[str] = []
    for line in output.splitlines():
        found = LISTED_SKILL.match(line) if line and not line[0].isspace() else None
        if found is not None and found.group(1) not in names:
            names.append(found.group(1))
    return names


def _skill_listing(request: dict[str, object], workspace: Path, bin_dir: Path | None) -> dict[str, object]:
    name, path = request.get("skill_name"), request.get("skill_path")
    if not isinstance(name, str) or SKILL_NAME.fullmatch(name) is None or not isinstance(path, str):
        raise ValueError("discover request needs skill_name and skill_path")
    skills = workspace / ".agents/skills"
    requested = Path(path)
    if ".." in requested.parts or requested.resolve() != skills / name:
        raise ValueError("skill_path must be <workspace>/.agents/skills/<skill_name>")
    gemini = _gemini(bin_dir)
    with _login_home() as home:
        result = _run([gemini, "skills", "list"], workspace, _environment(gemini, bin_dir, home),
                      timeout=TIMEOUTS["discover"])
    if result.returncode:
        raise RuntimeError(f"`gemini skills list` exits {result.returncode}: {_tail(result.stderr)}")
    present = (skills / name / "SKILL.md").is_file()
    return {"skills": [{"name": item, "path": str(requested.parent / item)} for item in _listed(result.stdout)
                       if item != name or present]}


def _stream(text: str) -> list[dict[str, object]]:
    """Return the JSON objects of a JSON Lines stream. Other lines (banners, partial output) are skipped."""
    events: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("{"):
            try:
                events.append(mapping(loads_untrusted(line)))
            except ValueError:
                continue
    return events


def _exit_code(name: str, event: dict[str, object]) -> int:
    """Return the exit code that a `tool_result` shows. Gemini reports a shell command that ran as `success`
    and appends `Exit Code: N` to the output when N is not 0, so read the last such line.
    """
    success = event.get("status") == "success"
    output = event.get("output")
    found = cast(list[str], EXIT_LINE.findall(output)) if name == "run_shell_command" and isinstance(output, str) else []
    if not found:
        return 0 if success else 1
    text = found[-1].strip()
    if not text.isascii() or not text.isdigit():
        return 1
    return int(text) if success or int(text) else 1


def _trace(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Map completed Gemini shell and file-read tool calls to trace entries. Report only what Gemini reported."""
    calls: dict[str, tuple[str, dict[str, object]]] = {}
    trace: list[dict[str, object]] = []
    for event in events:
        identifier, kind = event.get("tool_id"), event.get("type")
        if not isinstance(identifier, str):
            continue
        if kind == "tool_use":
            calls[identifier] = (str(event.get("tool_name")), _fields(event.get("parameters")))
        elif kind == "tool_result" and identifier in calls:
            name, parameters = calls[identifier]
            field = TOOL_ARGV.get(name)
            value = parameters.get(field[0]) if field is not None else None
            if field is not None and isinstance(value, str) and value:
                trace.append({"argv": field[1](value), "exit_code": _exit_code(name, event)})
    return trace


def _answer(text: str) -> dict[str, object]:
    body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return mapping(loads_untrusted(body))
    except ValueError:
        raise RuntimeError("gemini answer is not a JSON object") from None


def _fields(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _usage(stats: object) -> dict[str, int] | None:
    """Sum per-model token counts. Return None when the stats carry none, so the runner records no usage."""
    sources = ("input_tokens", "cached", "output_tokens")
    blocks = [_fields(model) for model in _fields(_fields(stats).get("models")).values()] or [_fields(stats)]
    totals: dict[str, int] = {}
    for block in blocks:
        for source, target in zip(sources, USAGE_KEYS, strict=True):
            value = block.get(source)
            if type(value) is int and value >= 0:
                totals[target] = totals.get(target, 0) + value
    return totals or None


def _restrictions(kind: str, root: Path, label: str) -> dict[str, str]:
    """Return the environment that passes the probe's restrictions to Gemini CLI's own sandbox.

    On macOS the stock profiles leave `.agents` writable. This writes a custom profile into the workspace.
    Gemini CLI loads it from `<workspace>/.gemini/` when `SEATBELT_PROFILE` names no built-in profile.
    """
    if kind == "sandbox-exec":
        directory = root / ".gemini"
        if directory.is_symlink():
            raise ValueError("workspace .gemini must not be a symlink")
        directory.mkdir(exist_ok=True)
        profile = directory / PROFILE_FILE
        profile.unlink(missing_ok=True)
        _ = profile.write_text(seatbelt_profile(str(root / ".agents")))
        profile.chmod(0o600)
        return {"SEATBELT_PROFILE": SEATBELT_PROFILE}
    agents = root / ".agents"
    flags = ["--cap-drop", "ALL", "--security-opt", "no-new-privileges", *LIMITS, "--label", label]
    return {"SANDBOX_FLAGS": " ".join(flags), "SANDBOX_MOUNTS": f"{agents}:{agents}:ro"}


def _infer(request: dict[str, object], workspace: Path, runtime: str | None, image: str,
           bin_dir: Path | None) -> dict[str, object]:
    prompt, schema, model = request.get("prompt"), request.get("response_schema"), request.get("model")
    if not isinstance(prompt, str) or not isinstance(schema, dict) or not isinstance(model, str):
        raise ValueError("infer request needs prompt, response_schema, and model")
    if not model or model.startswith("-"):
        raise ValueError("model must be a model name that does not start with '-'")
    kind, executable = _runtime(runtime, bin_dir)
    gemini = _gemini(bin_dir)
    root = workspace.resolve()
    label = f"{LABEL}=skillz-{secrets.token_hex(6)}"
    full = (f"{prompt}\n\nReply with one JSON object that matches this JSON schema and no other text:\n"
            + json.dumps(schema))
    command = [gemini, "-s", "--approval-mode=yolo", "-o", "stream-json", "-m", model, "-p", PROMPT_FLAG]
    extra = {"GEMINI_SANDBOX": kind, "GOOGLE_GENAI_USE_GCA": "true", **_restrictions(kind, root, label)}
    if kind != "sandbox-exec":
        extra["GEMINI_SANDBOX_IMAGE"] = image
        extra["DOCKER_CONFIG"] = str(_home() / ".docker")
    # A private TMPDIR: Gemini gives its sandbox write access to `os.tmpdir()`, which is otherwise the shared /tmp.
    with _login_home() as home, tempfile.TemporaryDirectory(prefix="skillz-gemini-tmp-") as scratch:
        env = _environment(gemini, bin_dir, home, **extra, TMPDIR=scratch)
        stop = None if kind == "sandbox-exec" else _stopper(executable, env, "ps", f"label={label}")
        result = _run(command, workspace, env, timeout=TIMEOUTS["infer"], stdin=full, stop=stop)
    if result.returncode:
        raise RuntimeError(f"gemini exits {result.returncode}: {_tail(result.stderr)}")
    events = _stream(result.stdout)
    ended = [event for event in events if event.get("type") == "result"]
    if ended and ended[-1].get("status") == "error":
        raise RuntimeError(f"gemini reports an error: {json.dumps(ended[-1].get('error'))[:300]}")
    text = "".join(content for event in events if event.get("type") == "message" and event.get("role") == "assistant"
                   and isinstance(content := event.get("content"), str))
    if not text.strip():
        raise RuntimeError("gemini printed no assistant answer")
    stats = ended[-1].get("stats") if ended else None
    return {"answer": _answer(text), "trace": _trace(events), "usage": _usage(stats)}


def _checked_workspace(value: object) -> Path:
    """Resolve the path first, then accept only a runner workspace: `<temporary>/skillz-*/workspace` with its own `.agents`.

    Every later check and mount uses the returned resolved path, so a symlink cannot move the mount.
    """
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("workspace must be an existing absolute directory")
    root = Path(value).resolve()
    agents = root / ".agents"
    if root.name != "workspace" or not root.parent.name.startswith("skillz-") or agents.is_symlink() or not agents.is_dir():
        raise ValueError("workspace must be a runner workspace: skillz-*/workspace with an .agents directory")
    if any(char in str(root) for char in ',:"\n\\'):
        raise ValueError("workspace path must not contain a comma, colon, quote, backslash, or newline")
    return root


def respond(raw: str, *, bin_dir: Path | None = None, runtime: str | None = None, image: str = IMAGE) -> dict[str, object]:
    """Answer one protocol-version-1 request. Raise ValueError for a bad request and RuntimeError for a host fault."""
    try:
        asked = mapping(loads_untrusted(raw))
    except json.JSONDecodeError:
        raise ValueError("request is not JSON") from None
    except ValueError:
        raise ValueError("request must be a JSON object") from None
    operation = asked.get("operation")
    if type(asked.get("schema_version")) is not int or asked["schema_version"] != VERSION:
        raise ValueError(f"unsupported schema_version; this wrapper speaks version {VERSION}")
    if operation not in OPERATIONS:
        raise ValueError(f"unknown operation {operation!r}")
    if not image or image.startswith("-"):
        raise ValueError("--image must be an image name that does not start with '-'")
    root = _checked_workspace(asked.get("workspace"))
    try:
        if operation == "sandbox":
            body = _sandbox(asked, root, runtime, image, bin_dir)
        elif operation == "discover":
            _stop_dotenv_search(root)
            body = _skill_listing(asked, root, bin_dir)
        else:
            _stop_dotenv_search(root)
            body = _infer(asked, root, runtime, image, bin_dir)
    except OSError as error:
        raise RuntimeError(f"host fault while running {operation}: {error.strerror or error}") from None
    return {"schema_version": VERSION, "operation": operation, **body}
