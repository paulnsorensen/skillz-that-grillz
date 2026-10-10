from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path

from skillz_experiments._cases import Case, CodedError, digest, mapping, relative
from skillz_experiments._contract import LOCATION, Contract, resolve
from skillz_experiments._records import write_bytes

OUTPUT_FILE_LIMIT = 64
OUTPUT_BYTES_LIMIT = 262144
PACKAGE_LIMIT = 1_000_000
TEXT_FILE_LIMIT = 262144
# Frozen bytes never count against PACKAGE_LIMIT. They have their own limits.
FROZEN_FILE_LIMIT = 16 * 1024 * 1024
FROZEN_TOTAL_LIMIT = 64 * 1024 * 1024
# A candidate file under this prefix is wedge source from the repository, not a skill file.
# The rest of the name is the path relative to the repository root. `materialize` never writes it.
WEDGE_PREFIX = "@wedge/"
_RUNTIME_OWNED = {"home", "tmp", ".agents", "answer.json", "response-schema.json"}


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MiB"


def candidate_files(value: object) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, content in mapping(value).items():
        if not isinstance(content, str):
            raise ValueError("candidate content must be text")
        files[relative(name)] = content
    return files


def ignored_files(root: Path) -> set[str]:
    """Return the git-ignored files under `root`. Any git failure gives an empty set.

    A root that git ignores itself gives an empty set too. Git would list every file below it.
    """
    try:
        inside = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", str(root.resolve())],
                                capture_output=True, check=False, timeout=30)
        if inside.returncode == 0:
            return set()
        run = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
                             capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if run.returncode:
        return set()
    return {name for name in run.stdout.decode("utf-8", errors="replace").split("\0") if name}


def _metadata(posix: str) -> bool:
    """Return True for VCS and host metadata and for bytecode caches. Capture skips them."""
    parts = posix.split("/")
    return (bool({".git", ".github", "__pycache__"} & set(parts))
            or parts[-1] in (".gitignore", ".gitattributes", ".gitkeep", ".gitmodules", ".DS_Store") or parts[-1].endswith(".pyc"))


def as_text(data: bytes) -> str | None:
    """Return `data` as text, or None when it is binary, not UTF-8, or over the text limit."""
    if len(data) > TEXT_FILE_LIMIT or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


@dataclass(frozen=True)
class Candidate:
    """The skill files of one candidate.

    `files` holds text, including wedge sources under `WEDGE_PREFIX`. `frozen` holds bytes that no
    proposal edits: binary files, non-UTF-8 files, text over `TEXT_FILE_LIMIT`, and built `.pyz` files.
    """

    files: dict[str, str]
    editable: tuple[str, ...]
    contract: Contract | None = None
    script: str | None = None
    frozen: dict[str, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if sum(len(text) for text in self.files.values()) > PACKAGE_LIMIT:
            raise ValueError("candidate package exceeds size limit")
        for name, data in self.frozen.items():
            if len(data) > FROZEN_FILE_LIMIT:
                raise CodedError("frozen-file-too-large",
                                 f"{name} is {_megabytes(len(data))}; the limit is {_megabytes(FROZEN_FILE_LIMIT)} per file")
        if sum(len(data) for data in self.frozen.values()) > FROZEN_TOTAL_LIMIT:
            largest = max(self.frozen, key=lambda name: len(self.frozen[name]))
            raise CodedError("frozen-file-too-large", f"the frozen files exceed {_megabytes(FROZEN_TOTAL_LIMIT)} in total; "
                             + f"the largest is {largest}")

    @classmethod
    def capture(cls, root: Path, editable: list[str], contract: Contract | None = None,
                exclude: tuple[str, ...] = ()) -> Candidate:
        """Read the skill files under `root`. Skip git-ignored files, VCS metadata, bytecode caches, `evals/`, and `exclude`.

        A file that is binary, is not UTF-8, or exceeds `TEXT_FILE_LIMIT` becomes frozen bytes.
        """
        if root.is_symlink():
            raise ValueError("candidate must not contain symlinks")
        ignored = ignored_files(root)
        files: dict[str, str] = {}
        frozen: dict[str, bytes] = {}
        for path in sorted(root.rglob("*")):
            posix = path.relative_to(root).as_posix()
            if posix in ignored or _metadata(posix) or posix.startswith("evals/"):
                continue
            if path.is_symlink():
                raise ValueError("candidate must not contain symlinks")
            if path.is_file():
                if any(part.startswith(".") for part in posix.split("/")):
                    raise CodedError("hidden-file", f"{posix} is a hidden file; remove it")
                name = relative(posix)
                if name.startswith(WEDGE_PREFIX):
                    raise CodedError("reserved-name", f"{name} starts with {WEDGE_PREFIX}, which names wedge sources; rename the file")
                if name in (LOCATION, *exclude):
                    continue
                if path.stat().st_size > FROZEN_FILE_LIMIT:
                    raise CodedError("frozen-file-too-large", f"{name} is {_megabytes(path.stat().st_size)}; "
                                     + f"the limit is {_megabytes(FROZEN_FILE_LIMIT)} per file")
                data = path.read_bytes()
                text = as_text(data)
                if text is None:
                    frozen[name] = data
                else:
                    files[name] = text
        if "SKILL.md" not in files or not set(editable) <= files.keys():
            raise ValueError("candidate needs SKILL.md and existing editable components")
        return cls(files, tuple(editable), contract, frozen=frozen)

    @property
    def skill(self) -> str:
        return resolve(self.contract).skill

    @cached_property
    def frozen_digests(self) -> dict[str, str]:
        return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(self.frozen.items())}

    @property
    def identity(self) -> str:
        if not self.frozen:
            return digest(self.files)
        return digest({"files": self.files, "frozen": self.frozen_digests})

    def changed(self, components: dict[str, str]) -> Candidate:
        if set(components) != set(self.editable):
            raise ValueError("proposal components differ from the frozen set")
        if any(len(text) > TEXT_FILE_LIMIT for text in components.values()):
            raise ValueError("proposal exceeds component size limit")
        child = Candidate(self.files | components, self.editable, self.contract, self.script, self.frozen)
        child.__dict__["frozen_digests"] = self.frozen_digests
        return child

    def with_frozen(self, built: dict[str, bytes]) -> Candidate:
        """Return this candidate with `built` replacing the frozen bytes of the same names.

        The digests of the other frozen files carry over, so they are not hashed again.
        """
        child = replace(self, frozen=self.frozen | built)
        fresh = {name: hashlib.sha256(data).hexdigest() for name, data in built.items()}
        child.__dict__["frozen_digests"] = dict(sorted((self.frozen_digests | fresh).items()))
        return child

    def materialize(self, root: Path) -> None:
        for name, content in self.files.items():
            if name.startswith(WEDGE_PREFIX):
                continue
            path = root / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(content, encoding="utf-8")
        for name, data in self.frozen.items():
            if name.startswith(WEDGE_PREFIX):
                continue
            path = root / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_bytes(data)
            if name.endswith(".pyz"):
                path.chmod(0o755)


