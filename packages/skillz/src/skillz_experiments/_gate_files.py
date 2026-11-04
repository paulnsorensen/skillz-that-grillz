"""Resolve `gate` file references in a raw contract into the inline `argv` form.

A kind that declares `"gate": "evals/gates/<name>.py"` becomes `"argv": ["python3", "-I", "-c", SOURCE]`.
The run freezes that argv in `run.json`, so resume and export never read the target again.
"""
from __future__ import annotations

import ast
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Collection
from pathlib import Path, PurePosixPath
from typing import NamedTuple, cast

from skillz_experiments._cases import CodedError, read_text, relative
from skillz_experiments._runtime import _end  # pyright: ignore[reportPrivateUsage]

GATE_GRADERS = ("command", "hybrid", "journey")
SOURCE_LIMIT = 64 * 1024
_PREFIX = ("python3", "-I", "-c")
FIXTURE_SECONDS = 20.0
OUTPUT_LIMIT = 64 * 1024
FIXTURE_FILES = 200
FIXTURE_BYTES = 4 * 1024 * 1024
NULL_TRACE = '{"type": "assistant", "message": {"content": []}}\n{"type": "result"}\n'
_STUB = ("import sys\nimport types\n_SIBLINGS = {siblings}\n_MAIN = {main}\n"
         + "for _name, _source in _SIBLINGS:\n    _module = types.ModuleType(_name)\n    sys.modules[_name] = _module\n"
         + "    exec(compile(_source, _name + '.py', 'exec'), _module.__dict__)\n"
         + "_gate = types.ModuleType('__main__')\nsys.modules['__main__'] = _gate\n"
         + "exec(compile(_MAIN, 'gate.py', 'exec'), _gate.__dict__)\n")


def _invalid(message: str) -> CodedError:
    return CodedError("contract-gate-invalid", message)


def _read(root: Path, name: str) -> str:
    try:
        return read_text(root, name, SOURCE_LIMIT)
    except ValueError as error:
        raise _invalid(f"gate file {error}") from None


