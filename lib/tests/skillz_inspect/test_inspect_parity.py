"""Press attacks on the `skillz_inspect` package through the built `inspect-skill.pyz` (subprocess seam).

The old stdlib inspector, checked in as `fixtures/inspect_skill_v3.py`, is the parity oracle.
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import cast
from zipfile import ZipFile

import pytest

from skillz_experiments._contract import HELPER_FIXTURES

ROOT = Path(__file__).resolve().parents[3]
PYZ = ROOT / "skills/skillz/scripts/inspect-skill.pyz"
OLD_INSPECTOR = Path(__file__).parent / "fixtures/inspect_skill_v3.py"
GOOD, BAD = HELPER_FIXTURES
ERROR = '{"error": "inspect: link escapes package", "exit_code": 3}'
LONG = " ".join(f"word{i}" for i in range(30)) + "."
MID = " ".join(f"mid{i}" for i in range(22)) + "."
FRONT = "---\nname: x\ndescription: y\n---\n"


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


# --- the old stdlib inspector, checked in as a fixture --------------------------------------------------------

@pytest.fixture(scope="module")
def old_script() -> Path:
    return OLD_INSPECTOR


@pytest.fixture(scope="module")
def old_module(old_script: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("old_inspect_skill", old_script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Outcome = tuple[str, object]


def old_outcome(old_script: Path, path: Path) -> Outcome:
    run = subprocess.run(["python3", "-I", str(old_script), str(path)], capture_output=True, encoding="utf-8",
                         errors="replace", check=False)
    document = cast(dict[str, object], json.loads(run.stdout))
    if run.returncode == 0:
        _ = document.pop("schema_version")
        return "ok", document
    assert run.returncode == 2
    return "error", document["error"]


def new_outcome(path: Path) -> Outcome:
    run = run_pyz(str(path))
    if run.returncode == 0:
        document = cast(dict[str, object], json.loads(run.stdout))
        assert document.pop("schema_version") == 4
        assert run.stderr == ""
        return "ok", document
    assert run.returncode == 3 and run.stdout == "" and "Traceback" not in run.stderr, run
    message = str(single_json_line(run.stderr)["error"])
    assert message.startswith("inspect: ")
    return "error", message.removeprefix("inspect: ")


FENCED = f"{FRONT}```\n{LONG}\n```\n{LONG}\n~~~~\n{LONG}\n~~~\n{LONG}\n~~~~\n"
CONTENT: dict[str, str | bytes] = {
    "empty": b"",
    "only-delimiter": "---\n",
    "two-delimiters": "---\n---\n",
    "no-trailing-newline": "---\nname: x\n---",
    "unterminated": "---\nname: x\n",
    "delimiter-with-trailing-space": "---\nname: x\n--- \nbody\n",
    "crlf": FRONT.replace("\n", "\r\n") + f"{LONG}\r\n",
    "bom": b"\xef\xbb\xbf" + FRONT.encode() + b"body\n",
    "not-utf8": b"---\nname: \xff\xfe\n---\nbody\n",
    "nul-byte": b"---\nname: x\n---\nbo\x00dy\n",
    "unicode-line-separators": f"{FRONT}a\u2028b\x0bc\x0cd\x85e {LONG}\n",
    "fenced": FENCED,
    "quote-and-list": f"{FRONT}> - {LONG}\n> > 1. {LONG}\n- {LONG}\n  - {MID}\n\n1) {LONG}\n",
    "curly-and-straight-quotes": f'{FRONT}Say “{LONG}” then "{LONG}" now. {LONG}\n',
    "inline-code": f"{FRONT}Use `{LONG}` and ``{LONG}`` for {LONG}\n",
    "very-long-sentence": f"{FRONT}{'word ' * 5000}end.\n",
    "very-long-line-no-punctuation": f"{FRONT}{'w ' * 100000}",
    "table": f"{FRONT}| {LONG} |\n|---|\n{MID}\n",
    "links": (f"{FRONT}[a](a.md) [b](./b.md#frag) [c](c%20d.md?q=1) [d](https://x.example/{LONG[:5]}) "
              '[e](mailto:a@b.c) [f](#top) [g](<h.md>) [i]() [j](k.md "title") ![img](i.png)\n'),
    "links-with-newline": f"{FRONT}[a](x\ny.md)\n",
    "link-with-nul": f"{FRONT}[a](a%00b.md)\n",
    "link-escape-dotdot": f"{FRONT}[a](../x.md)\n",
    "link-escape-absolute": f"{FRONT}[a](/etc/passwd)\n",
    "link-escape-encoded": f"{FRONT}[a](%2e%2e/x.md)\n",
    "link-long-name": f"{FRONT}[a]({'a' * 6000}.md)\n",
    "link-dir-traversal-inside": f"{FRONT}[a](sub/../a.md)\n",
    "link-unicode": f"{FRONT}[a](caf\u00e9/\u00fc.md) [b](caf%C3%A9.md)\n",
    "keys": "---\nname: x\nname: y\n  nested: z\n9bad: 1\n_ok-key: 1\nAbc_1: 2\n# comment: 3\n---\n",
}


@pytest.mark.parametrize("name", sorted(CONTENT))
def test_pyz_reports_what_the_old_inspector_reported_for_tricky_content(tmp_path: Path, old_script: Path, name: str) -> None:
    path = write(tmp_path / "pkg" / "SKILL.md", CONTENT[name])
    _ = write(tmp_path / "pkg" / "a.md", "a")
    assert new_outcome(path) == old_outcome(old_script, path)


def test_pyz_reports_the_size_limit_exactly_like_the_old_inspector(tmp_path: Path, old_script: Path) -> None:
    header = b"---\nname: x\n---\n"
    for size in (262143, 262144, 262145):
        path = write(tmp_path / f"{size}" / "SKILL.md", header + b"a" * (size - len(header)))
        assert new_outcome(path) == old_outcome(old_script, path), size
    assert new_outcome(tmp_path / "262145/SKILL.md")[0] == "error"
    assert new_outcome(tmp_path / "262144/SKILL.md")[0] == "ok"


def _symlinked_input(root: Path) -> Path:
    _ = write(root / "real.md", FRONT)
    (root / "link.md").symlink_to(root / "real.md")
    return root / "link.md"


def _symlink_to_directory_input(root: Path) -> Path:
    _ = write(root / "real/SKILL.md", FRONT)
    (root / "dirlink").symlink_to(root / "real")
    return root / "dirlink/SKILL.md"


def _directory_input(root: Path) -> Path:
    (root / "dir.md").mkdir(parents=True)
    return root / "dir.md"


def _missing_input(root: Path) -> Path:
    return root / "nowhere.md"


def _link_to_symlinked_file(root: Path) -> Path:
    _ = write(root / "real.md", "x")
    (root / "inner.md").symlink_to(root / "real.md")
    return write(root / "SKILL.md", f"{FRONT}[a](inner.md)\n")


def _link_through_symlinked_directory(root: Path) -> Path:
    _ = write(root / "real/f.md", "x")
    (root / "dir").symlink_to(root / "real")
    return write(root / "SKILL.md", f"{FRONT}[a](dir/f.md)\n")


def _link_to_symlink_escaping_the_package(root: Path) -> Path:
    _ = write(root / "outside.md", "x")
    (root / "pkg").mkdir()
    (root / "pkg/out.md").symlink_to(root / "outside.md")
    return write(root / "pkg/SKILL.md", f"{FRONT}[a](out.md)\n")


def _link_to_a_symlink_loop(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "loop").symlink_to(root / "loop")
    return write(root / "SKILL.md", f"{FRONT}[a](loop)\n")


def _path_with_spaces_and_unicode(root: Path) -> Path:
    _ = write(root / "un dossier/caf\u00e9 \u00fc/guide.md", "x")
    return write(root / "un dossier/caf\u00e9 \u00fc/SKILL \u2603.md", f"{FRONT}[a](guide.md) {LONG}\n")


def _fifo_input(root: Path) -> Path:
    root.mkdir(parents=True)
    os.mkfifo(root / "fifo.md")
    return root / "fifo.md"


def _dev_null(root: Path) -> Path:
    del root
    return Path("/dev/null")


LAYOUTS: dict[str, Callable[[Path], Path]] = {
    "symlink-input": _symlinked_input,
    "input-below-a-symlinked-directory": _symlink_to_directory_input,
    "directory-instead-of-file": _directory_input,
    "missing-file": _missing_input,
    "link-target-is-a-symlink": _link_to_symlinked_file,
    "link-through-a-symlinked-directory": _link_through_symlinked_directory,
    "link-target-symlink-leaves-package": _link_to_symlink_escaping_the_package,
    "symlink-loop-link": _link_to_a_symlink_loop,
    "spaces-and-unicode-path": _path_with_spaces_and_unicode,
    "fifo": _fifo_input,
    "dev-null": _dev_null,
}


# The old inspector calls Path.resolve, which raises RuntimeError on a symlink loop before Python 3.13.
_ORACLE_CRASHES = pytest.mark.skipif(sys.version_info < (3, 13), reason="the old inspector crashes on a loop here")


@pytest.mark.parametrize("name", [pytest.param(name, marks=_ORACLE_CRASHES) if name == "symlink-loop-link" else name
                                  for name in sorted(LAYOUTS)])
def test_pyz_reports_what_the_old_inspector_reported_for_tricky_file_layouts(tmp_path: Path, old_script: Path, name: str) -> None:
    path = LAYOUTS[name](tmp_path / "root")
    assert new_outcome(path) == old_outcome(old_script, path)


def _words(count: int) -> str:
    return " ".join(f"t{index}" for index in range(count))


def _fragments() -> list[str]:
    return ["```", "~~~", "````", "```python", "~~~js", "  ```", "    ```", "> " + _words(26), "> > - " + _words(22),
            "- " + _words(30) + ".", "  - " + _words(21), "1. " + _words(25), "2) " + _words(40) + "!", "| a | " + _words(30),
            "# " + _words(24), "", "", "", _words(10), _words(15) + ".", _words(19) + ". " + _words(19) + ".", _words(28),
            'He said "' + _words(30), _words(30) + '"', "`" + _words(30), _words(30) + "`", "Say “" + _words(26) + "” ok.",
            "[a](a.md) " + _words(21), "[b](../up.md)", "[c](sub/x.md)", "text ```x``` " + _words(22), "-", "- ", "*", "***",
            "\t- " + _words(22), "   > ```", _words(5) + "?!? " + _words(20) + "...", "1.", "10. " + _words(30)]


def test_inspect_matches_the_old_inspector_on_three_hundred_generated_documents(
        tmp_path: Path, old_module: ModuleType) -> None:
    from skillz_inspect import inspect
    generator = random.Random(20260510)
    pool = _fragments()
    old_inspect = cast(Callable[[Path], dict[str, object]], getattr(old_module, "inspect"))
    mismatches: list[str] = []
    for number in range(300):
        body = "\n".join(generator.choice(pool) for _ in range(generator.randint(1, 40)))
        path = write(tmp_path / f"doc{number}" / "SKILL.md", FRONT + body + "\n")
        _ = write(tmp_path / f"doc{number}" / "a.md", "a")
        try:
            expected: object = old_inspect(path) | {"schema_version": 4}
        except ValueError as error:
            expected = str(error)
        try:
            actual: object = inspect(path)
        except ValueError as error:
            actual = str(error)
        if actual != expected:
            mismatches.append(body)
    assert not mismatches, mismatches[:2]


# --- the built archive is not stale -----------------------------------------------------------------------------

def test_the_built_pyz_holds_exactly_the_current_package_and_fromargs_sources() -> None:
    pairs = {"site-packages/skillz_inspect": ROOT / "lib/src/skillz_inspect",
             "site-packages/fromargs": ROOT / "lib/fromargs/src/fromargs"}
    with ZipFile(PYZ) as archive:
        names = set(archive.namelist())
        for prefix, directory in pairs.items():
            sources = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
            packed = {name.removeprefix(prefix + "/"): archive.read(name) for name in names
                      if name.startswith(prefix + "/") and not name.endswith("/") and "__pycache__" not in name}
            assert packed == sources, prefix


def test_the_built_pyz_does_not_carry_the_experiment_engine() -> None:
    with ZipFile(PYZ) as archive:
        assert not [name for name in archive.namelist() if "skillz_experiments" in name]


# --- error envelope exactness ---------------------------------------------------------------------------------

def _unreadable_input(root: Path) -> Path:
    path = write(root / "locked.md", FRONT)
    path.chmod(0)
    return path


FAILURES: dict[str, Callable[[Path], Path]] = {
    "missing": _missing_input,
    "directory": _directory_input,
    "symlink": _symlinked_input,
    "fifo": _fifo_input,
    "dev-null": _dev_null,
    "empty": lambda root: write(root / "e.md", ""),
    "no-frontmatter": lambda root: write(root / "n.md", "body only\n"),
    "unterminated": lambda root: write(root / "u.md", "---\nname: x\n"),
    "not-utf8": lambda root: write(root / "b.md", b"---\n\xff\n---\n"),
    "oversize": lambda root: write(root / "o.md", b"---\n---\n" + b"a" * 262144),
    "link-escape": lambda root: write(root / "l.md", f"{FRONT}[x](../s)\n"),
    "link-nul": lambda root: write(root / "z.md", f"{FRONT}[x](a%00b)\n"),
    "link-symlink": _link_to_symlinked_file,
    "link-through-symlink-dir": _link_through_symlinked_directory,
    "newline-in-file-name": lambda root: write(root / "line\nbreak.md", "body only\n"),
    "quote-and-backslash-in-file-name": lambda root: write(root / 'q"\\.md', "body only\n"),
    "unreadable": _unreadable_input,
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_every_failure_kind_exits_three_with_one_json_line_on_stderr_and_nothing_else(tmp_path: Path, name: str) -> None:
    path = FAILURES[name](tmp_path / "root")
    if name == "unreadable" and os.access(path, os.R_OK):
        pytest.skip("the process can read a mode 0 file")
    run = run_pyz(str(path))
    assert run.returncode == 3, run
    assert run.stdout == ""
    assert "Traceback" not in run.stderr
    document = single_json_line(run.stderr)
    assert set(document) == {"error", "exit_code"} and document["exit_code"] == 3
    assert isinstance(document["error"], str) and document["error"].startswith("inspect: ")


def test_the_escape_fixture_error_is_byte_exact_on_stderr(tmp_path: Path) -> None:
    path = write(tmp_path / "SKILL.md", cast(str, BAD["input"]))
    run = run_pyz(str(path))
    assert (run.returncode, run.stdout) == (3, "")
    assert json.loads(run.stderr) == BAD["error"]
    assert run.stderr.strip() == ERROR


def _assert_inside_the_contract(run: subprocess.CompletedProcess[str]) -> None:
    assert "Traceback" not in run.stderr, run
    if run.returncode == 3:
        lines = [line for line in run.stderr.splitlines() if line.strip()]
        document = cast(dict[str, object], json.loads(lines[-1]))
        assert run.stdout == "" and set(document) == {"error", "exit_code"}, run
    else:
        assert run.returncode == 0, run
        assert cast(dict[str, object], json.loads(run.stdout))["schema_version"] == 4


def test_a_link_to_a_symlink_loop_stays_inside_the_error_contract(tmp_path: Path) -> None:
    path = _link_to_a_symlink_loop(tmp_path / "root")
    _assert_inside_the_contract(run_pyz(str(path)))
    assert new_outcome(path) == ("error", "package must not contain symlinks")


def test_a_very_long_link_target_stays_inside_the_error_contract(tmp_path: Path) -> None:
    _assert_inside_the_contract(run_pyz(str(write(tmp_path / "SKILL.md", f"{FRONT}[a]({'a' * 70000}.md)\n"))))


@pytest.mark.parametrize("args", [[], ["a", "b"], ["--foo"], ["x.md", "--foo"], ["--path"], ["--"]])
def test_usage_errors_exit_two_with_an_empty_stdout_and_one_json_line(args: list[str]) -> None:
    run = run_pyz(*args)
    assert run.returncode == 2 and run.stdout == "", run
    document = single_json_line(run.stderr)
    assert set(document) == {"error", "exit_code"} and document["exit_code"] == 2


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_goes_to_stdout_with_exit_zero_and_a_silent_stderr(flag: str) -> None:
    run = run_pyz(flag)
    assert run.returncode == 0 and run.stderr == ""
    assert "inspect-skill" in run.stdout and "PATH" in run.stdout


def test_version_exits_zero_with_a_silent_stderr() -> None:
    run = run_pyz("--version")
    assert run.returncode == 0 and run.stderr == "" and run.stdout.strip()


def test_a_file_named_like_an_option_works_through_the_end_of_options_marker(tmp_path: Path) -> None:
    _ = write(tmp_path / "--odd.md", FRONT)
    run = subprocess.run([sys.executable, "-I", str(PYZ), "--", "--odd.md"], cwd=tmp_path, capture_output=True,
                         text=True, check=False)
    assert run.returncode == 0, run
    assert json.loads(run.stdout)["frontmatter_keys"] == ["description", "name"]


def test_a_relative_path_and_the_path_option_report_the_same_facts(tmp_path: Path) -> None:
    _ = write(tmp_path / "SKILL.md", FRONT + LONG + "\n")
    relative = subprocess.run([sys.executable, "-I", str(PYZ), "SKILL.md"], cwd=tmp_path, capture_output=True,
                              text=True, check=False)
    named = run_pyz("--path", str(tmp_path / "SKILL.md"))
    assert relative.returncode == named.returncode == 0
    assert json.loads(relative.stdout) == json.loads(named.stdout)


def test_the_success_document_is_one_json_value_with_the_v4_keys(tmp_path: Path) -> None:
    run = run_pyz(str(write(tmp_path / "SKILL.md", cast(str, GOOD["input"]))))
    assert run.returncode == 0 and run.stderr == ""
    assert json.loads(run.stdout) == GOOD["output"]