def store_frozen(candidate: Candidate, directory: Path) -> dict[str, str]:
    """Write each frozen file of `candidate` to `directory`, named by its digest. Return the digest of each name."""
    directory.mkdir(exist_ok=True)
    for name, data in candidate.frozen.items():
        _ = write_bytes(directory / candidate.frozen_digests[name], data)
    return dict(candidate.frozen_digests)


def load_frozen(digests: Mapping[str, object], directory: Path) -> dict[str, bytes]:
    """Read the frozen files that `store_frozen` wrote. A missing or changed file is tampering."""
    frozen: dict[str, bytes] = {}
    for name, digest_text in digests.items():
        blob = directory / str(digest_text)
        if not isinstance(digest_text, str) or not re.fullmatch(r"[0-9a-f]{64}", digest_text) or not blob.is_file():
            raise CodedError("run-record-tampered", f"the frozen file {name} is missing from the run; create a new run")
        data = blob.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest_text:
            raise CodedError("run-record-tampered", f"the frozen file {name} differs from the run record; create a new run")
        try:
            frozen[relative(name)] = data
        except ValueError:
            raise CodedError("run-record-tampered", f"the frozen file name {name} is not a skill path; create a new run") from None
    return frozen


def make_workspace(workspace: Path) -> Path:
    for directory in ("home", "tmp", ".agents/skills"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    return workspace


def stage_task(workspace: Path, candidate: Candidate | None, case: Case | None) -> None:
    if candidate is not None:
        root = workspace / ".agents/skills" / candidate.skill
        candidate.materialize(root)
        _ = (root / "EXPERIMENT_MARKER").write_text(candidate.identity)
    if case is not None:
        for name, content in case.files.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(content)


def snapshot_outputs(workspace: Path) -> dict[str, str]:
    """Return the text files that a task leaves in the workspace, bounded in count and size.

    Symlinks, runtime-owned paths, binary files, and files over the limits are skipped.
    A symlink above the workspace is allowed. Only the parts below the workspace count.
    """
    files: dict[str, str] = {}
    total = 0
    for path in sorted(workspace.rglob("*")):
        parts = path.relative_to(workspace).parts
        if parts[0] in _RUNTIME_OWNED or any(part.startswith(".") for part in parts):
            continue
        if path.is_symlink() or not path.is_file() or any(
                (workspace.joinpath(*parts[:depth])).is_symlink() for depth in range(1, len(parts))):
            continue
        if len(files) >= OUTPUT_FILE_LIMIT or path.stat().st_size > OUTPUT_BYTES_LIMIT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        total += len(text)
        if total > 1_000_000:
            break
        files["/".join(parts)] = text
    return files