def _imports(source: str, name: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise _invalid(f"gate {name} is not valid Python: {error}") from None
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                raise _invalid(f"gate {name} uses a relative import; import a sibling module by its plain name")
            found.add(node.module)
    return found


def _bundle(root: Path, gate: str) -> tuple[str, list[str]]:
    """Return the inline source and every gate file that it reads.

    A gate with no sibling imports is its own source. Otherwise the source is a small stub that registers each
    sibling module, dependencies first, and runs the gate as `__main__`.
    """
    folder = PurePosixPath(gate).parent
    main = _read(root, gate)
    siblings: dict[str, str] = {}
    order: list[str] = []

    def visit(name: str, source: str, stack: tuple[str, ...]) -> None:
        for module in sorted(_imports(source, name)):
            top = module.split(".")[0]
            path = (folder / f"{top}.py").as_posix()
            if top in sys.stdlib_module_names:
                if path != gate and os.path.lexists(root / path):
                    raise _invalid(f"gate sibling {path} shadows the standard library module {top}")
                continue
            if module != top or not os.path.lexists(root / path):
                raise _invalid(f"gate {name} imports {module}: only the standard library and sibling modules in {folder} are allowed")
            if top in stack:
                raise _invalid(f"gate modules import each other in a cycle at {top}")
            if top in siblings:
                continue
            siblings[top] = _read(root, path)
            visit(top, siblings[top], (*stack, top))
            order.append(top)

    visit(gate, main, (PurePosixPath(gate).stem,))
    paths = [gate, *(f"{(folder / name).as_posix()}.py" for name in order)]
    if not order:
        return main, paths
    stub = _STUB.format(siblings=repr([(name, siblings[name]) for name in order]), main=repr(main))
    return stub, paths


def _expand(root: Path, item: dict[str, object], kind: str, used: set[str]) -> dict[str, object]:
    if item.get("grader") not in GATE_GRADERS:
        raise _invalid(f"kind {kind} grader does not take a gate")
    if "argv" in item:
        raise _invalid(f"kind {kind} declares both gate and argv; declare one")
    gate = item["gate"]
    try:
        if not isinstance(gate, str):
            raise ValueError("gate must be text")
        _ = relative(gate)
    except ValueError:
        raise _invalid(f"kind {kind} gate must be a relative path under evals/ that ends with .py") from None
    if not gate.startswith("evals/") or not gate.endswith(".py"):
        raise _invalid(f"kind {kind} gate must be a relative path under evals/ that ends with .py")
    source, paths = _bundle(root, gate)
    if len(source.encode("utf-8")) > SOURCE_LIMIT:
        raise _invalid(f"kind {kind} gate source is larger than {SOURCE_LIMIT} bytes")
    used.update(paths)
    return {key: value for key, value in item.items() if key != "gate"} | {"argv": [*_PREFIX, source]}


def expand_gates(raw: object, root: Path) -> object:
    """Return `raw` with each kind `gate` path replaced by the inline `argv` that runs the gate source.

    `root` is the directory that holds `evals/`. Raise `contract-gate-invalid` for an unusable gate and
    `contract-gate-editable` when `editable` names a gate file. Other input passes through unchanged.
    """
    if not isinstance(raw, dict):
        return raw
    document = cast(dict[str, object], raw)
    kinds = document.get("kinds")
    if not isinstance(kinds, dict):
        return document
    used: set[str] = set()
    expanded: dict[str, object] = {}
    for kind, entry in cast(dict[str, object], kinds).items():
        expanded[kind] = (_expand(root, cast(dict[str, object], entry), kind, used)
                          if isinstance(entry, dict) and "gate" in entry else entry)
    editable = document.get("editable")
    if isinstance(editable, list):
        for name in cast(list[object], editable):
            if name in used:
                raise CodedError("contract-gate-editable", f"editable names the gate file {name}; a candidate cannot edit its own grader")
    return document | {"kinds": expanded}


class GateFinding(NamedTuple):
    code: str
    severity: str
    gate: str
    message: str
    fix: str


def fixtures_of(gate: str) -> str:
    """Return the fixture directory that sits beside a gate file: `evals/gates/x.py` -> `evals/gates/x.fixtures`."""
    path = PurePosixPath(gate)
    return path.with_name(f"{path.stem}.fixtures").as_posix()


def _unsafe(root: Path, fixture: Path) -> str | None:
    """Return why a fixture directory is unsafe to copy, or None. A symlink anywhere on its path or in its tree is unsafe."""
    here = root
    for part in fixture.relative_to(root).parts:
        here = here / part
        if here.is_symlink():
            return f"{here.relative_to(root).as_posix()} is a symbolic link"
    files = size = 0
    for folder, names, leaves in os.walk(fixture):
        for name in (*names, *leaves):
            path = Path(folder, name)
            if path.is_symlink():
                return f"{path.relative_to(root).as_posix()} is a symbolic link"
        for name in leaves:
            files += 1
            size += Path(folder, name).stat().st_size
        if files > FIXTURE_FILES or size > FIXTURE_BYTES:
            return f"it holds more than {FIXTURE_FILES} files or {FIXTURE_BYTES} bytes"
    return None


def _limit_file_size() -> None:
    _ = resource.setrlimit(resource.RLIMIT_FSIZE, (FIXTURE_BYTES, FIXTURE_BYTES))


def _run(source: str, work: Path, home: str) -> tuple[int, bytes] | None:
    """Run the gate source in `work` with a minimal environment. Return its exit code and the first bytes of stdout, None on a timeout.

    The child leads its own process group, and every exit path ends that group, so no descendant outlives the run.
    """
    command = [shutil.which(_PREFIX[0]) or sys.executable, *_PREFIX[1:], source]
    environment = {"PATH": os.defpath, "LC_ALL": "C.UTF-8", "HOME": home}
    with tempfile.TemporaryFile() as out:
        with subprocess.Popen(command, cwd=work, env=environment, text=True, stdin=subprocess.DEVNULL, stdout=out,
                              stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=_limit_file_size) as child:
            try:
                _ = child.wait(timeout=FIXTURE_SECONDS)
            except subprocess.TimeoutExpired:
                return None
            finally:
                _end(child)
        _ = out.seek(0)
        return child.returncode, out.read(OUTPUT_LIMIT + 1)


def _score(source: str, fixture: Path | None, trace: str | None = None) -> float | None:
    """Run the gate source once in a fresh temporary workspace. Return its score, 0 for a failed run, None on a timeout."""
    with tempfile.TemporaryDirectory() as name:
        work = Path(name, "work")
        work.mkdir()
        if fixture is not None:
            for part in ("trace", "output"):
                if (fixture / part).is_dir():
                    _ = shutil.copytree(fixture / part, work / part, symlinks=True)
        if trace is not None:
            (work / "trace").mkdir()
            _ = (work / "trace/events.jsonl").write_text(trace, encoding="utf-8")
        done = _run(source, work, name)
    if done is None:
        return None
    code, output = done
    if code != 0 or len(output) > OUTPUT_LIMIT:
        return 0.0
    try:
        score = cast(dict[str, object], json.loads(output.decode("utf-8", errors="replace")))["score"]
    except (ValueError, KeyError, TypeError):
        return 0.0
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return 0.0
    return float(score)


def probe_gates(root: Path, raw: object, must_kinds: Collection[str]) -> list[GateFinding]:
    """Run each gate of a raw contract on its `pass` and `fail` fixtures, and on a null trace for a journey kind with `must` items.

    A fixture is `<gate stem>.fixtures/{pass,fail}/` with `trace/events.jsonl` and `output/`, in either harness encoding.
    Fixtures are optional: a missing one is a warning. A fixture with a symbolic link or too many files is an error.
    `root` is the directory that holds `evals/`.
    """
    kinds = cast(dict[str, object], raw).get("kinds") if isinstance(raw, dict) else None
    gates: dict[str, list[str]] = {}
    for kind, entry in (cast(dict[str, object], kinds).items() if isinstance(kinds, dict) else ()):
        if isinstance(entry, dict) and isinstance(cast(dict[str, object], entry).get("gate"), str):
            gates.setdefault(str(cast(dict[str, object], entry)["gate"]), []).append(kind)
    found: list[GateFinding] = []

    def timed_out(gate: str, what: str) -> None:
        found.append(GateFinding("gate-fixture-timeout", "error", gate,
                                 f"the gate {gate} did not finish on {what} within {FIXTURE_SECONDS:g} seconds",
                                 "make the gate finish quickly; it must not wait or loop"))

    for gate, names in gates.items():
        source, _ = _bundle(root, gate)
        base = root / fixtures_of(gate)
        absent: list[str] = []
        for side, code in (("pass", "gate-unpassable"), ("fail", "gate-permissive")):
            fixture = base / side
            if not fixture.is_dir() or fixture.is_symlink():
                absent.append(side)
                continue
            problem = _unsafe(root, fixture)
            if problem is not None:
                found.append(GateFinding("gate-fixture-unsafe", "error", gate,
                                         f"the fixture {fixtures_of(gate)}/{side} is not safe to run: {problem}",
                                         "use plain files only, and keep the fixture small"))
                continue
            score = _score(source, fixture)
            if score is None:
                timed_out(gate, f"{fixtures_of(gate)}/{side}")
            elif (score != 1) if side == "pass" else (score > 0):
                what = "does not score 1 on its pass fixture" if side == "pass" else "scores above 0 on its fail fixture"
                found.append(GateFinding(code, "error", gate, f"the gate {gate} {what}: score {score:g}",
                                         "fix the gate, or the fixture if it does not show the flow it names"))
        if absent:
            found.append(GateFinding("gate-fixture-missing", "warning", gate,
                                     f"the gate {gate} has no {' or '.join(absent)} fixture under {fixtures_of(gate)}",
                                     f"add {fixtures_of(gate)}/pass and /fail, each with trace/events.jsonl and an output/ tree"))
        if any(name in must_kinds for name in names):
            score = _score(source, None, NULL_TRACE)
            if score is None:
                timed_out(gate, "a null trace")
            elif score > 0:
                found.append(GateFinding("gate-null-trace", "warning", gate,
                                         f"the gate {gate} scores {score:g} on a trace with one empty agent turn, yet its cases have must items",
                                         "check the flow that the must items name, for example with RECEIPTS"))
    return found
