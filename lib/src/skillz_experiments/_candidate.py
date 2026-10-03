from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from skillz_experiments._cases import Case, CodedError, digest, mapping, relative
from skillz_experiments._contract import LOCATION, Contract, resolve

OUTPUT_FILE_LIMIT = 64
OUTPUT_BYTES_LIMIT = 262144
_RUNTIME_OWNED = {"home", "tmp", ".agents", "answer.json", "response-schema.json"}


def candidate_files(value: object) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, content in mapping(value).items():
        if not isinstance(content, str):
            raise ValueError("candidate content must be text")
        files[relative(name)] = content
    return files


def _ignored(root: Path) -> set[str]:
    """Return the git-ignored files under `root`. Any git failure gives an empty set."""
    try:
        run = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
                             capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    if run.returncode:
        return set()
    return {name for name in run.stdout.decode("utf-8", errors="replace").split("\0") if name}


@dataclass(frozen=True)
class Candidate:
    files: dict[str, str]
    editable: tuple[str, ...]
    contract: Contract | None = None
    script: str | None = None

    @classmethod
    def capture(cls, root: Path, editable: list[str], contract: Contract | None = None) -> Candidate:
        if root.is_symlink():
            raise ValueError("candidate must not contain symlinks")
        ignored = _ignored(root)
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("candidate must not contain symlinks")
            if path.is_file():
                name = relative(path.relative_to(root).as_posix())
                if name in ("scripts/skillz-experiment.pyz", LOCATION) or name in ignored:
                    continue
                if path.stat().st_size > 262144:
                    raise ValueError("candidate file exceeds size limit")
                try:
                    files[name] = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    raise CodedError("undecodable-file", f"{name} is not UTF-8") from None
        if sum(len(text) for text in files.values()) > 1_000_000:
            raise ValueError("candidate package exceeds size limit")
        if "SKILL.md" not in files or not set(editable) <= files.keys():
            raise ValueError("candidate needs SKILL.md and existing editable components")
        return cls(files, tuple(editable), contract)

    @property
    def skill(self) -> str:
        return resolve(self.contract).skill

    @property
    def identity(self) -> str:
        return digest(self.files)

    def changed(self, components: dict[str, str]) -> Candidate:
        if set(components) != set(self.editable):
            raise ValueError("proposal components differ from the frozen set")
        if any(len(text) > 262144 for text in components.values()):
            raise ValueError("proposal exceeds component size limit")
        files = self.files | components
        if sum(len(text) for text in files.values()) > 1_000_000:
            raise ValueError("candidate package exceeds size limit")
        return Candidate(files, self.editable, self.contract, self.script)

    def materialize(self, root: Path) -> None:
        for name, content in self.files.items():
            path = root / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(content, encoding="utf-8")


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
    """
    files: dict[str, str] = {}
    total = 0
    for path in sorted(workspace.rglob("*")):
        parts = path.relative_to(workspace).parts
        if parts[0] in _RUNTIME_OWNED or any(part.startswith(".") for part in parts):
            continue
        if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
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